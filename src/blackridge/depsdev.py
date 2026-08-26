"""deps.dev package and dependency evidence adapter."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import quote
from uuid import uuid4

import httpx

from blackridge.errors import ConfigurationError, ExternalToolError
from blackridge.evidence import ProbeEvidence


class PackageSystem(StrEnum):
    GO = "go"
    RUBYGEMS = "rubygems"
    NPM = "npm"
    CARGO = "cargo"
    MAVEN = "maven"
    PYPI = "pypi"
    NUGET = "nuget"


RESOLVED_GRAPH_SYSTEMS = {
    PackageSystem.NPM,
    PackageSystem.CARGO,
    PackageSystem.MAVEN,
    PackageSystem.PYPI,
}


def _object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ExternalToolError(f"deps.dev returned a non-object {context}")
    return value


def _list(value: object, context: str) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ExternalToolError(f"deps.dev returned a non-list {context}")
    return value


class DepsDevClient:
    """Collect raw package facts without converting them into an approval verdict."""

    base_url = "https://api.deps.dev/v3"

    def __init__(
        self,
        *,
        fetch: Callable[[str], dict[str, object] | None] | None = None,
        timeout_seconds: float = 20,
    ) -> None:
        self._fetch = fetch
        self.timeout_seconds = timeout_seconds

    def _get(self, url: str, *, allow_not_found: bool = False) -> dict[str, object] | None:
        if self._fetch:
            return self._fetch(url)
        try:
            response = httpx.get(url, timeout=self.timeout_seconds, follow_redirects=True)
            if allow_not_found and response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise ExternalToolError(
                f"deps.dev returned HTTP {exc.response.status_code} for {url}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ExternalToolError(f"deps.dev request failed for {url}: {exc}") from exc
        if not isinstance(data, dict):
            raise ExternalToolError(f"deps.dev returned a non-object response for {url}")
        return data

    def probe_package(
        self,
        system: PackageSystem,
        name: str,
        *,
        version: str | None = None,
    ) -> ProbeEvidence:
        """Fetch package, selected version, and resolved dependency facts."""

        normalized_name = name.strip()
        if not normalized_name:
            raise ConfigurationError("package name cannot be empty")

        encoded_name = quote(normalized_name, safe="")
        package_url = f"{self.base_url}/systems/{system.value}/packages/{encoded_name}"
        package = self._get(package_url)
        if package is None:
            raise ExternalToolError(f"deps.dev returned no package object for {package_url}")
        package_key = _object(package.get("packageKey"), "packageKey")
        versions = _list(package.get("versions"), "versions")
        if not isinstance(versions, list) or not versions:
            raise ExternalToolError(
                f"deps.dev returned no versions for {system.value}:{normalized_name}"
            )

        selected = self._select_version(versions, requested=version)
        selected_key = _object(selected.get("versionKey"), "selected versionKey")
        selected_version = selected_key.get("version")
        if not isinstance(selected_version, str) or not selected_version:
            raise ExternalToolError("deps.dev selected version has no usable version identifier")

        encoded_version = quote(selected_version, safe="")
        version_url = f"{package_url}/versions/{encoded_version}"
        version_data = self._get(version_url)
        if version_data is None:
            raise ExternalToolError(f"deps.dev returned no version object for {version_url}")
        licenses = _list(version_data.get("licenses"), "licenses")
        advisory_keys = _list(version_data.get("advisoryKeys"), "advisoryKeys")
        registries = _list(version_data.get("registries"), "registries")

        warnings: list[str] = []
        dependency_summary: dict[str, object]
        dependency_url = f"{version_url}:dependencies"
        sources = [package_url, version_url]
        if system in RESOLVED_GRAPH_SYSTEMS:
            dependency_data = self._get(dependency_url, allow_not_found=True)
            sources.append(dependency_url)
            if dependency_data is None:
                warnings.append("deps.dev has no resolved dependency graph for this version")
                dependency_summary = {"available": False}
            else:
                dependency_summary = self._summarize_dependencies(dependency_data)
        else:
            warnings.append(
                f"deps.dev does not publish resolved graphs for the {system.value} ecosystem"
            )
            dependency_summary = {"available": False}

        observations: dict[str, object] = {
            "package": {
                "system": system.value,
                "requested_name": normalized_name,
                "canonical_name": package_key.get("name"),
                "available_version_count": len(versions),
                "default_version": self._default_version(versions),
            },
            "selected_version": {
                "version": selected_version,
                "published_at": version_data.get("publishedAt") or selected.get("publishedAt"),
                "deprecated": bool(version_data.get("isDeprecated", False)),
                "deprecated_reason": version_data.get("deprecatedReason"),
                "licenses": licenses,
                "advisories": [
                    advisory.get("id")
                    for advisory in advisory_keys
                    if isinstance(advisory, dict)
                ],
                "related_projects": self._related_projects(version_data),
                "registries": registries,
            },
            "dependency_graph": dependency_summary,
        }
        return ProbeEvidence(
            probe_id=uuid4().hex,
            observed_at=datetime.now(UTC),
            provider="deps.dev-v3",
            subject=f"{system.value}:{normalized_name}@{selected_version}",
            request={"system": system.value, "name": normalized_name, "version": version},
            observations=observations,
            sources=sources,
            warnings=warnings,
        )

    def source_urls(
        self, system: PackageSystem, name: str, *, version: str | None = None
    ) -> list[str]:
        encoded_name = quote(name.strip(), safe="")
        package_url = f"{self.base_url}/systems/{system.value}/packages/{encoded_name}"
        urls = [package_url]
        if version:
            urls.append(f"{package_url}/versions/{quote(version, safe='')}")
        return urls

    @staticmethod
    def _select_version(versions: list[object], *, requested: str | None) -> dict[str, object]:
        typed = [item for item in versions if isinstance(item, dict)]
        if requested:
            for item in typed:
                key = item.get("versionKey")
                if isinstance(key, dict) and key.get("version") == requested:
                    return item
            raise ConfigurationError(f"requested package version is not available: {requested}")
        for item in typed:
            if item.get("isDefault"):
                return item
        return typed[-1]

    @staticmethod
    def _default_version(versions: list[object]) -> str | None:
        for item in versions:
            if isinstance(item, dict) and item.get("isDefault"):
                key = item.get("versionKey")
                return str(key.get("version")) if isinstance(key, dict) else None
        return None

    @staticmethod
    def _related_projects(version_data: dict[str, object]) -> list[dict[str, object]]:
        projects: list[dict[str, object]] = []
        for relation in _list(version_data.get("relatedProjects"), "relatedProjects"):
            if not isinstance(relation, dict):
                continue
            project_key = relation.get("projectKey")
            projects.append(
                {
                    "id": project_key.get("id") if isinstance(project_key, dict) else None,
                    "relation_type": relation.get("relationType"),
                    "provenance": relation.get("relationProvenance"),
                }
            )
        return projects

    @staticmethod
    def _summarize_dependencies(data: dict[str, object]) -> dict[str, object]:
        nodes = [
            node for node in _list(data.get("nodes"), "dependency nodes") if isinstance(node, dict)
        ]
        edges = [
            edge for edge in _list(data.get("edges"), "dependency edges") if isinstance(edge, dict)
        ]
        direct = [node for node in nodes if node.get("relation") == "DIRECT"]
        indirect = [node for node in nodes if node.get("relation") == "INDIRECT"]
        node_errors = []
        for node in nodes:
            if not node.get("errors"):
                continue
            version_key = node.get("versionKey")
            node_errors.append(
                {
                    "package": version_key.get("name") if isinstance(version_key, dict) else None,
                    "errors": node.get("errors"),
                }
            )
        direct_packages = [
            {
                "name": node["versionKey"].get("name"),
                "version": node["versionKey"].get("version"),
            }
            for node in direct
            if isinstance(node.get("versionKey"), dict)
        ]
        return {
            "available": True,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "direct_count": len(direct),
            "indirect_count": len(indirect),
            "direct_packages": direct_packages,
            "graph_error": data.get("error"),
            "node_errors": node_errors,
        }
