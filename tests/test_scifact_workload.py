from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "prepare_scifact_workload", ROOT / "tools" / "prepare_scifact_workload.py"
)
assert SPEC is not None and SPEC.loader is not None
PREPARER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARER)


def test_audit_contract_rejects_status_document_disagreement() -> None:
    schema = json.loads(
        (
            ROOT
            / "benchmarks"
            / "scientific-claim-auditor-v1"
            / "public"
            / "claim-audit.schema.json"
        ).read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)
    document = {
        "document_id": "10",
        "title": "Frozen source",
        "verdict": "support",
        "rationales": [{"sentence_index": 0, "quote": "Exact quote."}],
    }
    base = {
        "schema_version": "1",
        "request_id": "contract-case",
        "claim": "A sufficiently long scientific claim.",
        "summary": "Corpus-relative result.",
        "sources": [{"document_id": "10", "title": "Frozen source"}],
    }

    assert not list(validator.iter_errors({**base, "status": "supported", "documents": [document]}))
    assert list(validator.iter_errors({**base, "status": "supported", "documents": []}))
    assert list(validator.iter_errors({**base, "status": "contradicted", "documents": [document]}))
    assert list(validator.iter_errors({**base, "status": "mixed", "documents": [document]}))


def _jsonl(records: list[dict[str, object]]) -> bytes:
    return b"".join((json.dumps(record) + "\n").encode() for record in records)


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    claims = _jsonl(
        [
            {
                "id": 1,
                "claim": "The alpha result is supported.",
                "evidence": {"10": [{"sentences": [0], "label": "SUPPORT"}]},
                "cited_doc_ids": [10],
            },
            {
                "id": 2,
                "claim": "The beta result is supported.",
                "evidence": {"20": [{"sentences": [1], "label": "CONTRADICT"}]},
                "cited_doc_ids": [20],
            },
            {
                "id": 3,
                "claim": "The unknown result has evidence.",
                "evidence": {},
                "cited_doc_ids": [10],
            },
        ]
    )
    corpus = _jsonl(
        [
            {"doc_id": 10, "title": "Alpha", "abstract": ["Exact alpha quote."]},
            {
                "doc_id": 20,
                "title": "Beta",
                "abstract": ["Background.", "Exact beta quote."],
            },
        ]
    )
    training = _jsonl(
        [
            {
                "id": 101,
                "claim": "A disjoint training claim exists.",
                "evidence": {},
                "cited_doc_ids": [10],
            }
        ]
    )
    archive = tmp_path / "data.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name, content in (
            (PREPARER.CLAIMS_MEMBER, claims),
            (PREPARER.TRAIN_MEMBER, training),
            (PREPARER.CORPUS_MEMBER, corpus),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            bundle.addfile(member, io.BytesIO(content))
    archive_bytes = archive.read_bytes()
    manifest = {
        "dataset": "SciFact fixture",
        "repository_commit": "a" * 40,
        "repository_tree": "b" * 40,
        "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "archive_size": len(archive_bytes),
        "files": {
            PREPARER.TRAIN_MEMBER: {
                "sha256": hashlib.sha256(training).hexdigest(),
                "size": len(training),
                "records": 1,
            },
            PREPARER.CLAIMS_MEMBER: {
                "sha256": hashlib.sha256(claims).hexdigest(),
                "size": len(claims),
                "records": 3,
            },
            PREPARER.CORPUS_MEMBER: {
                "sha256": hashlib.sha256(corpus).hexdigest(),
                "size": len(corpus),
                "records": 2,
            },
        },
        "selection": {
            "support_cases": 1,
            "contradict_cases": 1,
            "insufficient_evidence_cases": 1,
        },
        "licenses": {},
        "limitations": [],
    }
    manifest_path = tmp_path / "upstream.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return archive, manifest_path


def test_preparer_retains_exact_quotes_and_reviewed_nei_sources(tmp_path: Path) -> None:
    archive, manifest = _fixture(tmp_path)
    output = tmp_path / "output"

    emitted = PREPARER.prepare(archive, manifest, output)
    cases = [json.loads(line) for line in (output / "cases.jsonl").read_text().splitlines()]

    assert emitted["selected_claim_ids"] == [1, 2, 3]
    assert cases[0]["expected_audit"]["documents"][0]["rationales"] == [
        {"quote": "Exact alpha quote.", "sentence_index": 0}
    ]
    assert cases[1]["expected_audit"]["status"] == "contradicted"
    assert cases[2]["expected_audit"]["documents"] == []
    assert cases[2]["expected_audit"]["sources"] == [{"document_id": "10", "title": "Alpha"}]
    assert (output / "corpus.jsonl").read_bytes() == corpus_bytes_from_archive(archive)


def corpus_bytes_from_archive(archive: Path) -> bytes:
    with tarfile.open(archive, "r:gz") as bundle:
        stream = bundle.extractfile(PREPARER.CORPUS_MEMBER)
        assert stream is not None
        return stream.read()


def test_preparer_rejects_archive_tampering_before_creating_output(tmp_path: Path) -> None:
    archive, manifest = _fixture(tmp_path)
    archive.write_bytes(archive.read_bytes() + b"tampered")
    output = tmp_path / "output"

    with pytest.raises(ValueError, match="archive size mismatch"):
        PREPARER.prepare(archive, manifest, output)

    assert not output.exists()
