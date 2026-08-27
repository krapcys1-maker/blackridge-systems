"""Convert retained auditor reports and run the exact official SciFact evaluator."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    observations: dict[int, dict[str, Any]] = {}
    for report_path in args.reports:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        for observation in report["observations"]:
            claim_id = int(observation["case_id"].rsplit("-", 1)[1])
            if claim_id in observations:
                raise ValueError(f"duplicate reported claim: {claim_id}")
            observations[claim_id] = observation

    gold = _jsonl(args.gold)
    gold_ids = [int(item["id"]) for item in gold]
    if set(gold_ids) != set(observations):
        missing = sorted(set(gold_ids) - set(observations))
        extra = sorted(set(observations) - set(gold_ids))
        raise ValueError(f"report coverage mismatch; missing={missing}, extra={extra}")

    predictions = []
    for claim_id in gold_ids:
        audit = observations[claim_id]["audit"]
        evidence: dict[str, dict[str, Any]] = {}
        for document in audit["documents"]:
            document_id = document["document_id"]
            if document_id in evidence:
                raise ValueError(f"duplicate predicted document: {claim_id}/{document_id}")
            label = {
                "support": "SUPPORT",
                "contradict": "CONTRADICT",
            }[document["verdict"]]
            evidence[document_id] = {
                "label": label,
                "sentences": [
                    int(rationale["sentence_index"])
                    for rationale in document["rationales"]
                ],
            }
        predictions.append({"id": claim_id, "evidence": evidence})

    args.predictions_output.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in predictions),
        encoding="utf-8",
    )
    evaluator_module = _load_module("exact_scifact_evaluator", args.evaluator)
    normalized_gold = [evaluator_module.unify_label(item) for item in gold]
    evaluator = evaluator_module.Evaluator(verbose=True)
    metrics = evaluator.evaluate(normalized_gold, predictions)
    result = {
        "schema_version": "1",
        "evaluator_commit": args.evaluator_commit,
        "evaluator_file": str(args.evaluator.resolve()),
        "cases": len(gold),
        "metrics": metrics,
    }
    args.metrics_output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--reports", required=True, nargs="+", type=Path)
    parser.add_argument("--evaluator", required=True, type=Path)
    parser.add_argument("--evaluator-commit", required=True)
    parser.add_argument("--predictions-output", required=True, type=Path)
    parser.add_argument("--metrics-output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
