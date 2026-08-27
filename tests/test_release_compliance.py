from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from blackridge.errors import BlackridgeError
from blackridge.release_compliance import (
    _docker_user_args,
    _license_analysis,
    _requirement_name,
    _runtime_lock_blockers,
    _safe_extract_wheel,
    _verify_python_license_review,
    load_distribution_manifest,
    load_python_license_review,
    probe_image_release,
    probe_wheel_release,
    render_third_party_notices,
)


def project_manifest():
    return load_distribution_manifest(Path("compliance/distribution-manifest.yaml"))


def test_notices_include_active_boundaries_but_not_research_candidates() -> None:
    notices = render_third_party_notices(project_manifest())

    assert "python-json-patch" in notices
    assert "Octocode" in notices
    assert "SWE-ReX runtime image" in notices
    assert "paper-qa" not in notices.casefold()
    assert "does not embed their packages" in notices


def test_docker_user_mapping_is_explicit_on_posix(monkeypatch) -> None:
    fake_os = SimpleNamespace(name="posix", getuid=lambda: 123, getgid=lambda: 456)
    monkeypatch.setattr("blackridge.release_compliance.os", fake_os)

    assert _docker_user_args() == ["--user", "123:456"]


def test_wheel_extraction_rejects_parent_traversal(tmp_path: Path) -> None:
    wheel = tmp_path / "unsafe.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("../outside", "bad")

    with pytest.raises(BlackridgeError, match="unsafe path"):
        _safe_extract_wheel(wheel, tmp_path / "extract")


def test_invalid_wheel_requirement_is_a_controlled_compliance_error() -> None:
    with pytest.raises(BlackridgeError, match="invalid Requires-Dist"):
        _requirement_name("; extra == 'broken'")


def test_wheel_probe_compares_real_metadata_and_packages_license(
    monkeypatch, tmp_path: Path
) -> None:
    wheel = tmp_path / "blackridge_systems-0.1.0-py3-none-any.whl"
    requirements = [
        "httpx>=0.27,<1",
        "jsonpatch==1.33",
        "jsonschema==4.26.0",
        "pydantic>=2.10,<3",
        "PyYAML>=6,<7",
        "rich>=13.9,<15",
        "typer>=0.15,<1",
        "build>=1,<2; extra == 'dev'",
    ]
    metadata = "Metadata-Version: 2.4\nName: blackridge-systems\nVersion: 0.1.0\n"
    metadata += "".join(f"Requires-Dist: {item}\n" for item in requirements)
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("blackridge/__init__.py", "")
        archive.writestr("blackridge_systems-0.1.0.dist-info/METADATA", metadata)
        archive.writestr("blackridge_systems-0.1.0.dist-info/licenses/LICENSE", "Apache-2.0")

    def fake_syft(source, *, scan_mount, output_dir, image):
        del source, scan_mount, image
        (output_dir / "sbom.spdx.json").write_text(
            json.dumps({"packages": [{"name": "blackridge-systems"}]}), encoding="utf-8"
        )
        (output_dir / "sbom.cdx.json").write_text(
            json.dumps({"components": [{"name": "blackridge-systems"}]}), encoding="utf-8"
        )
        return []

    monkeypatch.setattr("blackridge.release_compliance._syft_scan", fake_syft)
    probe = probe_wheel_release(wheel, project_manifest(), output_dir=tmp_path / "out")

    assert probe.observations["release_gate_open"] is True
    assert probe.observations["release_blockers"] == []
    component_manifest = probe.observations["component_manifest"]
    assert component_manifest["optional_requires_dist"] == ["build>=1,<2; extra == 'dev'"]
    assert (tmp_path / "out" / "license-bundle.zip").is_file()


def test_wheel_probe_refuses_a_nonempty_output_directory(tmp_path: Path) -> None:
    wheel = tmp_path / "fixture.whl"
    wheel.write_bytes(b"not inspected because output preflight fails")
    output = tmp_path / "out"
    output.mkdir()
    (output / "stale.json").write_text("{}", encoding="utf-8")

    with pytest.raises(BlackridgeError, match="must be empty"):
        probe_wheel_release(wheel, project_manifest(), output_dir=output)


def test_image_probe_refuses_mutable_tag_before_invoking_docker(tmp_path: Path) -> None:
    with pytest.raises(BlackridgeError, match="immutable image"):
        probe_image_release(
            "blackridge/swerex-runtime:latest",
            project_manifest(),
            output_dir=tmp_path,
        )


