from __future__ import annotations

import pytest

from blackridge.depsdev import DepsDevClient, PackageSystem
from blackridge.errors import ExternalToolError


def test_probe_preserves_raw_package_and_graph_facts_without_verdict() -> None:
    def fetch(url: str) -> dict[str, object]:
        if url.endswith("/packages/paper-qa"):
            return {
                "packageKey": {"system": "PYPI", "name": "paper-qa"},
                "versions": [
                    {
                        "versionKey": {
                            "system": "PYPI",
                            "name": "paper-qa",
                            "version": "5.0.0",
                        },
                        "isDefault": True,
                    }
                ],
            }
        if url.endswith("/versions/5.0.0"):
            return {
                "licenses": ["Apache-2.0"],
                "advisoryKeys": [{"id": "GHSA-example"}],
                "relatedProjects": [
                    {
                        "projectKey": {"id": "github.com/Future-House/paper-qa"},
                        "relationType": "SOURCE_REPO",
                        "relationProvenance": "UNVERIFIED_METADATA",
                    }
                ],
            }
        if url.endswith("/versions/5.0.0:dependencies"):
            return {
                "nodes": [
                    {
                        "versionKey": {"name": "paper-qa", "version": "5.0.0"},
                        "relation": "SELF",
                    },
                    {
                        "versionKey": {"name": "pydantic", "version": "2.11.0"},
                        "relation": "DIRECT",
                    },
                ],
                "edges": [{"fromNode": 0, "toNode": 1, "requirement": ">=2"}],
            }
        raise AssertionError(f"unexpected URL: {url}")

    probe = DepsDevClient(fetch=fetch).probe_package(PackageSystem.PYPI, "paper-qa")

    assert probe.subject == "pypi:paper-qa@5.0.0"
    assert probe.observations["selected_version"]["licenses"] == ["Apache-2.0"]
    assert probe.observations["dependency_graph"]["direct_packages"] == [
        {"name": "pydantic", "version": "2.11.0"}
    ]
    assert "verdict" not in probe.model_dump()


def test_probe_records_explicit_graph_unavailability() -> None:
    def fetch(url: str) -> dict[str, object]:
        if "/versions/" in url:
            return {"licenses": []}
        return {
            "packageKey": {"system": "GO", "name": "example/module"},
            "versions": [
                {
                    "versionKey": {
                        "system": "GO",
                        "name": "example/module",
                        "version": "v1.0.0",
                    },
                    "isDefault": True,
                }
            ],
        }

    probe = DepsDevClient(fetch=fetch).probe_package(PackageSystem.GO, "example/module")

    assert probe.observations["dependency_graph"] == {"available": False}
    assert "does not publish resolved graphs" in probe.warnings[0]


def test_probe_rejects_malformed_upstream_objects_with_a_domain_error() -> None:
    responses = iter(
        [
            {
                "packageKey": "wrong-type",
                "versions": [{"versionKey": {"version": "1"}, "isDefault": True}],
            },
            {"licenses": []},
        ]
    )

    with pytest.raises(ExternalToolError, match="non-object packageKey"):
        DepsDevClient(fetch=lambda _url: next(responses)).probe_package(PackageSystem.GO, "demo")
