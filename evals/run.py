"""Evaluate the real Vault search, without writing to the corpus or vault."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import statistics
import time

from knowledge_mcp.vault import Vault


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def load_questions(path: Path, root: Path) -> list[dict]:
    questions = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not questions or len({q["id"] for q in questions}) != len(questions):
        raise ValueError("questions must be nonempty and have unique IDs")
    for question in questions:
        if not question["query"].strip() or not question["expected_paths"]:
            raise ValueError("each question needs a query and expected_paths")
        for relative in question["expected_paths"]:
            target = (root / relative).resolve()
            if not target.is_relative_to(root.resolve()) or not target.is_file():
                raise ValueError("expected document missing or outside vault")
            if relative.split("/")[0] != question["scope"]:
                raise ValueError("expected document must belong to the query scope")
    return questions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, default=Path("evals/corpus"))
    parser.add_argument("--questions", type=Path, default=Path("evals/public.jsonl"))
    parser.add_argument("--mode", choices=["bm25", "rerank"], default="bm25")
    parser.add_argument("--model", default="BAAI/bge-reranker-base")
    parser.add_argument("--top-n", type=int, default=30)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--summary-only", action="store_true", help="omit questions, document paths and titles")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    if args.output.resolve().is_relative_to(args.vault.resolve()):
        parser.error("evaluation output must be outside the read-only vault")
    if args.output.resolve() == args.questions.resolve():
        parser.error("evaluation output must not overwrite the question set")
    questions = load_questions(args.questions, args.vault)
    started = time.perf_counter()
    if args.mode == "rerank":
        from knowledge_mcp.reranking import RerankConfig, get_reranker
        config = RerankConfig(enabled=True, model=args.model, top_n=args.top_n)
        ranker = get_reranker(config)
        # Fail loudly in evaluations: fallback must never be reported as rerank.
        if not ranker.load():
            raise RuntimeError("reranker unavailable; evaluation aborted")
        vault = Vault(args.vault, rerank=config)
    else:
        vault = Vault(args.vault)
    load_ms = (time.perf_counter() - started) * 1000
    # One full warmup round; filesystem cache and ONNX setup excluded from timings.
    for q in questions:
        vault.search(q["scope"], q["query"], limit=10, diagnostics=[])
    details = []
    latencies = []
    for q in questions:
        times = []
        for _ in range(args.repeats):
            started = time.perf_counter()
            matches = vault.search(q["scope"], q["query"], limit=10, diagnostics=[])
            times.append((time.perf_counter() - started) * 1000)
            if args.mode == "rerank" and matches and any(getattr(m, "rerank_score", None) is None for m in matches):
                raise RuntimeError("rerank failed during evaluation; refusing mixed results")
        latencies.extend(times)
        rank = next((i for i, m in enumerate(matches, 1) if m.path in q["expected_paths"]), 0)
        details.append({"id":q["id"], "type":q.get("type", "unspecified"), "rank":rank,
                        "latency_ms":statistics.median(times)})
    summary = {f"recall@{k}": sum(0 < d["rank"] <= k for d in details) / len(details) for k in (1, 5, 10)}
    summary.update({"mrr@10":statistics.mean(1 / d["rank"] if d["rank"] else 0 for d in details),
                    "p50_ms":percentile(latencies, .5), "p95_ms":percentile(latencies, .95)})
    result = {"mode":args.mode, "model":args.model if args.mode == "rerank" else None,
              "top_n":args.top_n if args.mode == "rerank" else None,
              "questions":len(questions), "questions_sha256":hashlib.sha256(args.questions.read_bytes()).hexdigest(),
              "repeats":args.repeats, "warmup_rounds":1, "load_ms":load_ms,
              "python":platform.python_version(), "platform":platform.platform(), "summary":summary,
              "types":{kind:sum(q.get("type", "unspecified") == kind for q in questions)
                       for kind in sorted({q.get("type", "unspecified") for q in questions})}}
    if not args.summary_only:
        result["details"] = details
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"mode":args.mode,"questions":len(questions),"summary":summary}, indent=2))


if __name__ == "__main__":
    main()
