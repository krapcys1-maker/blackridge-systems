from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path
from types import ModuleType

from jsonschema import Draft202012Validator

ROOT = Path(__file__).parents[1]


def _module(name: str, relative: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


INSPECTOR = _module(
    "wheel_inspector_component", "components/wheel_inspector_v1/wheel_inspector.py"
)
POLICY = _module("wheel_policy_component", "components/wheel_policy_v1/wheel_policy.py")
SCHEMA_ROOT = ROOT / "benchmarks" / "wheel-release-auditor-v1" / "public"


def _request(wheel: Path) -> dict[str, object]:
    return {
        "schema_version": "1",
        "request_id": "wheel-fixture",
        "expected_wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "expected_project_name": "fixture-project",
        "expected_version": "1.2.3",
        "allowed_project_licenses": ["Apache-2.0"],
        "required_license_kinds": ["LICENSE", "NOTICE", "THIRD_PARTY_NOTICES"],
        "forbidden_dependency_names": ["blocked-package"],
    }


def _wheel(
    path: Path,
    *,
    unsafe_members: tuple[str, ...] = (),
    record_algorithm: str = "sha256",
) -> None:
    metadata = (
        "Metadata-Version: 2.4\n"
        "Name: fixture-project\n"
        "Version: 1.2.3\n"
        "License-Expression: Apache-2.0\n"
        "Requires-Python: >=3.11\n"
        "Requires-Dist: jsonschema==4.26.0\n"
        "Provides-Extra: audit\n\n"
    )
    members = {
        "fixture_project-1.2.3.dist-info/METADATA": metadata.encode(),
        "fixture_project-1.2.3.dist-info/licenses/LICENSE": b"license",
        "fixture_project-1.2.3.dist-info/licenses/NOTICE": b"notice",
        "fixture_project-1.2.3.dist-info/licenses/THIRD_PARTY_NOTICES.md": b"third party",
        **{unsafe_member: b"unsafe" for unsafe_member in unsafe_members},
    }
    record_path = "fixture_project-1.2.3.dist-info/RECORD"
    record_stream = io.StringIO(newline="")
    writer = csv.writer(record_stream, lineterminator="\n")
    for member_name, content in members.items():
        digest = base64.urlsafe_b64encode(
            hashlib.new(record_algorithm, content).digest()
        ).rstrip(b"=")
        writer.writerow((member_name, f"{record_algorithm}={digest.decode()}", len(content)))
    writer.writerow((record_path, "", ""))
    members[record_path] = record_stream.getvalue().encode()
    with zipfile.ZipFile(path, "w") as archive:
        for member_name, content in members.items():
            archive.writestr(member_name, content)


def _validate(schema_name: str, value: object) -> None:
    schema = json.loads((SCHEMA_ROOT / schema_name).read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(value))
    assert not errors, [error.message for error in errors]


def test_wheel_inventory_and_policy_keep_failures_structured(tmp_path: Path) -> None:
    wheel = tmp_path / "fixture_project-1.2.3-py3-none-any.whl"
    _wheel(wheel)
    request = _request(wheel)

    inventory = INSPECTOR.inspect_wheel(request, wheel)
    positive = POLICY.evaluate(
        {"inputs": {"wheel-audit-request/v1": request, "wheel-inventory/v1": inventory}}
    )
    _validate("wheel-inventory.schema.json", inventory)
    _validate("wheel-audit.schema.json", positive)

    assert inventory["archive"]["unsafe_paths"] == []
    assert inventory["archive"]["record_valid"] is True
    assert {item["kind"] for item in inventory["license_files"]} == {
        "LICENSE",
        "NOTICE",
        "THIRD_PARTY_NOTICES",
    }
    assert positive["status"] == "policy-passed"
    assert positive["release_ready"] is False

    blocked_request = dict(request)
    blocked_request["forbidden_dependency_names"] = ["JSONSchema"]
    negative = POLICY.evaluate(
        {
            "inputs": {
                "wheel-audit-request/v1": blocked_request,
                "wheel-inventory/v1": inventory,
            }
        }
    )
    assert negative["status"] == "policy-failed"
    forbidden = next(
        item for item in negative["checks"] if item["check_id"] == "forbidden-dependencies"
    )
    assert forbidden["observed"] == ["jsonschema"]


def test_wheel_inspector_reports_unsafe_member_without_extracting(tmp_path: Path) -> None:
    wheel = tmp_path / "unsafe-1.2.3-py3-none-any.whl"
    _wheel(wheel, unsafe_members=("../escape.py", "C:/drive.py"))

    inventory = INSPECTOR.inspect_wheel(_request(wheel), wheel)

    assert inventory["archive"]["unsafe_paths"] == ["../escape.py", "C:/drive.py"]
    assert not (tmp_path.parent / "escape.py").exists()
    assert not (tmp_path / "C:" / "drive.py").exists()


def test_wheel_inspector_rejects_oversized_metadata(tmp_path: Path) -> None:
    wheel = tmp_path / "oversized-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        info = zipfile.ZipInfo("fixture_project-1.2.3.dist-info/METADATA")
        info.file_size = INSPECTOR.MAX_METADATA_BYTES + 1
        archive.writestr(info, b"Metadata-Version: 2.4\n")
        archive.writestr("fixture_project-1.2.3.dist-info/RECORD", "")

    original_limit = INSPECTOR.MAX_METADATA_BYTES
    INSPECTOR.MAX_METADATA_BYTES = 8
    try:
        try:
            INSPECTOR.inspect_wheel(_request(wheel), wheel)
        except ValueError as error:
            assert "uncompressed size limit" in str(error)
        else:
            raise AssertionError("oversized metadata was accepted")
    finally:
        INSPECTOR.MAX_METADATA_BYTES = original_limit


def test_wheel_policy_reports_record_tampering(tmp_path: Path) -> None:
    wheel = tmp_path / "record-tampered-1.2.3-py3-none-any.whl"
    _wheel(wheel)
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("unrecorded.py", "not in RECORD")
    request = _request(wheel)

    inventory = INSPECTOR.inspect_wheel(request, wheel)
    result = POLICY.evaluate(
        {"inputs": {"wheel-audit-request/v1": request, "wheel-inventory/v1": inventory}}
    )

    assert inventory["archive"]["record_valid"] is False
    assert inventory["archive"]["record_errors"] == [
        "archive member is absent from RECORD: unrecorded.py"
    ]
    failed = [check["check_id"] for check in result["checks"] if not check["passed"]]
    assert failed == ["record-integrity"]


def test_wheel_record_accepts_sha512_and_unrecorded_deprecated_signature(
    tmp_path: Path,
) -> None:
    wheel = tmp_path / "signed-1.2.3-py3-none-any.whl"
    _wheel(wheel, record_algorithm="sha512")
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("fixture_project-1.2.3.dist-info/RECORD.jws", "deprecated")

    inventory = INSPECTOR.inspect_wheel(_request(wheel), wheel)

    assert inventory["archive"]["record_valid"] is True
    assert inventory["archive"]["record_errors"] == []
