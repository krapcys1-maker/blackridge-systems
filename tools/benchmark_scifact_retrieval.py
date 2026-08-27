"""Measure open-corpus retrieval independently of downstream stance decisions."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("retrieval_benchmark_component", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load retriever: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def benchmark(args: argparse.Namespace) -> dict[str, Any]:
    module = _load_module(args.retriever)
    started = time.perf_counter()
    index = module.Bm25Index(args.corpus)
    index_seconds = time.perf_counter() - started
    excluded = {
        case["upstream_claim_id"] for case in _jsonl(args.exclude_cases)
    } if args.exclude_cases is not None else set()
    claims = [
        claim
        for claim in _jsonl(args.development)
        if claim["id"] not in excluded and claim["evidence"]
    ]
    ranks: list[int | None] = []
    query_started = time.perf_counter()
    misses = []
    for claim in claims:
        request = {
            "request_id": f"scifact-dev-{claim['id']}",
            "claim": claim["claim"],
            "maximum_candidates": 10,
        }
        result = index.retrieve(request)
        expected_ids = {str(document_id) for document_id in claim["evidence"]}
        retrieved_ids = [item["document_id"] for item in result["candidates"]]
        rank = next(
            (
                position
                for position, value in enumerate(retrieved_ids, start=1)
                if value in expected_ids
            ),
            None,
        )
        ranks.append(rank)
        if rank is None:
            misses.append(
                {
                    "claim_id": claim["id"],
                    "claim": claim["claim"],
                    "expected_document_ids": sorted(expected_ids),
                    "retrieved_document_ids": retrieved_ids,
                }
            )
    query_seconds = time.perf_counter() - query_started
    return {
        "schema_version": "1",
        "evidence_claims": len(claims),
        "recall": {
            f"at_{limit}": round(
                sum(rank is not None and rank <= limit for rank in ranks) / len(ranks), 8
            )
            for limit in (1, 3, 5, 10)
        },
        "mean_reciprocal_rank_at_10": round(
            sum(1 / rank for rank in ranks if rank is not None) / len(ranks), 8
        ),
        "index_seconds": round(index_seconds, 6),
        "query_seconds": round(query_seconds, 6),
        "mean_query_milliseconds": round(query_seconds * 1000 / len(claims), 6),
        "misses_at_10": misses,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development", required=True, type=Path)
    parser.add_argument("--exclude-cases", type=Path)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--retriever", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = benchmark(args)
    rendered = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    args.output.write_text(rendered, encoding="utf-8")
    compact = dict(report)
    compact["misses_at_10"] = len(report["misses_at_10"])
    print(json.dumps(compact, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
