#!/usr/bin/env python3
"""Evidence-gated scientific auditor using exact extracted MultiVerS weights."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import torch
from safetensors.torch import load_file
from tokenizers import Tokenizer
from torch import nn
from transformers import LongformerConfig, LongformerModel

_LABELS = ("contradict", "neutral", "support")


class MultiVerSInference:
    def __init__(self, model_path: Path, config_path: Path, tokenizer_path: Path) -> None:
        torch.set_num_threads(8)
        config_value = json.loads(config_path.read_text(encoding="utf-8"))
        self._config = LongformerConfig.from_dict(config_value)
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        if self._tokenizer.get_vocab_size() != 50275 or self._config.vocab_size != 50275:
            raise ValueError("MultiVerS tokenizer vocabulary is incompatible")
        state = load_file(model_path, device="cpu")
        self._encoder = LongformerModel(self._config)
        encoder_state = {
            key.removeprefix("encoder."): value
            for key, value in state.items()
            if key.startswith("encoder.")
        }
        incompatible = self._encoder.load_state_dict(encoder_state, strict=False)
        allowed_position_id = {"embeddings.position_ids"}
        if (
            set(incompatible.missing_keys) - allowed_position_id
            or set(incompatible.unexpected_keys) - allowed_position_id
        ):
            raise ValueError(f"MultiVerS encoder tensors are incompatible: {incompatible}")

        self._label_first = nn.Linear(1024, 1024)
        self._label_second = nn.Linear(1024, 3)
        self._rationale_first = nn.Linear(2048, 1024)
        self._rationale_second = nn.Linear(1024, 1)
        for prefix, layer in (
            ("label_classifier._linear_layers.0", self._label_first),
            ("label_classifier._linear_layers.1", self._label_second),
            ("rationale_classifier._linear_layers.0", self._rationale_first),
            ("rationale_classifier._linear_layers.1", self._rationale_second),
        ):
            layer.load_state_dict(
                {"weight": state[f"{prefix}.weight"], "bias": state[f"{prefix}.bias"]}
            )
        del state, encoder_state
        for module in (
            self._encoder,
            self._label_first,
            self._label_second,
            self._rationale_first,
            self._rationale_second,
        ):
            module.eval()
        self._activation = nn.GELU()

    def predict(self, claim: str, document: dict[str, Any]) -> dict[str, Any]:
        text = claim + "</s>" + document["title"] + "</s>" + "</s>".join(document["abstract"])
        encoded = self._tokenizer.encode(text)
        input_ids = torch.tensor([encoded.ids], dtype=torch.int64)
        attention_mask = torch.tensor([encoded.attention_mask], dtype=torch.int64)
        if input_ids.shape[1] > 4096:
            raise ValueError("MultiVerS input exceeds its frozen 4096-token window")
        eos_indices = torch.where(input_ids[0] == 2)[0]
        sentence_indices = eos_indices[2:]
        if len(sentence_indices) != len(document["abstract"]):
            raise ValueError("MultiVerS sentence boundary reconstruction failed")
        positions = torch.arange(input_ids.shape[1])
        special = (input_ids[0] == 0) | (input_ids[0] == 2)
        global_attention = (special | (positions < eos_indices[0])).to(torch.int64)
        with torch.inference_mode():
            output = self._encoder(
                input_ids=input_ids,
                attention_mask=attention_mask,
                global_attention_mask=global_attention.unsqueeze(0),
            )
            pooled = output.pooler_output
            label_logits = self._label_second(self._activation(self._label_first(pooled)))
            label_probabilities = torch.softmax(label_logits, dim=1)[0]
            sentence_states = output.last_hidden_state[:, sentence_indices, :]
            rationale_input = torch.cat(
                [pooled.unsqueeze(1).expand_as(sentence_states), sentence_states], dim=2
            )
            rationale_logits = self._rationale_second(
                self._activation(self._rationale_first(rationale_input))
            ).squeeze(2)
            rationale_probabilities = torch.sigmoid(rationale_logits)[0]
        return {
            "label": _LABELS[int(label_probabilities.argmax())],
            "confidence": float(label_probabilities.max()),
            "rationales": [
                index
                for index, probability in enumerate(rationale_probabilities.tolist())
                if probability >= 0.5
            ],
        }


def audit(payload: dict[str, Any], engine: MultiVerSInference) -> dict[str, Any]:
    inputs = payload["inputs"]
    request = inputs["scientific-claim-request/v1"]
    retrieval = inputs["scientific-candidate-set/v1"]
    if request["request_id"] != retrieval["request_id"] or request["claim"] != retrieval["claim"]:
        raise ValueError("request identity does not match retrieval identity")
    candidates = retrieval["candidates"][:6]
    sources = []
    documents = []
    for position, candidate in enumerate(candidates, start=1):
        sources.append({"document_id": candidate["document_id"], "title": candidate["title"]})
        prediction = engine.predict(request["claim"], candidate)
        if prediction["label"] != "neutral" and prediction["rationales"]:
            documents.append(
                {
                    "document_id": candidate["document_id"],
                    "title": candidate["title"],
                    "verdict": prediction["label"],
                    "rationales": [
                        {"sentence_index": index, "quote": candidate["abstract"][index]}
                        for index in prediction["rationales"]
                    ],
                }
            )
        if position >= 3 and len(documents) >= request["minimum_evidence_documents"]:
            break
    if len(documents) < request["minimum_evidence_documents"]:
        documents = []
    verdicts = {document["verdict"] for document in documents}
    if verdicts == {"support", "contradict"}:
        status = "mixed"
    elif verdicts == {"support"}:
        status = "supported"
    elif verdicts == {"contradict"}:
        status = "contradicted"
    else:
        status = "insufficient-evidence"
    summaries = {
        "supported": "The frozen corpus contains cited evidence that supports the claim.",
        "contradicted": "The frozen corpus contains cited evidence that contradicts the claim.",
        "mixed": "The frozen corpus contains cited supporting and contradictory evidence.",
        "insufficient-evidence": "No retrieved frozen-corpus evidence passed both model gates.",
    }
    return {
        "schema_version": "1",
        "request_id": request["request_id"],
        "claim": request["claim"],
        "status": status,
        "summary": summaries[status],
        "documents": documents,
        "sources": sources,
    }


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: scifact_multivers_auditor.py MODEL.safetensors CONFIG.json TOKENIZER.json"
        )
    payload = json.load(sys.stdin)
    engine = MultiVerSInference(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
    print(json.dumps(audit(payload, engine), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
