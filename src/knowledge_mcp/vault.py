from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import date
from functools import wraps
import json
from pathlib import Path
import os
import re
import tempfile
from typing import Literal, Sequence
import warnings

import yaml

from .locking import vault_write_lock, WriteLockTimeout
from .ranking import overlaps_enough, query_bigrams, Ranker, topic_of

Scope = Literal["personal", "company"]
SCOPE_ROOTS: dict[str, str] = {
    "personal": "personal",
    "company": "company",
}
DOCUMENT_TYPES = frozenset(
    {"article", "reference", "decision", "guide", "research", "task"}
)
# A company task note is only useful later if it always answers the same
# questions; enforcing the headings here keeps every writer (MCP, scripts, a
# hand edit caught by validate) from drifting into free-form dumps.
TASK_SECTIONS = ("任务描述", "解决思考", "解决方法", "结果", "链接")
# Merging notes may drop duplicate frontmatter and repeated paragraphs, but a
# merged body far shorter than its sources means content was summarised away,
# which risks dropping source details.
MERGE_MIN_RATIO = 0.7
DOCUMENT_STATUSES = frozenset({"active", "review", "deprecated"})
# Every new document starts unconfirmed. ``active`` is only reachable through a
# version-checked change to an existing document, so no creation path —
# import or direct MCP call — can skip human review.
NEW_DOCUMENT_STATUS = "review"


class VaultError(Exception):
    """Base error for invalid vault operations."""


class InvalidScopeError(VaultError):
    pass


class InvalidPathError(VaultError):
    pass


class DocumentNotFoundError(VaultError):
    pass


class ArchiveAccessError(VaultError):
    pass


class VersionConflictError(VaultError):
    pass


class AmbiguousChangeError(VaultError):
    pass


class DocumentExistsError(VaultError):
    pass


class DuplicateSourceError(VaultError):
    """Raised when a new document reuses a source an existing document claims."""


class InvalidMetadataError(VaultError):
    pass


class VaultBusyError(VaultError):
    pass


def _serialized_write(method):
    """Keep all file checks and mutations in the same cross-process lock."""
    @wraps(method)
    def locked(self, *args, **kwargs):
        try:
            with vault_write_lock(self.root):
                return method(self, *args, **kwargs)
        except WriteLockTimeout as exc:
            raise VaultBusyError(str(exc)) from None

    return locked


def _normalized_date(value: str, field: str) -> str:
    """Validate a caller-supplied date so migrated documents keep their history."""
    if not isinstance(value, str):
        raise InvalidMetadataError(f"{field} must be a YYYY-MM-DD string")
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise InvalidMetadataError(f"{field} must be a YYYY-MM-DD date") from exc


def _yaml_scalar(value: str) -> str:
    """Render one frontmatter value so it survives a YAML round trip.

    Titles and tags come from arbitrary text: ``story: e20s05`` and
    ``#learning-notes`` both break a plain ``key: value`` line. Values that
    cannot be read back unchanged are quoted; everything else stays plain so
    the frontmatter remains readable by hand.
    """
    if value != value.strip() or not value:
        return json.dumps(value, ensure_ascii=False)
    try:
        parsed = yaml.safe_load(f"key: {value}\n")
    except yaml.YAMLError:
        return json.dumps(value, ensure_ascii=False)
    if parsed == {"key": value}:
        return value
    return json.dumps(value, ensure_ascii=False)


def split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Split YAML frontmatter and Markdown body without copying the document."""
    if not text.startswith("---\n"):
        return {}, text
    closing = text.find("\n---\n", 4)
    if closing < 0:
        return {}, text
    raw = yaml.safe_load(text[4:closing]) or {}
    if not isinstance(raw, dict):
        return {}, text
    return raw, text[closing + 5 :]


_FENCE_PATTERN = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})")
_CLOSING_FENCE_PATTERN = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})[ \t]*$")
_TOP_LEVEL_HEADING_PATTERN = re.compile(r"^[ ]{0,3}#(?:[ \t]+.*)?$")
_SUB_HEADING_PATTERN = re.compile(r"^[ ]{0,3}#{2,}")


def _missing_task_sections(content: str) -> list[str]:
    """Return required task headings that do not appear as ``## 标题`` lines."""
    present = {
        line.strip()[3:].strip()
        for line in content.splitlines()
        if line.strip().startswith("## ")
    }
    return [section for section in TASK_SECTIONS if section not in present]


def _has_top_level_heading(content: str) -> bool:
    """Return True if content contains an ATX top-level heading (# ...) outside code blocks."""
    in_code_block = False
    fence_char = None
    fence_len = 0
    for line in content.splitlines():
        if in_code_block:
            match_closing = _CLOSING_FENCE_PATTERN.match(line)
            if match_closing:
                fence = match_closing.group(1)
                if fence[0] == fence_char and len(fence) >= fence_len:
                    in_code_block = False
                    fence_char = None
                    fence_len = 0
            continue

        match_fence = _FENCE_PATTERN.match(line)
        if match_fence:
            fence = match_fence.group(1)
            in_code_block = True
            fence_char = fence[0]
            fence_len = len(fence)
            continue

        if _TOP_LEVEL_HEADING_PATTERN.match(line) and not _SUB_HEADING_PATTERN.match(line):
            return True
    return False


