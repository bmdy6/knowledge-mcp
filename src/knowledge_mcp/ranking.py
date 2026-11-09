"""Rank Markdown search hits by relevance instead of by file path.

Rank source articles ahead of navigation pages using lexical signals:

- BM25 over Chinese bigrams plus English technical terms, so term frequency and
  document length both matter;
- a title boost, because a document whose title matches the query is usually the
  one being looked for;
- a heavier weight for English technical terms (``asyncio``, ``pydantic``),
  which are precise signals a Chinese query would otherwise drown out;
- lighter weight for index-like documents, so navigation pages do not outrank
  the content they point at.

Semantic (vector) scoring is deliberately not ported: this vault has no
embedding service, and ``docs/ARCHITECTURE.md`` rules out a vector store for
this stage.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

# Chinese has no spaces, so a word-level tokenizer would produce one term per
# sentence. Overlapping bigrams are the standard cheap alternative: "依赖注入"
# becomes 依赖/赖注/注入 plus the full run, which matches sub-phrases reliably.
_CJK = re.compile(r"[\u4e00-\u9fff]+")
_TECHNICAL = re.compile(r"[a-z0-9][a-z0-9_.-]*")

# BM25 scoring constants, preserved from the original implementation.
_BM25_K1 = 1.2
_BM25_B = 0.65
_BM25_TITLE_WEIGHT = 2.2

# English terms at least this long are treated as technical identifiers.
_MIN_TECHNICAL_TERM_LENGTH = 4

# Documents whose path looks like navigation are down-weighted relative to
# content, without excluding them: an index page is sometimes the right answer.
# The vault keeps one topic map per scope and one learning-point index, so the
# names below are the only navigation pages a search should rank lower.
_LOW_SIGNAL_PATH_PARTS = ("README.md", "学习点索引")
_LOW_SIGNAL_WEIGHT = 0.35

# A scope's top-level documents are the maintained books; auxiliary folders keep
# their sources. A source that repeats a book's topic should not outrank the book.
_TOP_LEVEL_WEIGHT = 1.6

# Third-party book copies and raw snapshots are long and match almost anything,
# so they stay findable but rank below notes written for this vault.
# Auxiliary files sit in batch folders (辅助材料/批次NN-…/), so match the
# book-copy filename prefix after any directory, not right after 辅助材料/.
_REFERENCE_PATH_PARTS = ("/书-", "原文快照", "旧库处理登记")
_REFERENCE_WEIGHT = 0.6

# Spoken questions wrap the topic in words that say nothing about it. Left in,
# "为什么要用消息队列" never matches literally and its bigrams hit every document.
_QUESTION_WORDS = re.compile(
    "为什么要用|为什么要|为什么|怎么办|怎么做|怎么样|怎么|怎样|如何|是什么|什么|的区别|区别"
)

# With no literal match, a document must hold this share of the query's bigrams.
_MIN_BIGRAM_OVERLAP = 0.6


def _terms(text: str) -> list[str]:
    """Split text into Chinese bigrams plus lowercase English/number words."""
    normalized = text.casefold()
    chinese = "".join(_CJK.findall(normalized))
    bigrams = [chinese[i : i + 2] for i in range(len(chinese) - 1)]
    # The full Chinese run as one token, plus each CJK character, so single
    # characters and long unbroken names both match.
    runs = [run for run in _CJK.findall(normalized) if len(run) > 2]
    return bigrams + runs + _TECHNICAL.findall(normalized)


def topic_of(query: str) -> str:
    """Drop question words so only the topic is matched; keep the query if nothing is left."""
    stripped = " ".join(_QUESTION_WORDS.sub(" ", query).split())
    return stripped or query.strip()


def query_bigrams(query: str) -> tuple[str, ...]:
    """Chinese bigrams of each query word, used when the wording differs from the text."""
    bigrams: list[str] = []
    for run in _CJK.findall(query.casefold()):
        bigrams.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tuple(dict.fromkeys(bigrams))


def overlaps_enough(lowered_text: str, bigrams: tuple[str, ...]) -> bool:
    """True when the text holds enough of the query's bigrams to count as a match."""
    if not bigrams:
        return False
    found = sum(1 for bigram in bigrams if bigram in lowered_text)
    return found / len(bigrams) >= _MIN_BIGRAM_OVERLAP


def _is_technical_term(term: str) -> bool:
    """True for English identifiers (field names, library names, commands)."""
    return (
        len(term) >= _MIN_TECHNICAL_TERM_LENGTH
        and term.isascii()
        and any(character.isalpha() for character in term)
    )


@dataclass(frozen=True)
class RankedDocument:
    """One document that matched, already reduced to its best line."""

    path: str
    score: float
    # 1-based line numbers of every matching line, in document order.
    match_lines: tuple[int, ...]
    title_match: bool