def test_image_probe_refuses_stale_output_before_invoking_docker(tmp_path: Path) -> None:
    (tmp_path / "stale.json").write_text("old evidence", encoding="utf-8")

    with pytest.raises(BlackridgeError, match="output directory must be empty"):
        probe_image_release(
            "sha256:" + "a" * 64,
            project_manifest(),
            output_dir=tmp_path,
        )


def test_image_probe_validates_license_review_before_invoking_docker(tmp_path: Path) -> None:
    invalid_review = tmp_path / "invalid-review.yaml"
    invalid_review.write_text(
        "schema_version: '1'\nreviewer: test\nreview_scope: too short\npackages: []\n",
        encoding="utf-8",
    )
    output = tmp_path / "new-output"

    with pytest.raises(ValidationError):
        probe_image_release(
            "sha256:" + "a" * 64,
            project_manifest(),
            output_dir=output,
            license_review_file=invalid_review,
        )

    assert not output.exists()


def test_license_analysis_keeps_unknown_and_review_licenses_separate() -> None:
    analysis = _license_analysis(
        {"packages": [{"name": "unknown", "licenseDeclared": "NOASSERTION"}]},
        [
            {"name": "unknown", "license": "NOASSERTION", "license_files": []},
            {"name": "copy-left", "license": "GPLv3+", "license_files": ["LICENSE"]},
        ],
        [{"binary_package": "base", "copyright_file": None}],
    )

    assert analysis["sbom_without_declared_license_count"] == 1
    assert [item["name"] for item in analysis["python_review_license_packages"]] == ["copy-left"]
    assert len(analysis["python_without_extracted_license_text"]) == 1
    assert len(analysis["os_without_extracted_copyright_file"]) == 1


def test_runtime_lock_blocker_requires_complete_exact_closure() -> None:
    assert _runtime_lock_blockers({"complete": True}) == []
    assert _runtime_lock_blockers({"complete": False}) == [
        "Dockerfile apt or Python dependency closure does not match its embedded locks"
    ]


def test_exact_license_review_resolves_unknown_metadata_but_not_public_approval(
    tmp_path: Path,
) -> None:
    license_file = tmp_path / "python" / "demo-1.0" / "demo-1.0.dist-info" / "LICENSE"
    license_file.parent.mkdir(parents=True)
    license_file.write_text("MIT test license", encoding="utf-8")
    digest = hashlib.sha256(license_file.read_bytes()).hexdigest()
    base_review = load_python_license_review(Path("docker/python-license-review.yaml"))
    review_data = base_review.model_dump()
    review_data["packages"] = [
        {
            "name": "demo",
            "version": "1.0",
            "observed_metadata": "NOASSERTION",
            "concluded_license_spdx": "MIT",
            "requires_public_distribution_review": False,
            "license_files": [{"path": "demo-1.0.dist-info/LICENSE", "sha256": digest}],
            "sources": ["https://example.invalid/demo"],
            "source_archive": {
                "filename": "demo-1.0.tar.gz",
                "url": "https://example.invalid/demo-1.0.tar.gz",
                "sha256": "a" * 64,
            },
        }
    ]
    review = type(base_review).model_validate(review_data)
    result = _verify_python_license_review(
        review,
        [
            {
                "name": "demo",
                "version": "1.0",
                "license": "NOASSERTION",
                "license_files": ["demo-1.0.dist-info/LICENSE"],
            }
        ],
        tmp_path,
    )

    assert result["issues"] == []
    assert result["valid_entry_count"] == 1
    assert result["unresolved_unknown_metadata"] == []
    assert result["public_distribution_ready"] is False


def test_v1_workflow_cannot_publish_internal_runtime_image() -> None:
    policy = Path("docker/distribution-manifest.yaml").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/release-evidence.yml").read_text(encoding="utf-8")

    assert "distribution_mode: internal-build-only" in policy
    assert "public_image_publication: prohibited" in policy
    assert "permissions:\n  contents: read" in workflow
    assert "docker push" not in workflow.casefold()
    assert "docker/login-action" not in workflow.casefold()
    assert 'manifest["runtime_locks"]["complete"] is True' in workflow
    assert 'review["valid_entry_count"] == 6' in workflow
    assert 'observed["release_blockers"] == expected_blockers' in workflow


def test_ci_enforces_format_components_and_seventy_percent_coverage() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "ruff format --check src tests tools components" in workflow
    assert "python -m compileall -q src tests tools components" in workflow
    assert "--cov-fail-under=70" in workflow
