#!/usr/bin/env python3
"""Inventory one hash-locked wheel without extracting archive members."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import sys
import zipfile
from collections import Counter
from email.parser import BytesParser
from email.policy import default
from pathlib import Path, PurePosixPath
from typing import Any

MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_LICENSE_BYTES = 16 * 1024 * 1024
MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_RECORDED_MEMBER_BYTES = 512 * 1024 * 1024


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _unsafe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        not name
        or "\\" in name
        or name.startswith("/")
        or path.is_absolute()
        or (bool(path.parts) and path.parts[0].endswith(":"))
        or ".." in path.parts
    )


def _read_bounded(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    limit: int,
    label: str,
) -> bytes:
    if info.file_size > limit:
        raise ValueError(
            f"{label} exceeds the {limit}-byte uncompressed size limit: "
            f"{info.filename} ({info.file_size} bytes)"
        )
    return archive.read(info)


def _record_errors(
    archive: zipfile.ZipFile,
    infos: list[zipfile.ZipInfo],
    record_info: zipfile.ZipInfo,
) -> list[str]:
    record_bytes = _read_bounded(
        archive,
        record_info,
        limit=MAX_RECORD_BYTES,
        label="wheel RECORD",
    )
    try:
        record_text = record_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        return [f"RECORD is not UTF-8: {error}"]
    rows = list(csv.reader(io.StringIO(record_text, newline="")))
    info_by_name = {info.filename: info for info in infos if not info.is_dir()}
    record_parent = PurePosixPath(record_info.filename).parent
    deprecated_signatures = {
        str(record_parent / "RECORD.jws"),
        str(record_parent / "RECORD.p7s"),
    }
    seen: set[str] = set()
    errors: list[str] = []
    for row_number, row in enumerate(rows, start=1):
        if len(row) != 3:
            errors.append(f"RECORD row {row_number} has {len(row)} columns instead of 3")
            continue
        path, hash_field, size_field = row
        if path in seen:
            errors.append(f"RECORD contains duplicate path: {path}")
            continue
        seen.add(path)
        if path in deprecated_signatures:
            errors.append(f"RECORD must not mention deprecated signature file: {path}")
            continue
        info = info_by_name.get(path)
        if info is None:
            errors.append(f"RECORD references a missing archive member: {path}")
            continue
        if path == record_info.filename:
            if hash_field or size_field:
                errors.append("RECORD must leave its own hash and size empty")
            continue
        if not hash_field or not size_field:
            errors.append(f"RECORD entry lacks hash or size: {path}")
            continue
        algorithm, separator, encoded_digest = hash_field.partition("=")
        if separator != "=" or not encoded_digest:
            errors.append(f"RECORD entry has an invalid hash field: {path}")
            continue
        try:
            digest = hashlib.new(algorithm)
        except ValueError:
            errors.append(f"RECORD entry uses an unsupported hash algorithm: {path}")
            continue
        if algorithm.lower() in {"md5", "sha1"} or digest.digest_size < 32:
            errors.append(f"RECORD entry uses a hash weaker than sha256: {path}")
            continue
        try:
            expected_size = int(size_field)
        except ValueError:
            errors.append(f"RECORD entry has a non-integer size: {path}")
            continue
        if expected_size != info.file_size:
            errors.append(
                f"RECORD size mismatch for {path}: expected {expected_size}, "
                f"archive reports {info.file_size}"
            )
            continue
        if info.file_size > MAX_RECORDED_MEMBER_BYTES:
            errors.append(f"RECORD member exceeds the verification size limit: {path}")
            continue
        observed_size = 0
        with archive.open(info) as stream:
            while chunk := stream.read(1024 * 1024):
                observed_size += len(chunk)
                if observed_size > MAX_RECORDED_MEMBER_BYTES:
                    break
                digest.update(chunk)
        if observed_size != expected_size:
            errors.append(
                f"RECORD streamed size mismatch for {path}: expected {expected_size}, "
                f"read {observed_size}"
            )
            continue
        observed_digest = base64.urlsafe_b64encode(digest.digest()).rstrip(b"=").decode("ascii")
        if observed_digest != encoded_digest:
            errors.append(f"RECORD sha256 mismatch for: {path}")
    for unrecorded in sorted(set(info_by_name) - seen - deprecated_signatures):
        errors.append(f"archive member is absent from RECORD: {unrecorded}")
    return errors


def _license_kind(path: str) -> str | None:
    name = PurePosixPath(path).name.upper()
    if name.startswith("THIRD_PARTY_NOTICES"):
        return "THIRD_PARTY_NOTICES"
    if name.startswith("LICENSE"):
        return "LICENSE"
    if name.startswith("NOTICE"):
        return "NOTICE"
    return None


def inspect_wheel(request: dict[str, Any], wheel_path: Path) -> dict[str, Any]:
    wheel_sha256 = _sha256_file(wheel_path)
    with zipfile.ZipFile(wheel_path) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        counts = Counter(names)
        duplicate_names = sorted(name for name, count in counts.items() if count > 1)
        unsafe_paths = sorted(name for name in names if _unsafe_member(name))
        metadata_names = sorted(
            name for name in names if name.endswith(".dist-info/METADATA")
        )
        record_infos = [
            info for info in infos if info.filename.endswith(".dist-info/RECORD")
        ]
        if len(metadata_names) != 1:
            raise ValueError(
                f"wheel must contain exactly one dist-info/METADATA; found {len(metadata_names)}"
            )
        if len(record_infos) != 1:
            raise ValueError(
                f"wheel must contain exactly one dist-info/RECORD; found {len(record_infos)}"
            )
        metadata_info = next(info for info in infos if info.filename == metadata_names[0])
        metadata_bytes = _read_bounded(
            archive,
            metadata_info,
            limit=MAX_METADATA_BYTES,
            label="wheel metadata",
        )
        metadata = BytesParser(policy=default).parsebytes(metadata_bytes)
        record_errors = _record_errors(archive, infos, record_infos[0])
        license_files = []
        for info in sorted(infos, key=lambda item: item.filename):
            kind = _license_kind(info.filename)
            if kind is None:
                continue
            content = _read_bounded(
                archive,
                info,
                limit=MAX_LICENSE_BYTES,
                label="wheel license file",
            )
            license_files.append(
                {
                    "path": info.filename,
                    "kind": kind,
                    "sha256": _sha256_bytes(content),
                    "size": len(content),
                }
            )
    return {
        "schema_version": "1",
        "request_id": request["request_id"],
        "artifact": {
            "filename": wheel_path.name,
            "sha256": wheel_sha256,
            "size": wheel_path.stat().st_size,
        },
        "metadata": {
            "name": metadata.get("Name", ""),
            "version": metadata.get("Version", ""),
            "license": metadata.get("License-Expression") or metadata.get("License") or "",
            "requires_python": metadata.get("Requires-Python", ""),
            "requires_dist": metadata.get_all("Requires-Dist", []),
            "provides_extra": metadata.get_all("Provides-Extra", []),
            "metadata_path": metadata_names[0],
            "metadata_sha256": _sha256_bytes(metadata_bytes),
        },
        "archive": {
            "member_count": len(names),
            "members": sorted(names),
            "duplicate_names": duplicate_names,
            "unsafe_paths": unsafe_paths,
            "record_path": record_infos[0].filename,
            "record_valid": not record_errors,
            "record_errors": record_errors,
        },
        "license_files": license_files,
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: wheel_inspector.py ARTIFACT.whl")
    request = json.load(sys.stdin)
    print(
        json.dumps(
            inspect_wheel(request, Path(sys.argv[1])),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