class Ranker:
    """Corpus-level BM25 index over searchable lines of a scope."""

    def __init__(
        self,
        documents: list[tuple[str, str, list[tuple[int, str]]]],
        *,
        technical_term_weight: float = 2.0,
        title_weight: float = 2.0,
    ) -> None:
        """Build the index from ``(path, title, searchable_lines)`` tuples."""
        self._paths: list[str] = []
        self._titles: list[str] = []
        self._lines: list[list[tuple[int, str]]] = []
        # Per-document term statistics; one "document" is one Markdown file.
        self._term_frequencies: list[Counter[str]] = []
        self._title_terms: list[set[str]] = []
        self._technical_term_weight = technical_term_weight
        self._title_weight = title_weight

        for path, title, lines in documents:
            terms: list[str] = []
            for _number, text in lines:
                terms.extend(_terms(text))
            self._paths.append(path)
            self._titles.append(title)
            self._lines.append(lines)
            self._term_frequencies.append(Counter(terms))
            self._title_terms.append(set(_terms(title)))

        self._document_frequency: Counter[str] = Counter()
        for frequencies in self._term_frequencies:
            self._document_frequency.update(frequencies.keys())
        lengths = [sum(frequencies.values()) for frequencies in self._term_frequencies]
        self._average_length = (sum(lengths) / len(lengths)) if lengths else 1.0
        self._inverted: dict[str, list[int]] = {}
        for index, frequencies in enumerate(self._term_frequencies):
            for term in frequencies:
                self._inverted.setdefault(term, []).append(index)

    def rank(self, query: str, *, limit: int) -> list[RankedDocument]:
        """Return documents ordered by relevance, best first."""
        query_terms = Counter(_terms(query))
        if not query_terms or not self._paths:
            return []

        weights = {
            term: (
                self._technical_term_weight
                if _is_technical_term(term)
                else 1.0
            )
            for term in query_terms
        }
        candidates: set[int] = set()
        for term in query_terms:
            candidates.update(self._inverted.get(term, ()))

        query_set = set(query_terms)
        normalized_query = _normalize_title(query)
        scored: list[RankedDocument] = []
        total = len(self._paths)
        for index in candidates:
            frequencies = self._term_frequencies[index]
            length = sum(frequencies.values())
            score = 0.0
            for term, occurrences in query_terms.items():
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                document_frequency = self._document_frequency[term]
                idf = math.log(
                    1
                    + (total - document_frequency + 0.5)
                    / (document_frequency + 0.5)
                )
                score += (
                    weights[term]
                    * idf
                    * frequency
                    * (occurrences + _BM25_TITLE_WEIGHT)
                    / (
                        frequency
                        + _BM25_K1
                        * (1 - _BM25_B + _BM25_B * length / max(self._average_length, 1.0))
                    )
                )

            title_bonus = query_set & self._title_terms[index]
            if title_bonus:
                overlap = len(title_bonus) / len(query_set)
                score *= 1 + self._title_weight * overlap
            title_match = _normalize_title(self._titles[index]) == normalized_query
            if title_match:
                score *= 1 + self._title_weight
            if any(part in self._paths[index] for part in _LOW_SIGNAL_PATH_PARTS):
                score *= _LOW_SIGNAL_WEIGHT
            elif any(part in self._paths[index] for part in _REFERENCE_PATH_PARTS):
                score *= _REFERENCE_WEIGHT
            elif self._paths[index].count("/") == 1:
                score *= _TOP_LEVEL_WEIGHT

            match_lines = tuple(
                number
                for number, text in self._lines[index]
                if any(term in text.casefold() for term in _literal_needles(query))
            )
            scored.append(
                RankedDocument(
                    path=self._paths[index],
                    score=score,
                    match_lines=match_lines,
                    title_match=title_match,
                )
            )

        # Highest score first; path as a stable tie-breaker so equal scores keep
        # a deterministic order across runs.
        scored.sort(key=lambda item: (-item.score, item.path))
        return scored[:limit]


def _literal_needles(query: str) -> tuple[str, ...]:
    """Substrings used to find the exact lines to report.

    BM25 works on bigrams, which make poor snippets. The line numbers should
    point at the real query text, so this returns the literal query and, for
    multi-word queries, each whitespace-separated part.
    """
    stripped = query.strip()
    if not stripped:
        return ()
    parts = [part for part in stripped.split() if part]
    return tuple(dict.fromkeys([stripped, *parts]))


def _normalize_title(title: str) -> str:
    """Strip markup and spaces so titles compare equal across spellings."""
    cleaned = re.sub(r"\s+", "", title)
    cleaned = re.sub(r"^(?:[Tt][:：]|【[^】]*】)", "", cleaned)
    cleaned = cleaned.removeprefix("#").strip()
    return cleaned.casefold()
