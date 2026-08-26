"""Octocode adapter used for repository discovery without reimplementing search."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from blackridge.errors import ExternalToolError
from blackridge.models import Capability, RepositoryMetadata, SearchQuery
from blackridge.runner import CommandRunner

DEFAULT_OCTOCODE_PACKAGE = "octocode@18.3.0"


class DiscoveryHit:
    """Provider-neutral hit plus the query and ordering that produced it."""

    def __init__(self, metadata: RepositoryMetadata, query: SearchQuery, position: int) -> None:
        self.metadata = metadata
        self.query = query
        self.position = position


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    if len(normalized) == 10:
        normalized += "T00:00:00+00:00"
    parsed = datetime.fromisoformat(normalized)
    return parsed.replace(tzinfo=parsed.tzinfo or UTC)


class OctocodeDiscovery:
    """Calls Octocode's ghSearchRepos tool and normalizes its compact JSON output."""

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        package: str = DEFAULT_OCTOCODE_PACKAGE,
        execute: Callable[[list[str]], str] | None = None,
    ) -> None:
        self.runner = runner or CommandRunner()
        self.package = package
        self._execute = execute

    @property
    def provider_name(self) -> str:
        return f"octocode-cli:{self.package}"

    def search(self, capability: Capability, *, limit: int = 10) -> list[DiscoveryHit]:
        deduplicated: dict[str, DiscoveryHit] = {}
        for query in capability.searches:
            for position, metadata in enumerate(self._search_query(query, limit=limit), start=1):
                key = metadata.full_name.lower()
                existing = deduplicated.get(key)
                if existing is None or position < existing.position:
                    deduplicated[key] = DiscoveryHit(
                        metadata=metadata,
                        query=query,
                        position=position,
                    )
        seed_query = capability.searches[0]
        for repository in capability.seeds:
            key = repository.lower()
            if key not in deduplicated:
                deduplicated[key] = DiscoveryHit(
                    metadata=RepositoryMetadata(
                        full_name=repository,
                        url=f"https://github.com/{repository}",
                    ),
                    query=seed_query,
                    position=1,
                )
        return list(deduplicated.values())

    def _search_query(self, query: SearchQuery, *, limit: int) -> list[RepositoryMetadata]:
        payload: dict[str, Any] = {
            "keywords": query.keywords,
            "concise": False,
            "limit": limit,
            "sort": "best-match",
            "archived": False,
        }
        for key in ("language", "stars", "updated", "license"):
            value = getattr(query, key)
            if value is not None:
                payload[key] = value

        argv = [
            "npx",
            "-y",
            self.package,
            "tools",
            "ghSearchRepos",
            "--queries",
            json.dumps(payload, separators=(",", ":")),
            "--compact",
        ]
        raw = self._execute(argv) if self._execute else self.runner.run(argv).stdout
        return self._parse_response(raw)

    @staticmethod
    def _parse_response(raw: str) -> list[RepositoryMetadata]:
        try:
            envelope = json.loads(raw)
            result = envelope["results"][0]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ExternalToolError("Octocode returned an invalid JSON envelope") from exc

        if result.get("status") == "empty":
            return []
        try:
            repositories = result["data"]["repositories"]
        except (KeyError, TypeError) as exc:
            raise ExternalToolError("Octocode response did not contain repositories") from exc

        normalized: list[RepositoryMetadata] = []
        for repository in repositories:
            if not isinstance(repository, dict):
                raise ExternalToolError("Octocode concise output cannot be used for scoring")
            full_name = f"{repository['owner']}/{repository['repo']}"
            normalized.append(
                RepositoryMetadata(
                    full_name=full_name,
                    url=f"https://github.com/{full_name}",
                    description=repository.get("description"),
                    stars=repository.get("stars", 0),
                    forks=repository.get("forks", 0),
                    open_issues=repository.get("openIssuesCount", 0),
                    pushed_at=_parse_datetime(repository.get("pushedAt")),
                    updated_at=_parse_datetime(repository.get("updatedAt")),
                    created_at=_parse_datetime(repository.get("createdAt")),
                    topics=repository.get("topics") or [],
                )
            )
        return normalized
