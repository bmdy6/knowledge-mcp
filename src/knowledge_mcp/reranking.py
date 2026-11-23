"""Optional, lazy CPU cross-encoder. Failures preserve the BM25 search path."""
from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
import logging
import math
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from .vault import SearchResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RerankConfig:
    enabled: bool = False
    model: str = "BAAI/bge-reranker-base"
    top_n: int = 30

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("rerank.enabled must be a boolean")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("rerank.model must be a nonempty string")
        if type(self.top_n) is not int or not 1 <= self.top_n <= 100:
            raise ValueError("rerank.top_n must be an integer between 1 and 100")


def load_config(path: Path | None) -> RerankConfig:
    """Read an explicitly supplied YAML file; no files are created in the vault."""
    if path is None:
        return RerankConfig()
    settings = yaml.safe_load(path.expanduser().read_text(encoding="utf-8"))
    if settings is None:
        return RerankConfig()
    if not isinstance(settings, dict) or not isinstance(settings.get("rerank", {}), dict):
        raise ValueError("configuration must contain a rerank mapping")
    values = settings.get("rerank", {})
    if set(values) - {"enabled", "model", "top_n"}:
        raise ValueError("unknown rerank configuration option")
    return RerankConfig(**values)


def _load_encoder(config: RerankConfig):
    # Keep both the import and the model download entirely off the default path.
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return TextCrossEncoder(
        model_name=config.model, providers=["CPUExecutionProvider"], threads=4,
    )


class Reranker:
    def __init__(self, config: RerankConfig):
        self.config = config
        self._encoder = None
        self._failed = False
        self._lock = Lock()

    @property
    def failed(self) -> bool:
        return self._failed

    def _disable(self, stage: str, error: Exception) -> None:
        # Exception messages can quote private queries/documents. Log only type.
        logger.warning("Rerank %s failed (%s); using BM25 until process restart",
                       stage, type(error).__name__)
        self._failed = True
        self._encoder = None

    def _load(self) -> bool:
        if self._failed or not self.config.enabled:
            return False
        if self._encoder is None:
            try:
                self._encoder = _load_encoder(self.config)
            except Exception as error:
                self._disable("load", error)
                return False
        return True

    def load(self) -> bool:
        with self._lock:
            return self._load()

    def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult] | None:
        if not results:
            return []
        with self._lock:
            if not self._load():
                return None
            try:
                # Search snippets are capped at 1000 characters already. Bound
                # titles too, so malformed frontmatter cannot send a full note.
                documents = [f"{r.title[:256]}\n{r.snippet[:1000]}" for r in results]
                scores = [float(s) for s in self._encoder.rerank(
                    query, documents, batch_size=8)]
                if len(scores) != len(results) or not all(math.isfinite(s) for s in scores):
                    raise ValueError("invalid reranker scores")
            except Exception as error:
                self._disable("inference", error)
                return None
        # Stable ties retain BM25 order; scores are model scores, not probabilities.
        return sorted(
            [replace(result, rerank_score=score) for result, score in zip(results, scores)],
            key=lambda result: -result.rerank_score,
        )


@lru_cache(maxsize=4)
def get_reranker(config: RerankConfig) -> Reranker:
    """Reuse the model even though MCP creates a Vault for every tool call."""
    return Reranker(config)
