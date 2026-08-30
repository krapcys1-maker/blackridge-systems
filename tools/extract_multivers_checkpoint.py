"""Extract inference-only MultiVerS tensors from an exact trusted legacy checkpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import types
from pathlib import Path
from typing import Any

import torch
from safetensors.torch import save_file

EXPECTED_SCIFACT_SHA256 = "630739ec906bc5ad959a59bcee479329f97aeee4eb373230c79595b076c46690"
MULTIVERS_COMMIT = "a6ce033f0e17ae38c1f102eae1ee4ca213fbbe2e"
LONGFORMER_REVISION = "b190dd42e462d2dee634d1162c839710079f97ab"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _install_legacy_pickle_stubs() -> None:
    """Provide the two Lightning globals referenced by the 2021 pickle.

    ``torch.load`` can execute pickle code, so callers must hash-check the official
    checkpoint before this function is used. The extracted output is safetensors.
    """

    lightning = types.ModuleType("pytorch_lightning")
    callbacks = types.ModuleType("pytorch_lightning.callbacks")
    checkpoint = types.ModuleType("pytorch_lightning.callbacks.model_checkpoint")
    utilities = types.ModuleType("pytorch_lightning.utilities")
    argparse_module = types.ModuleType("pytorch_lightning.utilities.argparse")
    model_checkpoint = type("ModelCheckpoint", (object,), {})
    model_checkpoint.__module__ = checkpoint.__name__
    checkpoint.ModelCheckpoint = model_checkpoint  # type: ignore[attr-defined]
    argparse_module._gpus_arg_default = lambda value: value  # type: ignore[attr-defined]
    for module in (lightning, callbacks, checkpoint, utilities, argparse_module):
        sys.modules[module.__name__] = module


def extract(checkpoint_path: Path, output_directory: Path) -> dict[str, Any]:
    actual_hash = _sha256(checkpoint_path)
    if actual_hash != EXPECTED_SCIFACT_SHA256:
        raise ValueError(
            f"refusing untrusted legacy checkpoint: expected {EXPECTED_SCIFACT_SHA256}, "
            f"got {actual_hash}"
        )
    if output_directory.exists():
        raise ValueError(f"output already exists: {output_directory}")

    _install_legacy_pickle_stubs()
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    state = checkpoint.get("state_dict")
    hyper_parameters = checkpoint.get("hyper_parameters")
    if not isinstance(state, dict) or not isinstance(hyper_parameters, dict):
        raise ValueError("legacy checkpoint structure is incompatible")
    retained = {
        key: value.contiguous()
        for key, value in state.items()
        if key.startswith(("encoder.", "label_classifier.", "rationale_classifier."))
    }
    required_shapes = {
        "encoder.embeddings.word_embeddings.weight": (50275, 1024),
        "label_classifier._linear_layers.0.weight": (1024, 1024),
        "label_classifier._linear_layers.1.weight": (3, 1024),
        "rationale_classifier._linear_layers.0.weight": (1024, 2048),
        "rationale_classifier._linear_layers.1.weight": (1, 1024),
    }
    for key, expected_shape in required_shapes.items():
        value = retained.get(key)
        if value is None or tuple(value.shape) != expected_shape:
            raise ValueError(f"legacy checkpoint tensor is incompatible: {key}")

    hparams = hyper_parameters.get("hparams")
    if hparams is None:
        raise ValueError("legacy checkpoint is missing hparams")
    label_threshold = getattr(hparams, "label_threshold", None)
    rationale_threshold = float(getattr(hparams, "rationale_threshold", 0.5))
    if label_threshold is not None or rationale_threshold != 0.5:
        raise ValueError("legacy checkpoint thresholds differ from the frozen decoder")

    output_directory.mkdir(parents=True)
    model_path = output_directory / "model.safetensors"
    save_file(retained, model_path)
    report = {
        "schema_version": "1",
        "source_checkpoint_sha256": actual_hash,
        "source_checkpoint_size": checkpoint_path.stat().st_size,
        "multivers_commit": MULTIVERS_COMMIT,
        "longformer_revision": LONGFORMER_REVISION,
        "training_epoch": int(checkpoint["epoch"]),
        "training_global_step": int(checkpoint["global_step"]),
        "pytorch_lightning_version": str(checkpoint["pytorch-lightning_version"]),
        "label_threshold": label_threshold,
        "rationale_threshold": rationale_threshold,
        "tensor_count": len(retained),
        "model_sha256": _sha256(model_path),
        "model_size": model_path.stat().st_size,
    }
    (output_directory / "extraction-report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(extract(args.checkpoint, args.output), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
