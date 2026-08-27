"""Prepare the hash-locked SciFact calibration workload without network access."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

CLAIMS_MEMBER = "data/claims_dev.jsonl"
TRAIN_MEMBER = "data/claims_train.jsonl"
CORPUS_MEMBER = "data/corpus.jsonl"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _load_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), f"{path} must contain a JSON object")
    return value


def _read_verified_member(
    archive: tarfile.TarFile,
    member_name: str,
    declaration: dict[str, Any],
) -> bytes:
    matching = [member for member in archive.getmembers() if member.name == member_name]
    _require(len(matching) == 1, f"archive must contain exactly one {member_name!r} member")
    member = matching[0]
    _require(member.isfile(), f"archive member {member_name!r} must be a regular file")
    extracted = archive.extractfile(member)
    if extracted is None:
        raise ValueError(f"cannot read archive member {member_name!r}")
    data = extracted.read()
    _require(len(data) == declaration["size"], f"size mismatch for {member_name!r}")
    _require(_sha256(data) == declaration["sha256"], f"SHA-256 mismatch for {member_name!r}")
    return data


def _parse_jsonl(data: bytes, source: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(data.decode("utf-8").splitlines(), start=1):
        _require(bool(line.strip()), f"blank JSONL record in {source}:{line_number}")
        value = json.loads(line)
        _require(isinstance(value, dict), f"record in {source}:{line_number} must be an object")
        records.append(value)
    return records


def _unique_by_integer_id(records: Iterable[dict[str, Any]], key: str, source: str) -> None:
    seen: set[int] = set()
    for record in records:
        identifier = record.get(key)
        if not isinstance(identifier, int) or isinstance(identifier, bool):
            raise ValueError(f"{source} has a non-integer {key}")
        _require(identifier not in seen, f"duplicate {key}={identifier} in {source}")
        seen.add(identifier)


def _claim_labels(claim: dict[str, Any]) -> set[str]:
    evidence = claim.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError(f"claim {claim.get('id')} has invalid evidence")
    labels: set[str] = set()
    for document_id, document_annotations in evidence.items():
        _require(str(document_id).isdigit(), f"claim {claim.get('id')} has invalid document id")
        if not isinstance(document_annotations, list) or not document_annotations:
            raise ValueError("evidence annotations must be nonempty")
        for annotation in document_annotations:
            _require(isinstance(annotation, dict), "evidence annotation must be an object")
            label = annotation.get("label")
            _require(label in {"SUPPORT", "CONTRADICT"}, f"unsupported evidence label {label!r}")
            labels.add(label)
    return labels


def _validate_corpus(records: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    _unique_by_integer_id(records, "doc_id", CORPUS_MEMBER)
    corpus: dict[int, dict[str, Any]] = {}
    for document in records:
        document_id = document["doc_id"]
        title = document.get("title")
        abstract = document.get("abstract")
        _require(
            isinstance(title, str) and bool(title.strip()), f"document {document_id} has no title"
        )
        if not isinstance(abstract, list):
            raise ValueError(f"document {document_id} has invalid abstract")
        _require(
            all(isinstance(sentence, str) and bool(sentence.strip()) for sentence in abstract),
            f"document {document_id} has an invalid abstract sentence",
        )
        corpus[document_id] = document
    return corpus


def _expected_documents(
    claim: dict[str, Any], corpus: dict[int, dict[str, Any]]
) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    evidence: dict[str, list[dict[str, Any]]] = claim["evidence"]
    for raw_document_id in sorted(evidence, key=int):
        document_id = int(raw_document_id)
        _require(document_id in corpus, f"claim {claim['id']} cites missing document {document_id}")
        source = corpus[document_id]
        labels = {annotation["label"] for annotation in evidence[raw_document_id]}
        _require(
            len(labels) == 1, f"claim {claim['id']} has mixed labels for document {document_id}"
        )
        label = labels.pop()
        sentence_indexes: set[int] = set()
        for annotation in evidence[raw_document_id]:
            sentences = annotation.get("sentences")
            if not isinstance(sentences, list) or not sentences:
                raise ValueError("evidence sentences must be nonempty")
            for sentence_index in sentences:
                _require(
                    isinstance(sentence_index, int) and not isinstance(sentence_index, bool),
                    f"claim {claim['id']} has a non-integer sentence index",
                )
                _require(
                    0 <= sentence_index < len(source["abstract"]),
                    f"claim {claim['id']} has out-of-range sentence {sentence_index}",
                )
                sentence_indexes.add(sentence_index)
        documents.append(
            {
                "document_id": str(document_id),
                "title": source["title"],
                "verdict": "support" if label == "SUPPORT" else "contradict",
                "rationales": [
                    {"sentence_index": index, "quote": source["abstract"][index]}
                    for index in sorted(sentence_indexes)
                ],
            }
        )
    return documents


def _make_case(claim: dict[str, Any], corpus: dict[int, dict[str, Any]]) -> dict[str, Any]:
    claim_id = claim["id"]
    claim_text = claim.get("claim")
    cited_doc_ids = claim.get("cited_doc_ids")
    if not isinstance(claim_text, str) or len(claim_text) < 10:
        raise ValueError(f"claim {claim_id} is invalid")
    if not isinstance(cited_doc_ids, list) or not all(
        isinstance(item, int) and not isinstance(item, bool) for item in cited_doc_ids
    ):
        raise ValueError(f"claim {claim_id} has invalid cited_doc_ids")
    _require(
        len(cited_doc_ids) == len(set(cited_doc_ids)), f"claim {claim_id} repeats cited_doc_ids"
    )
    _require(
        all(document_id in corpus for document_id in cited_doc_ids),
        f"claim {claim_id} cites a document absent from the corpus",
    )

    labels = _claim_labels(claim)
    _require(len(labels) <= 1, f"claim {claim_id} is not a single-label calibration case")
    expected_documents = _expected_documents(claim, corpus)
    if labels == {"SUPPORT"}:
        status = "supported"
    elif labels == {"CONTRADICT"}:
        status = "contradicted"
    else:
        status = "insufficient-evidence"
        _require(not expected_documents, f"NEI claim {claim_id} unexpectedly has evidence")

    request_id = f"scifact-dev-{claim_id}"
    source_document_ids = (
        [int(document["document_id"]) for document in expected_documents]
        if expected_documents
        else cited_doc_ids
    )
    sources = [
        {"document_id": str(document_id), "title": corpus[document_id]["title"]}
        for document_id in source_document_ids
    ]
    return {
        "schema_version": "1",
        "case_id": request_id,
        "upstream_claim_id": claim_id,
        "request": {
            "schema_version": "1",
            "request_id": request_id,
            "claim": claim_text,
            "minimum_evidence_documents": 1,
            "maximum_candidates": 10,
        },
        "expected_audit": {
            "schema_version": "1",
            "request_id": request_id,
            "claim": claim_text,
            "status": status,
            "summary": f"SciFact calibration label: {status}.",
            "documents": expected_documents,
            "sources": sources,
        },
        "upstream_cited_doc_ids": cited_doc_ids,
    }


def _select_cases(
    claims: list[dict[str, Any]], corpus: dict[int, dict[str, Any]], selection: dict[str, Any]
) -> list[dict[str, Any]]:
    _unique_by_integer_id(claims, "id", CLAIMS_MEMBER)
    ordered = sorted(claims, key=lambda claim: claim["id"])
    categories = [
        ("SUPPORT", int(selection["support_cases"])),
        ("CONTRADICT", int(selection["contradict_cases"])),
        ("NEI", int(selection["insufficient_evidence_cases"])),
    ]
    selected: list[dict[str, Any]] = []
    for category, count in categories:
        matching = [
            claim
            for claim in ordered
            if (
                _claim_labels(claim) == {category}
                if category != "NEI"
                else not _claim_labels(claim)
            )
        ]
        _require(len(matching) >= count, f"not enough {category} claims")
        selected.extend(_make_case(claim, corpus) for claim in matching[:count])
    return selected


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _jsonl_bytes(values: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode() for value in values
    )


def prepare(archive_path: Path, manifest_path: Path, output_path: Path) -> dict[str, Any]:
    manifest = _load_json_object(manifest_path)
    archive_bytes = archive_path.read_bytes()
    _require(len(archive_bytes) == manifest["archive_size"], "archive size mismatch")
    _require(_sha256(archive_bytes) == manifest["archive_sha256"], "archive SHA-256 mismatch")

    declarations = manifest["files"]
    with tarfile.open(archive_path, mode="r:gz") as archive:
        claims_bytes = _read_verified_member(archive, CLAIMS_MEMBER, declarations[CLAIMS_MEMBER])
        train_bytes = _read_verified_member(archive, TRAIN_MEMBER, declarations[TRAIN_MEMBER])
        corpus_bytes = _read_verified_member(archive, CORPUS_MEMBER, declarations[CORPUS_MEMBER])

    claims = _parse_jsonl(claims_bytes, CLAIMS_MEMBER)
    train_claims = _parse_jsonl(train_bytes, TRAIN_MEMBER)
    corpus_records = _parse_jsonl(corpus_bytes, CORPUS_MEMBER)
    _require(len(claims) == declarations[CLAIMS_MEMBER]["records"], "claim record count mismatch")
    _require(
        len(train_claims) == declarations[TRAIN_MEMBER]["records"],
        "training claim record count mismatch",
    )
    _unique_by_integer_id(train_claims, "id", TRAIN_MEMBER)
    _require(
        not ({claim["id"] for claim in claims} & {claim["id"] for claim in train_claims}),
        "training and development claim ids overlap",
    )
    _require(
        len(corpus_records) == declarations[CORPUS_MEMBER]["records"],
        "corpus record count mismatch",
    )
    corpus = _validate_corpus(corpus_records)
    cases = _select_cases(claims, corpus, manifest["selection"])
    cases_bytes = _jsonl_bytes(cases)

    emitted = {
        "schema_version": "1",
        "dataset": manifest["dataset"],
        "source": {
            "repository_commit": manifest["repository_commit"],
            "repository_tree": manifest["repository_tree"],
            "archive_sha256": manifest["archive_sha256"],
            "archive_size": manifest["archive_size"],
            "files": declarations,
        },
        "selection": manifest["selection"],
        "selected_claim_ids": [case["upstream_claim_id"] for case in cases],
        "outputs": {
            "cases.jsonl": {
                "sha256": _sha256(cases_bytes),
                "size": len(cases_bytes),
                "records": len(cases),
            },
            "development.jsonl": {
                "sha256": _sha256(claims_bytes),
                "size": len(claims_bytes),
                "records": len(claims),
            },
            "corpus.jsonl": {
                "sha256": _sha256(corpus_bytes),
                "size": len(corpus_bytes),
                "records": len(corpus_records),
            },
            "training.jsonl": {
                "sha256": _sha256(train_bytes),
                "size": len(train_bytes),
                "records": len(train_claims),
            },
        },
        "licenses": manifest["licenses"],
        "limitations": manifest["limitations"],
    }

    output_path.mkdir(parents=True, exist_ok=False)
    (output_path / "cases.jsonl").write_bytes(cases_bytes)
    (output_path / "development.jsonl").write_bytes(claims_bytes)
    (output_path / "corpus.jsonl").write_bytes(corpus_bytes)
    (output_path / "training.jsonl").write_bytes(train_bytes)
    (output_path / "workload-manifest.json").write_bytes(_json_bytes(emitted))
    return emitted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    emitted = prepare(args.archive, args.manifest, args.output)
    print(json.dumps(emitted, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
