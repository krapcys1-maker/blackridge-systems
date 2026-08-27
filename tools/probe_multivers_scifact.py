"""Probe the exact official MultiVerS SciFact weights on the frozen workload."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import torch
from safetensors.torch import load_file
from torch import nn
from transformers import AutoTokenizer, LongformerConfig, LongformerModel

LABELS = ("CONTRADICT", "NEI", "SUPPORT")


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


def _cases(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.cases is not None:
        return _jsonl(args.cases)
    preparer = _load_module("multivers_scifact_preparer", args.preparer)
    corpus = {document["doc_id"]: document for document in _jsonl(args.corpus)}
    excluded = {
        case["upstream_claim_id"] for case in _jsonl(args.exclude_cases)
    } if args.exclude_cases is not None else set()
    cases = []
    for claim in _jsonl(args.development):
        if claim["id"] in excluded:
            continue
        normalized = dict(claim)
        normalized["cited_doc_ids"] = list(dict.fromkeys(claim["cited_doc_ids"]))
        cases.append(preparer._make_case(normalized, corpus))
    return cases


class MultiVerSInference:
    def __init__(self, model_path: Path, tokenizer_directory: Path) -> None:
        torch.set_num_threads(8)
        config = LongformerConfig.from_pretrained(tokenizer_directory, local_files_only=True)
        self.tokenizer = AutoTokenizer.from_pretrained(
            tokenizer_directory, local_files_only=True
        )
        if len(self.tokenizer) != 50275 or config.vocab_size != 50275:
            raise ValueError("MultiVerS tokenizer vocabulary is incompatible")
        state = load_file(model_path, device="cpu")
        self.encoder = LongformerModel(config)
        encoder_state = {
            key.removeprefix("encoder."): value
            for key, value in state.items()
            if key.startswith("encoder.")
        }
        incompatible = self.encoder.load_state_dict(encoder_state, strict=False)
        allowed_position_id = {"embeddings.position_ids"}
        if set(incompatible.missing_keys) - allowed_position_id or set(
            incompatible.unexpected_keys
        ) - allowed_position_id:
            raise ValueError(f"MultiVerS encoder tensors are incompatible: {incompatible}")

        self.label_first = nn.Linear(1024, 1024)
        self.label_second = nn.Linear(1024, 3)
        self.rationale_first = nn.Linear(2048, 1024)
        self.rationale_second = nn.Linear(1024, 1)
        for prefix, layer in (
            ("label_classifier._linear_layers.0", self.label_first),
            ("label_classifier._linear_layers.1", self.label_second),
            ("rationale_classifier._linear_layers.0", self.rationale_first),
            ("rationale_classifier._linear_layers.1", self.rationale_second),
        ):
            layer.load_state_dict(
                {"weight": state[f"{prefix}.weight"], "bias": state[f"{prefix}.bias"]}
            )
        del state, encoder_state
        self.encoder.eval()
        self.label_first.eval()
        self.label_second.eval()
        self.rationale_first.eval()
        self.rationale_second.eval()
        self.activation = nn.GELU()

    def predict(self, claim: str, document: dict[str, Any]) -> dict[str, Any]:
        eos = self.tokenizer.eos_token
        if not isinstance(eos, str):
            raise ValueError("MultiVerS tokenizer has no EOS token")
        text = claim + eos + document["title"] + eos + eos.join(document["abstract"])
        tokenized = self.tokenizer(text, return_tensors="pt")
        input_ids = tokenized["input_ids"][0]
        if input_ids.shape[0] > 4096:
            raise ValueError("MultiVerS input exceeds its frozen 4096-token window")
        eos_indices = torch.where(input_ids == self.tokenizer.eos_token_id)[0]
        sentence_indices = eos_indices[2:]
        if len(sentence_indices) != len(document["abstract"]):
            raise ValueError("MultiVerS sentence boundary reconstruction failed")
        positions = torch.arange(len(input_ids))
        special = (input_ids == self.tokenizer.bos_token_id) | (
            input_ids == self.tokenizer.eos_token_id
        )
        global_attention = (special | (positions < eos_indices[0])).to(torch.int64)
        inputs = {
            "input_ids": tokenized["input_ids"],
            "attention_mask": tokenized["attention_mask"],
            "global_attention_mask": global_attention.unsqueeze(0),
        }
        with torch.inference_mode():
            encoded = self.encoder(**inputs)
            pooled = encoded.pooler_output
            label_logits = self.label_second(self.activation(self.label_first(pooled)))
            label_probabilities = torch.softmax(label_logits, dim=1)[0]
            sentence_states = encoded.last_hidden_state[:, sentence_indices, :]
            pooled_expanded = pooled.unsqueeze(1).expand_as(sentence_states)
            rationale_input = torch.cat([pooled_expanded, sentence_states], dim=2)
            rationale_logits = self.rationale_second(
                self.activation(self.rationale_first(rationale_input))
            ).squeeze(2)
            rationale_probabilities = torch.sigmoid(rationale_logits)[0]
        label = LABELS[int(label_probabilities.argmax())]
        rationales = [
            index
            for index, probability in enumerate(rationale_probabilities.tolist())
            if probability >= 0.5
        ]
        return {
            "label": label,
            "label_probabilities": {
                name: round(float(probability), 8)
                for name, probability in zip(
                    LABELS, label_probabilities.tolist(), strict=True
                )
            },
            "rationales": rationales,
            "rationale_probabilities": [
                round(float(probability), 8)
                for probability in rationale_probabilities.tolist()
            ],
            "input_tokens": int(input_ids.shape[0]),
        }


def _audit_case(
    engine: MultiVerSInference,
    request: dict[str, Any],
    candidates: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    traces = []
    documents = []
    for candidate in candidates["candidates"][:3]:
        started = time.perf_counter()
        prediction = engine.predict(request["claim"], candidate)
        prediction["document_id"] = candidate["document_id"]
        prediction["duration_seconds"] = round(time.perf_counter() - started, 3)
        traces.append(prediction)
        if prediction["label"] == "NEI" or not prediction["rationales"]:
            continue
        verdict = "support" if prediction["label"] == "SUPPORT" else "contradict"
        documents.append(
            {
                "document_id": candidate["document_id"],
                "title": candidate["title"],
                "verdict": verdict,
                "rationales": [
                    {"sentence_index": index, "quote": candidate["abstract"][index]}
                    for index in prediction["rationales"]
                ],
            }
        )
    labels = {document["verdict"] for document in documents}
    if labels == {"support", "contradict"}:
        status = "mixed"
    elif labels == {"support"}:
        status = "supported"
    elif labels == {"contradict"}:
        status = "contradicted"
    else:
        status = "insufficient-evidence"
    sources = [
        {"document_id": item["document_id"], "title": item["title"]}
        for item in candidates["candidates"][:3]
    ]
    audit = {
        "schema_version": "1",
        "request_id": request["request_id"],
        "claim": request["claim"],
        "status": status,
        "documents": documents,
        "sources": sources,
    }
    return audit, traces


def probe(args: argparse.Namespace) -> dict[str, Any]:
    retriever = _load_module("multivers_scifact_retriever", args.retriever)
    index = retriever.Bm25Index(args.corpus)
    started = time.perf_counter()
    engine = MultiVerSInference(args.model, args.tokenizer_directory)
    model_load_seconds = round(time.perf_counter() - started, 3)
    observations = []
    cases = _cases(args)
    for position, case in enumerate(cases, start=1):
        candidates = index.retrieve(case["request"])
        audit, traces = _audit_case(engine, case["request"], candidates)
        expected = case["expected_audit"]
        expected_documents = sorted(
            (item["document_id"], item["verdict"])
            for item in expected["documents"]
        )
        actual_documents = sorted(
            (item["document_id"], item["verdict"])
            for item in audit["documents"]
        )
        expected_rationales = {
            item["document_id"]: sorted(
                rationale["sentence_index"] for rationale in item["rationales"]
            )
            for item in expected["documents"]
        }
        actual_rationales = {
            item["document_id"]: sorted(
                rationale["sentence_index"] for rationale in item["rationales"]
            )
            for item in audit["documents"]
        }
        rationales_nonempty = all(item["rationales"] for item in audit["documents"])
        retrieved_top3 = {item["document_id"] for item in candidates["candidates"][:3]}
        observations.append(
            {
                "case_id": case["case_id"],
                "claim": case["request"]["claim"],
                "expected_status": expected["status"],
                "actual_status": audit["status"],
                "status_match": expected["status"] == audit["status"],
                "expected_documents": expected_documents,
                "actual_documents": actual_documents,
                "document_match": expected_documents == actual_documents,
                "expected_rationales": expected_rationales,
                "actual_rationales": actual_rationales,
                "rationale_match": expected_rationales == actual_rationales,
                "rationales_nonempty": rationales_nonempty,
                "gold_document_retrieved_top3": not expected_rationales
                or bool(set(expected_rationales) & retrieved_top3),
                "audit": audit,
                "trace": traces,
            }
        )
        if position == len(cases) or position % 10 == 0:
            print(f"completed {position}/{len(cases)} cases", file=sys.stderr, flush=True)
    return {
        "schema_version": "1",
        "model_load_seconds": model_load_seconds,
        "summary": {
            "cases": len(observations),
            "status_matches": sum(item["status_match"] for item in observations),
            "document_matches": sum(item["document_match"] for item in observations),
            "rationale_matches": sum(item["rationale_match"] for item in observations),
            "nonempty_rationale_cases": sum(
                item["rationales_nonempty"] for item in observations
            ),
            "gold_document_retrieved_top3_cases": sum(
                item["gold_document_retrieved_top3"] for item in observations
            ),
        },
        "observations": observations,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--cases", type=Path)
    source.add_argument("--development", type=Path)
    parser.add_argument("--exclude-cases", type=Path)
    parser.add_argument(
        "--preparer",
        type=Path,
        default=Path(__file__).with_name("prepare_scifact_workload.py"),
    )
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--retriever", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--tokenizer-directory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = probe(args)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    compact = {
        "model_load_seconds": report["model_load_seconds"],
        "summary": report["summary"],
        "cases": [
            {
                "case_id": item["case_id"],
                "expected": item["expected_status"],
                "actual": item["actual_status"],
                "documents": item["actual_documents"],
                "durations": [entry["duration_seconds"] for entry in item["trace"]],
            }
            for item in report["observations"]
        ],
    }
    print(json.dumps(compact, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