@dataclass(frozen=True)
class SearchResult:
    scope: Scope
    path: str
    line_start: int
    line_end: int
    title: str
    snippet: str


_SECTION_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_DATE_SECTION_HEADING = re.compile(r"^##\s+(\d{4})-(\d{2})-(\d{2})\s*$")


def _date_section_ordinal(lines: list[str], hit: int) -> int:
    """Return the enclosing ``## YYYY-MM-DD`` date as an ordinal, if any."""
    for index in range(min(hit, len(lines)) - 1, -1, -1):
        match = _DATE_SECTION_HEADING.match(lines[index])
        if match is not None:
            try:
                return date(*(int(part) for part in match.groups())).toordinal()
            except ValueError:
                return 0
        heading = _SECTION_HEADING.match(lines[index])
        # Crossing another H2 means this hit is not inside an earlier date H2.
        if heading is not None and len(heading.group(1)) <= 2:
            return 0
    return 0


def _section_range(text: str, hit: int, body_start: int) -> tuple[int, int]:
    """Locate the smallest useful Markdown section/paragraph around a hit."""
    lines = text.splitlines()
    if hit < body_start or hit < 1 or hit > len(lines):
        return hit, hit

    # Prefer the nearest subheading (H2-H6). H1 is the document title and would
    # make a section hit expand to the whole article, defeating section search.
    heading_index: int | None = None
    heading_level = 0
    for index in range(hit - 1, body_start - 2, -1):
        match = _SECTION_HEADING.match(lines[index])
        if match is None:
            continue
        level = len(match.group(1))
        if level >= 2:
            heading_index = index
            heading_level = level
            break
        if level == 1:
            break

    if heading_index is not None:
        end = len(lines) - 1
        for index in range(heading_index + 1, len(lines)):
            match = _SECTION_HEADING.match(lines[index])
            if match is not None and len(match.group(1)) <= heading_level:
                end = index - 1
                break
        while end > heading_index and not lines[end].strip():
            end -= 1
        return heading_index + 1, end + 1

    # No subheading: return the surrounding paragraph rather than the whole file.
    start = hit - 1
    while start > body_start - 1 and lines[start - 1].strip():
        if _SECTION_HEADING.match(lines[start - 1]):
            break
        start -= 1
    end = hit - 1
    while end + 1 < len(lines) and lines[end + 1].strip():
        if _SECTION_HEADING.match(lines[end + 1]):
            break
        end += 1
    return start + 1, end + 1


def _section_snippet(text: str, start: int, end: int, hit: int, *, limit: int = 1000) -> str:
    """Show the section heading plus text around the actual hit, capped for UI use."""
    lines = text.splitlines()
    section = lines[start - 1 : end]
    rendered = "\n".join(section).strip()
    if len(rendered) <= limit:
        return rendered

    pieces: list[str] = []
    if section and _SECTION_HEADING.match(section[0]):
        pieces.append(section[0])
    left = max(start, hit - 3)
    right = min(end, hit + 4)
    window = "\n".join(lines[left - 1 : right]).strip()
    if window and (not pieces or window != pieces[0]):
        pieces.append(window)
    snippet = "\n...\n".join(pieces)
    return snippet[:limit]


@dataclass(frozen=True)
class ValidationFinding:
    scope: str
    path: str
    severity: Literal["error", "warning"]
    code: str
    message: str


