from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from blackridge.errors import BlackridgeError
from blackridge.release_compliance import (
    _docker_user_args,
    _license_analysis,
    _safe_extract_wheel,
    load_distribution_manifest,
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


def test_image_probe_refuses_mutable_tag_before_invoking_docker(tmp_path: Path) -> None:
    with pytest.raises(BlackridgeError, match="immutable image"):
        probe_image_release(
            "blackridge/swerex-runtime:latest",
            project_manifest(),
            output_dir=tmp_path,
        )


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