class Vault:
    """Filesystem-backed vault with explicit scope and path checks."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def _document_path(
        self, scope: str, relative_path: str, *, include_archive: bool = False
    ) -> tuple[Scope, Path, str]:
        scope, target, normalized, archive_path = self._validated_target(
            scope, relative_path, include_archive=include_archive
        )
        if not target.is_file():
            raise DocumentNotFoundError(f"Document not found: {normalized}")
        if target.is_symlink():
            raise InvalidPathError("Symbolic links are not valid document paths")
        metadata, _ = self._frontmatter(target.read_text(encoding="utf-8"))
        declared_scope = metadata.get("scope")
        if declared_scope is not None and declared_scope != scope:
            raise InvalidPathError(
                f"Frontmatter scope {declared_scope!r} does not match {scope!r}"
            )
        return scope, target, normalized

    def _validated_target(
        self, scope: str, relative_path: str, *, include_archive: bool = False
    ) -> tuple[Scope, Path, str, bool]:
        if scope not in SCOPE_ROOTS:
            raise InvalidScopeError(f"Unsupported scope: {scope}")

        candidate = Path(relative_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise InvalidPathError("Path must be a safe repository-relative path")

        scope_root = SCOPE_ROOTS[scope]
        normalized = candidate.as_posix()
        # Callers already pass ``scope``, so models often send the path relative
        # to it (``40-knowledge/x.md``); accept that form, but never reinterpret
        # a path that names another scope or the archive.
        first = candidate.parts[0] if candidate.parts else ""
        if first not in {*SCOPE_ROOTS.values(), "archive"}:
            normalized = f"{scope_root}/{normalized}"
            candidate = Path(normalized)
        archive_path = normalized == "archive" or normalized.startswith("archive/")
        if archive_path and not include_archive:
            raise ArchiveAccessError("Archive access requires include_archive=true")

        expected_prefix = f"{scope_root}/"
        if not normalized.startswith(expected_prefix) and not archive_path:
            raise InvalidPathError(f"Path must be under {scope_root}/")
        if not normalized.endswith(".md"):
            raise InvalidPathError("Only Markdown documents are supported")

        target = (self.root / candidate).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise InvalidPathError("Path resolves outside the vault") from exc
        if not archive_path:
            scope_root_path = (self.root / scope_root).resolve()
            try:
                target.relative_to(scope_root_path)
            except ValueError as exc:
                raise InvalidPathError(f"Path must be under {scope_root}/") from exc
        return scope, target, normalized, archive_path

    def validate_new_document_path(self, scope: str, relative_path: str) -> str:
        """Validate a new document target without writing to the vault."""
        _, _, normalized = self._new_document_target(scope, relative_path)
        return normalized

    def _new_document_target(
        self, scope: str, relative_path: str
    ) -> tuple[Scope, Path, str]:
        """Return a validated, unused path for a new document."""
        checked_scope, target, normalized, archive_path = self._validated_target(
            scope, relative_path, include_archive=True
        )
        if archive_path or checked_scope not in SCOPE_ROOTS:
            raise InvalidPathError("New documents cannot be created in archive/")
        if target.exists():
            raise DocumentExistsError(f"Document already exists: {normalized}")
        return checked_scope, target, normalized

    def _reject_duplicate_sources(
        self,
        scope: Scope,
        target: str,
        sources: Sequence[str],
        ignore: frozenset[str] = frozenset(),
    ) -> None:
        """Refuse to create a document whose source is already claimed.

        Path-only uniqueness is not enough: importing the same legacy file twice
        under two different names produces two documents for one source, which
        is exactly the duplication this repository exists to avoid. A source may
        only be claimed once per scope; the first document keeps it.
        """
        if not sources:
            return
        claimed: dict[str, str] = {}
        for path in self.list_documents(scope):
            if path == target or path in ignore:
                continue
            try:
                metadata, _ = self._frontmatter(
                    (self.root / path).read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError, yaml.YAMLError):
                continue
            existing = metadata.get("sources")
            if not isinstance(existing, list):
                continue
            for item in existing:
                if isinstance(item, str) and item.strip():
                    claimed.setdefault(item.strip(), path)
        for source in sources:
            owner = claimed.get(source)
            if owner is not None:
                raise DuplicateSourceError(
                    f"source already claimed by {owner}: {source}"
                )

    @staticmethod
    def _frontmatter(text: str) -> tuple[dict[str, object], str]:
        return split_frontmatter(text)

    @staticmethod
    def _body_start_line(text: str) -> int:
        if not text.startswith("---\n"):
            return 1
        closing = text.find("\n---\n", 4)
        if closing < 0:
            return 1
        return text[: closing + 5].count("\n") + 1

    @classmethod
    def _searchable_lines(cls, text: str) -> list[tuple[int, str]]:
        """Return body lines plus only the frontmatter fields used for search."""
        if not text.startswith("---\n"):
            return list(enumerate(text.splitlines(), start=1))

        lines = text.splitlines()
        closing_line = next(
            (
                number
                for number, line in enumerate(lines[1:], start=2)
                if line == "---"
            ),
            None,
        )
        if closing_line is None:
            return list(enumerate(text.splitlines(), start=1))

        searchable: list[tuple[int, str]] = []
        in_tags = False
        for number, line in enumerate(lines[1 : closing_line - 1], start=2):
            stripped = line.strip()
            if stripped.startswith(("title:", "series:")):
                searchable.append((number, line))
                in_tags = False
            elif stripped.startswith("tags:"):
                # Inline lists (`tags: [a, b]`) keep the value on this line; a bare
                # `tags:` marker is followed by block list items on later lines.
                searchable.append((number, line))
                in_tags = not stripped[len("tags:") :].strip()
            elif in_tags and line.startswith(("  - ", "- ")):
                searchable.append((number, line))
            elif stripped and not line.startswith((" ", "\t")):
                in_tags = False

        searchable.extend(
            (number, line)
            for number, line in enumerate(
                lines[closing_line:], start=closing_line + 1
            )
        )
        return searchable

    @staticmethod
    def _title(metadata: dict[str, object], body: str, fallback: str) -> str:
        value = metadata.get("title")
        if isinstance(value, str) and value.strip():
            return value.strip()
        for line in body.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
        return fallback

    def _search_roots(self, scope: str, include_archive: bool) -> list[Path]:
        if scope not in SCOPE_ROOTS:
            raise InvalidScopeError(f"Unsupported scope: {scope}")
        roots = [self.root / SCOPE_ROOTS[scope]]
        if include_archive:
            roots.append(self.root / "archive")
        return roots

    def _search_documents(
        self,
        scope: str,
        roots: list[Path],
        diagnostics: list[dict[str, str]] | None,
        searched_roots: list[str] | None = None,
    ) -> Iterator[tuple[Path, str, dict[str, object], str]]:
        def skipped(path: Path, code: str) -> None:
            # Never include parser exceptions: their messages quote note text.
            relative = path.relative_to(self.root).as_posix()
            if diagnostics is not None:
                diagnostics.append({"path": relative, "code": code})
            else:
                warnings.warn(
                    f"Partial search: skipped {relative!r} ({code})",
                    RuntimeWarning,
                    stacklevel=2,
                )

        for base in roots:
            if base.is_symlink():
                skipped(base, "symlink-search-root")
                continue
            if not base.is_dir():
                continue
            if searched_roots is not None:
                searched_roots.append(base.name)
            for path in sorted(base.rglob("*.md")):
                if path.is_symlink():
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    skipped(path, "unreadable-document")
                    continue
                try:
                    metadata, body = self._frontmatter(text)
                except yaml.YAMLError:
                    skipped(path, "invalid-frontmatter")
                    continue
                declared_scope = metadata.get("scope")
                if declared_scope is None or declared_scope == scope:
                    yield path, text, metadata, body

    def find_document(
        self,
        scope: str,
        query: str,
        *,
        limit: int = 20,
        include_archive: bool = False,
    ) -> dict[str, object]:
        """Find exact title, path, or declared-source matches and report coverage."""
        roots = self._search_roots(scope, include_archive)
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

        path_query = query.replace("\\", "/").removeprefix("./")
        title_query = query.casefold()
        skipped: list[dict[str, str]] = []
        searched_roots: list[str] = []
        matches: list[dict[str, str]] = []
        for path, _text, metadata, body in self._search_documents(
            scope, roots, skipped, searched_roots
        ):
            relative = path.relative_to(self.root).as_posix()
            title = self._title(metadata, body, path.stem)
            sources = metadata.get("sources")
            if path_query in (
                relative, relative.removeprefix(f"{SCOPE_ROOTS[scope]}/"),
                path.name, path.stem,
            ):
                kind = "path"
            elif title.casefold() == title_query:
                kind = "title"
            elif isinstance(sources, list) and any(
                isinstance(source, str) and source.strip() == query for source in sources
            ):
                kind = "source"
            else:
                continue
            matches.append({"path": relative, "title": title, "match_kind": kind})

        match_order = {"path": 0, "title": 1, "source": 2}
        matches.sort(key=lambda match: match_order[match["match_kind"]])
        return {
            "scope": scope,
            "include_archive": include_archive,
            "searched_roots": searched_roots,
            "matches": matches[:limit],
            "partial": bool(skipped),
            "skipped": skipped,
            "truncated": len(matches) > limit,
        }

    def search(
        self,
        scope: str,
        query: str,
        *,
        limit: int = 20,
        include_archive: bool = False,
        diagnostics: list[dict[str, str]] | None = None,
    ) -> list[SearchResult]:
        """Return matches ordered by relevance, each pointing at a real line.

        Every candidate is collected first and ranked afterwards, so results are
        no longer limited by path order: an article whose title matches the query
        outranks an index page that merely mentions it. A multi-word query
        (``缓存策略 适用场景``) matches per word — a document holding both words
        ranks above one holding a single word. Line numbers point at the line
        holding the most query words, because that is what a reader needs to land
        on.
        """
        roots = self._search_roots(scope, include_archive)
        if not query.strip():
            return []
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

        # A query may hold several words (``缓存策略 适用场景``). Matching the whole
        # string literally would drop every multi-word query before ranking, so
        # candidates are collected per word and ranked afterwards; a document
        # holding more of the words still wins, the rest keep their BM25 order.
        topic = topic_of(query)
        needles = [part.casefold() for part in topic.split()] or [topic.casefold()]
        bigrams = query_bigrams(topic)

        def matched_words(line: str) -> int:
            lowered = line.casefold()
            literal = sum(1 for part in needles if part in lowered)
            # A literal word always beats any number of bigrams on the same line.
            return literal * (len(bigrams) + 1) + sum(
                1 for bigram in bigrams if bigram in lowered
            )

        candidates: list[tuple[str, str, list[tuple[int, str]]]] = []
        full_text: dict[str, str] = {}

        for path, text, metadata, body in self._search_documents(
            scope, roots, diagnostics
        ):
            lines = self._searchable_lines(text)
            # Ranking orders candidates that contain a query word or enough bigrams.
            lowered = "\n".join(line for _number, line in lines).casefold()
            if not any(part in lowered for part in needles) and not overlaps_enough(
                lowered, bigrams
            ):
                continue
            relative = path.relative_to(self.root).as_posix()
            candidates.append((relative, self._title(metadata, body, path.stem), lines))
            full_text[relative] = text

        if not candidates:
            return []

        ranked = Ranker(candidates).rank(topic, limit=limit)
        by_path = {path: lines for path, _title, lines in candidates}
        titles = {path: title for path, title, _lines in candidates}
        # Rank and quote the same read, even if an editor changes a note while
        # search is running. A second read could fail or invalidate line numbers.
        scored_results: list[tuple[float, SearchResult]] = []
        recency_epoch = date(2020, 1, 1).toordinal()
        recency_span = 10 * 365
        for document in ranked:
            lines = by_path[document.path]
            source_text = full_text[document.path]
            source_lines = source_text.splitlines()
            body_start = self._body_start_line(source_text)

            # Relevance wins first. If two matching lines carry the same query
            # words, prefer the one in the newer dated section. This gives
            # evolving articles a useful recency tie-break without turning date
            # into the search key.
            scored = [
                (
                    matched_words(line),
                    _date_section_ordinal(source_lines, number),
                    -number,
                    number,
                    line,
                )
                for number, line in lines
                if matched_words(line)
            ]
            if not scored:  # pragma: no cover - guarded by the filter above
                continue
            _matches, section_date, _order, hit, _line = max(scored)
            start, end = _section_range(source_text, hit, body_start)
            result = SearchResult(
                scope=scope,
                path=document.path,
                line_start=start,
                line_end=end,
                title=titles[document.path],
                snippet=_section_snippet(source_text, start, end, hit),
            )

            # At document level BM25/title relevance remains dominant. A dated
            # matching section can change the score by at most 3%, so recency
            # only breaks close calls rather than hiding an older better match.
            recency = 0.0
            if section_date:
                recency = max(0.0, min(1.0, (section_date - recency_epoch) / recency_span))
            adjusted = document.score * (1.0 + 0.03 * recency)
            scored_results.append((adjusted, result))

        scored_results.sort(key=lambda item: (-item[0], item[1].path))
        return [result for _score, result in scored_results[:limit]]

    def list_documents(
        self,
        scope: str,
        *,
        prefix: str = "",
        include_archive: bool = False,
    ) -> list[str]:
        if scope not in SCOPE_ROOTS:
            raise InvalidScopeError(f"Unsupported scope: {scope}")
        base = self.root / SCOPE_ROOTS[scope]
        if prefix:
            prefix_path = Path(prefix)
            if prefix_path.is_absolute() or ".." in prefix_path.parts:
                raise InvalidPathError("prefix must be a safe relative path")
            # Same tolerance as ``_validated_target``: callers already pass
            # ``scope``, so a prefix that repeats this scope is accepted, while
            # one that names another scope is rejected instead of silently
            # matching nothing.
            first = prefix_path.parts[0] if prefix_path.parts else ""
            if first == SCOPE_ROOTS[scope]:
                prefix_path = Path(*prefix_path.parts[1:])
            elif first in SCOPE_ROOTS.values():
                raise InvalidPathError(f"prefix must be under {SCOPE_ROOTS[scope]}/")
            base = (base / prefix_path).resolve()
            try:
                base.relative_to((self.root / SCOPE_ROOTS[scope]).resolve())
            except ValueError as exc:
                raise InvalidPathError("prefix escapes the requested scope") from exc

        paths = [
            path.relative_to(self.root).as_posix()
            for path in sorted(base.rglob("*.md"))
            if path.is_file()
            and not path.is_symlink()
            and self._matches_scope(path, scope)
        ] if base.is_dir() else []
        if include_archive:
            archive = self.root / "archive"
            if archive.is_dir():
                paths.extend(
                    path.relative_to(self.root).as_posix()
                    for path in sorted(archive.rglob("*.md"))
                    if path.is_file()
                    and not path.is_symlink()
                    and self._matches_scope(path, scope)
                )
        return paths

    def _matches_scope(self, path: Path, scope: Scope) -> bool:
        """Exclude documents explicitly assigned to the other scope."""
        try:
            metadata, _ = self._frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError):
            return False
        declared_scope = metadata.get("scope")
        return declared_scope is None or declared_scope == scope

    def read(
        self,
        scope: str,
        relative_path: str,
        *,
        include_archive: bool = False,
    ) -> str:
        _, path, _ = self._document_path(
            scope, relative_path, include_archive=include_archive
        )
        return path.read_text(encoding="utf-8")

    def read_range(
        self,
        scope: str,
        relative_path: str,
        start_line: int,
        end_line: int,
        *,
        include_archive: bool = False,
    ) -> dict[str, object]:
        if start_line < 1 or end_line < start_line:
            raise ValueError("Line range must be 1-based and end_line >= start_line")
        _, path, normalized = self._document_path(
            scope, relative_path, include_archive=include_archive
        )
        lines = path.read_text(encoding="utf-8").splitlines()
        if start_line > len(lines):
            raise ValueError(f"start_line {start_line} is outside the document")
        end_line = min(end_line, len(lines))
        selected = [
            {"line": number, "text": lines[number - 1]}
            for number in range(start_line, end_line + 1)
        ]
        return {
            "scope": scope,
            "path": normalized,
            "start_line": start_line,
            "end_line": end_line,
            "lines": selected,
        }

    def propose_change(
        self,
        scope: str,
        relative_path: str,
        expected_text: str,
        replacement: str,
    ) -> dict[str, str]:
        _, path, normalized = self._document_path(scope, relative_path)
        original = path.read_text(encoding="utf-8")
        count = original.count(expected_text) if expected_text else 0
        if count == 0:
            raise VersionConflictError("Expected text is absent from the current file")
        if count > 1:
            raise AmbiguousChangeError("Expected text must match exactly once")
        updated = original.replace(expected_text, replacement, 1)
        return {"path": normalized, "before": original, "after": updated}

    @_serialized_write
    def apply_change(
        self,
        scope: str,
        relative_path: str,
        expected_text: str,
        replacement: str,
        *,
        replaces: Sequence[str] = (),
    ) -> dict[str, str]:
        """Patch one document, optionally absorbing other documents into it.

        ``replaces`` lists existing documents whose content is merged by this
        edit. They are deleted only after the patch is written, and the patched
        body must keep at least ``MERGE_MIN_RATIO`` of their combined length —
        the same guard ``create_document`` uses, so absorbing a document cannot
        silently drop it.
        """
        change = self.propose_change(scope, relative_path, expected_text, replacement)
        path = self.root / change["path"]
        current = path.read_text(encoding="utf-8")
        if current != change["before"]:
            raise VersionConflictError("Document changed while applying the patch")

        checked_scope, _, normalized = self._document_path(scope, relative_path)
        replaced: dict[str, Path] = {}
        for item in replaces:
            _, old_path, old_normalized = self._document_path(checked_scope, item)
            if old_normalized == normalized:
                raise InvalidMetadataError(
                    "apply_change cannot absorb the document it edits"
                )
            replaced[old_normalized] = old_path
        if replaced:
            old_length = sum(
                len(self._frontmatter(p.read_text(encoding="utf-8"))[1])
                for p in replaced.values()
            )
            if len(self._frontmatter(change["after"])[1]) < MERGE_MIN_RATIO * old_length:
                raise InvalidMetadataError(
                    "patched body is shorter than "
                    f"{MERGE_MIN_RATIO:.0%} of the {old_length} chars it absorbs; "
                    "merges may remove duplicates but must not drop content"
                )

        mode = path.stat().st_mode
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(change["after"])
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, mode)
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        for old_path in replaced.values():
            old_path.unlink()
            self._prune_empty_dirs(old_path.parent, checked_scope)
        result = {"path": change["path"], "content": change["after"]}
        if replaced:
            result["replaced"] = sorted(replaced)
        return result

    @_serialized_write
    def create_document(
        self,
        scope: str,
        relative_path: str,
        title: str,
        content: str,
        *,
        document_type: str = "article",
        tags: Sequence[str] = (),
        series: str | None = None,
        sources: Sequence[str] = (),
        created: str | None = None,
        updated: str | None = None,
        replaces: Sequence[str] = (),
    ) -> dict[str, object]:
        """Create one new Markdown document that always starts as review.

        ``replaces`` lists existing documents merged into this one. They are
        deleted only after the new file is written, their sources may be
        re-claimed here, and the new body must keep at least
        ``MERGE_MIN_RATIO`` of their combined body length.

        There is deliberately no ``status`` parameter: creation is the path an
        AI or a migration uses, and such content must not be able to declare
        itself confirmed. ``active`` is only reachable through a version-checked
        ``apply_change`` on an existing document.

        ``created``/``updated`` default to today. They are only set explicitly
        when importing existing documents that must keep their original dates.
        """
        if not title.strip() or "\n" in title or "\r" in title:
            raise InvalidMetadataError("title must be a non-empty single line")
        if document_type not in DOCUMENT_TYPES:
            raise InvalidMetadataError(f"Unsupported document type: {document_type}")
        if content.lstrip().startswith("---"):
            raise InvalidMetadataError(
                "content must be the Markdown body without frontmatter"
            )
        if _has_top_level_heading(content):
            raise InvalidMetadataError(
                "content must not contain top-level Markdown headings (# ); the title generates the single document heading"
            )
        if document_type == "task":
            missing = _missing_task_sections(content)
            if missing:
                raise InvalidMetadataError(
                    "task documents must contain sections: "
                    + ", ".join(f"## {name}" for name in missing)
                )
        normalized_tags = []
        for tag in tags:
            if not isinstance(tag, str) or not tag.strip():
                raise InvalidMetadataError("tags must contain non-empty strings")
            normalized_tags.append(tag.strip())
        if series is not None:
            if not isinstance(series, str) or not series.strip():
                raise InvalidMetadataError(
                    "series must be a non-empty string when provided"
                )
            if "\n" in series or "\r" in series:
                raise InvalidMetadataError("series must be a single line")
            series = series.strip()

        normalized_sources = []
        for source in sources:
            if (
                not isinstance(source, str)
                or not source.strip()
                or "\n" in source
                or "\r" in source
            ):
                raise InvalidMetadataError(
                    "sources must contain non-empty single-line strings"
                )
            normalized_sources.append(source.strip())

        created_date = (
            _normalized_date(created, "created")
            if created is not None
            else date.today().isoformat()
        )
        updated_date = (
            _normalized_date(updated, "updated")
            if updated is not None
            else created_date
        )

        checked_scope, target, normalized = self._new_document_target(
            scope, relative_path
        )
        replaced: dict[str, Path] = {}
        for item in replaces:
            _, old_path, old_normalized = self._document_path(checked_scope, item)
            replaced[old_normalized] = old_path
        if replaced:
            old_length = sum(
                len(self._frontmatter(p.read_text(encoding="utf-8"))[1])
                for p in replaced.values()
            )
            if len(content) < MERGE_MIN_RATIO * old_length:
                raise InvalidMetadataError(
                    f"merged content is {len(content)} chars, below "
                    f"{MERGE_MIN_RATIO:.0%} of the {old_length} chars it replaces; "
                    "merges may remove duplicates but must not drop content"
                )
        self._reject_duplicate_sources(
            checked_scope, normalized, normalized_sources, frozenset(replaced)
        )

        target.parent.mkdir(parents=True, exist_ok=True)
        if target.parent.is_symlink():
            raise InvalidPathError("Symbolic link directories are not valid targets")

        metadata = [
            "---",
            f"title: {_yaml_scalar(title.strip())}",
            f"type: {document_type}",
            f"status: {NEW_DOCUMENT_STATUS}",
            f"scope: {checked_scope}",
            f"created: {created_date}",
            f"updated: {updated_date}",
        ]
        if series is not None:
            metadata.append(f"series: {_yaml_scalar(series)}")
        if normalized_tags:
            metadata.append("tags:")
            metadata.extend(f"  - {_yaml_scalar(tag)}" for tag in normalized_tags)
        if normalized_sources:
            metadata.append("sources:")
            metadata.extend(
                f"  - {_yaml_scalar(source)}" for source in normalized_sources
            )
        document = "\n".join(metadata) + "\n---\n\n"
        document += f"# {title.strip()}\n\n"
        document += content.rstrip() + "\n"

        descriptor = None
        try:
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o644,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                descriptor = None
                handle.write(document)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise DocumentExistsError(f"Document already exists: {normalized}") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        for old_path in replaced.values():
            old_path.unlink()
            self._prune_empty_dirs(old_path.parent, checked_scope)
        result = {"scope": checked_scope, "path": normalized, "content": document}
        if replaced:
            result["replaced"] = sorted(replaced)
        return result

    @_serialized_write
    def delete_document(
        self,
        scope: str,
        relative_path: str,
        *,
        successor: str | None = None,
    ) -> dict[str, str]:
        """Delete one document, optionally only when a successor has absorbed it.

        ``successor`` names a document that is supposed to hold the removed
        content (for example an article a card was folded into). When given, the
        deletion is refused unless the successor body is at least
        ``MERGE_MIN_RATIO`` of the deleted body, so a human-approved cleanup
        cannot quietly drop content. Without it the deletion is unconditional
        and only the caller's approval stands behind it.
        """
        checked_scope, target, normalized = self._document_path(scope, relative_path)
        if successor:
            _, successor_path, successor_normalized = self._document_path(
                checked_scope, successor
            )
            if successor_normalized == normalized:
                raise InvalidMetadataError("a document cannot succeed itself")
            old_length = len(self._frontmatter(target.read_text(encoding="utf-8"))[1])
            kept = len(
                self._frontmatter(successor_path.read_text(encoding="utf-8"))[1]
            )
            if kept < MERGE_MIN_RATIO * old_length:
                raise InvalidMetadataError(
                    f"{successor_normalized} holds {kept} chars, below "
                    f"{MERGE_MIN_RATIO:.0%} of the {old_length} chars it is supposed "
                    "to have absorbed; fold the content in first"
                )
        target.unlink()
        self._prune_empty_dirs(target.parent, checked_scope)
        return {"scope": checked_scope, "path": normalized, "deleted": "true"}

    @_serialized_write
    def move_document(
        self, scope: str, relative_path: str, new_path: str
    ) -> dict[str, str]:
        """Move or rename one document inside its scope without touching content."""
        checked_scope, source, normalized = self._document_path(scope, relative_path)
        _, target, new_normalized = self._new_document_target(checked_scope, new_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.parent.is_symlink():
            raise InvalidPathError("Symbolic link directories are not valid targets")
        os.rename(source, target)
        self._prune_empty_dirs(source.parent, checked_scope)
        return {"scope": checked_scope, "from": normalized, "to": new_normalized}

    def _prune_empty_dirs(self, directory: Path, scope: Scope) -> None:
        # Old category folders should disappear once emptied, but never the
        # scope root itself.
        stop = (self.root / SCOPE_ROOTS[scope]).resolve()
        directory = directory.resolve()
        while directory != stop and stop in directory.parents:
            try:
                directory.rmdir()
            except OSError:
                return
            directory = directory.parent

    def validate(
        self,
        scope: str | None = None,
        *,
        include_archive: bool = False,
    ) -> list[ValidationFinding]:
        """Inspect Markdown metadata without changing the vault."""
        scopes = [scope] if scope is not None else list(SCOPE_ROOTS)
        for selected_scope in scopes:
            if selected_scope not in SCOPE_ROOTS:
                raise InvalidScopeError(f"Unsupported scope: {selected_scope}")

        findings: list[ValidationFinding] = []
        for selected_scope in scopes:
            findings.extend(
                self._validate_files(
                    self.root / SCOPE_ROOTS[selected_scope],
                    selected_scope,
                    in_archive=False,
                )
            )
        if include_archive:
            findings.extend(
                self._validate_files(
                    self.root / "archive",
                    "archive",
                    in_archive=True,
                    allowed_scopes=set(scopes),
                )
            )
        return findings

    def _validate_files(
        self,
        base: Path,
        scope: str,
        *,
        in_archive: bool,
        allowed_scopes: set[str] | None = None,
    ) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []
        if not base.is_dir():
            return findings
        for path in sorted(base.rglob("*.md")):
            findings.extend(
                self._validate_file(
                    path,
                    scope,
                    in_archive=in_archive,
                    allowed_scopes=allowed_scopes,
                )
            )
        return findings

    def _validate_file(
        self,
        path: Path,
        scope: str,
        *,
        in_archive: bool,
        allowed_scopes: set[str] | None,
    ) -> list[ValidationFinding]:
        relative = path.relative_to(self.root).as_posix()
        if path.is_symlink():
            return [
                ValidationFinding(
                    scope,
                    relative,
                    "error",
                    "symlink",
                    "Symbolic links are not valid document paths",
                )
            ]
        try:
            text = path.read_text(encoding="utf-8")
            metadata, body = self._frontmatter(text)
        except UnicodeError:
            return [
                ValidationFinding(
                    scope,
                    relative,
                    "error",
                    "invalid-encoding",
                    "Document is not valid UTF-8",
                )
            ]
        except OSError as exc:
            return [
                ValidationFinding(
                    scope,
                    relative,
                    "error",
                    "unreadable",
                    f"Document could not be read: {exc}",
                )
            ]
        except yaml.YAMLError:
            return [
                ValidationFinding(
                    scope,
                    relative,
                    "error",
                    "invalid-frontmatter",
                    "Frontmatter is not valid YAML",
                )
            ]

        declared_scope = metadata.get("scope")
        if (
            in_archive
            and declared_scope is not None
            and allowed_scopes is not None
            and declared_scope not in allowed_scopes
        ):
            return []

        findings: list[ValidationFinding] = []
        if not in_archive and declared_scope is None:
            findings.append(
                ValidationFinding(
                    scope,
                    relative,
                    "warning",
                    "missing-scope",
                    "Formal documents should declare scope",
                )
            )
        elif (
            not in_archive
            and declared_scope is not None
            and declared_scope != scope
        ):
            findings.append(
                ValidationFinding(
                    scope,
                    relative,
                    "error",
                    "scope-mismatch",
                    f"Frontmatter scope {declared_scope!r} does not match {scope!r}",
                )
            )

        document_type = metadata.get("type")
        if document_type is not None and document_type not in DOCUMENT_TYPES:
            findings.append(
                ValidationFinding(
                    scope,
                    relative,
                    "error",
                    "invalid-type",
                    f"Unsupported document type: {document_type!r}",
                )
            )
        if document_type == "task":
            missing = _missing_task_sections(body)
            if missing:
                findings.append(
                    ValidationFinding(
                        scope,
                        relative,
                        "error",
                        "missing-task-sections",
                        "Task document lacks: "
                        + ", ".join(f"## {name}" for name in missing),
                    )
                )

        status = metadata.get("status")
        if status is not None and status not in DOCUMENT_STATUSES:
            findings.append(
                ValidationFinding(
                    scope,
                    relative,
                    "error",
                    "invalid-status",
                    f"Unsupported document status: {status!r}",
                )
            )

        tags = metadata.get("tags")
        if tags is not None:
            if not isinstance(tags, list) or any(
                not isinstance(tag, str) or not tag.strip() for tag in tags
            ):
                findings.append(
                    ValidationFinding(
                        scope,
                        relative,
                        "error",
                        "invalid-tags",
                        "tags must be a list of non-empty strings",
                    )
                )
        return findings

    @staticmethod
    def serialize_result(result: SearchResult) -> dict[str, object]:
        return asdict(result)

    @staticmethod
    def serialize_finding(finding: ValidationFinding) -> dict[str, str]:
        return asdict(finding)
