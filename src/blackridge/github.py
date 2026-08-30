"""Official GitHub CLI adapter for metadata enrichment."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from blackridge.errors import ExternalToolError
from blackridge.models import Capability, RepositoryMetadata, SearchQuery
from blackridge.octocode import DiscoveryHit
from blackridge.runner import CommandRunner


@dataclass(frozen=True)
class GitHubSearchExecution:
    """One exact official-CLI query and its normalized result count."""

    query: SearchQuery
    argv: tuple[str, ...]
    result_count: int


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


class GitHubCli:
    """Fetches repository facts through the user's authenticated `gh` installation."""

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        execute: Callable[[list[str]], str] | None = None,
    ) -> None:
        self.runner = runner or CommandRunner()
        self._execute = execute

    def enrich(self, current: RepositoryMetadata) -> RepositoryMetadata:
        argv = ["gh", "api", f"repos/{current.full_name}"]
        raw = self._execute(argv) if self._execute else self.runner.run(argv).stdout
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            message = f"GitHub returned invalid JSON for {current.full_name}"
            raise ExternalToolError(message) from exc

        if not isinstance(data, dict):
            raise ExternalToolError(f"GitHub returned non-object JSON for {current.full_name}")

        license_data = data.get("license")
        if license_data is None:
            license_data = {}
        if not isinstance(license_data, dict):
            raise ExternalToolError(
                f"GitHub returned an invalid license object for {current.full_name}"
            )
        try:
            return RepositoryMetadata.model_validate(
                {
                    **current.model_dump(),
                    "url": data.get("html_url") or current.url,
                    "description": data.get("description") or current.description,
                    "stars": data.get("stargazers_count", current.stars),
                    "forks": data.get("forks_count", current.forks),
                    "open_issues": data.get("open_issues_count", current.open_issues),
                    "pushed_at": _parse_datetime(data.get("pushed_at")) or current.pushed_at,
                    "updated_at": _parse_datetime(data.get("updated_at")) or current.updated_at,
                    "created_at": _parse_datetime(data.get("created_at")) or current.created_at,
                    "language": data.get("language") or current.language,
                    "topics": data.get("topics") or current.topics,
                    "license_key": license_data.get("key") or current.license_key,
                    "license_spdx": license_data.get("spdx_id"),
                    "archived": bool(data.get("archived", False)),
                    "is_fork": bool(data.get("fork", False)),
                    "default_branch": data.get("default_branch"),
                }
            )
        except (TypeError, ValueError) as exc:
            raise ExternalToolError(
                f"GitHub returned invalid repository metadata for {current.full_name}: {exc}"
            ) from exc


class GitHubSearchDiscovery:
    """Search GitHub through the authenticated official CLI without executing repository code."""

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        execute: Callable[[list[str]], str] | None = None,
        max_queries: int | None = None,
        denied_repositories: set[str] | None = None,
        partial_on_budget_exhaustion: bool = False,
    ) -> None:
        if max_queries is not None and max_queries < 1:
            raise ValueError("max_queries must be positive when provided")
        self.runner = runner or CommandRunner()
        self._execute = execute
        self.max_queries = max_queries
        self.queries_used = 0
        self.partial_on_budget_exhaustion = partial_on_budget_exhaustion
        self.budget_exhausted = False
        self.denied_repositories = {
            repository.casefold() for repository in (denied_repositories or set())
        }
        self.executed_queries: list[GitHubSearchExecution] = []

    @property
    def provider_name(self) -> str:
        return "github-cli:search-repos"

    def search(self, capability: Capability, *, limit: int = 10) -> list[DiscoveryHit]:
        if not 1 <= limit <= 100:
            raise ValueError("GitHub search limit must be between 1 and 100")
        deduplicated: dict[str, DiscoveryHit] = {}
        for query in capability.searches:
            if self.budget_exhausted:
                break
            effective_query = query
            repositories = self._search_query(query, limit)
            if not repositories:
                for fallback in self._fallback_queries(query):
                    if self.budget_exhausted:
                        break
                    repositories = self._search_query(fallback, limit)
                    if repositories:
                        effective_query = fallback
                        break
            for position, metadata in enumerate(repositories, start=1):
                key = metadata.full_name.casefold()
                if key in self.denied_repositories:
                    continue
                current = deduplicated.get(key)
                if current is None or position < current.position:
                    deduplicated[key] = DiscoveryHit(metadata, effective_query, position)
        seed_query = capability.searches[0]
        for repository in capability.seeds:
            key = repository.casefold()
            if key in self.denied_repositories:
                continue
            deduplicated.setdefault(
                key,
                DiscoveryHit(
                    RepositoryMetadata(
                        full_name=repository,
                        url=f"https://github.com/{repository}",
                    ),
                    seed_query,
                    1,
                ),
            )
        return list(deduplicated.values())

    @staticmethod
    def _fallback_queries(query: SearchQuery) -> list[SearchQuery]:
        """Loosen an over-constrained model query while retaining the exact executed variant."""

        variants: list[SearchQuery] = []
        seen: set[tuple[str, ...]] = {tuple(item.casefold() for item in query.keywords)}
        for concept in query.keywords:
            tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", concept)
            if not tokens:
                continue
            keywords = tokens[:2]
            key = tuple(item.casefold() for item in keywords)
            if key in seen:
                continue
            seen.add(key)
            variants.append(
                query.model_copy(
                    update={
                        "keywords": keywords,
                        "stars": None,
                        "updated": None,
                        "license": None,
                    }
                )
            )
        return variants[:1]

    def _search_query(self, query: SearchQuery, limit: int) -> list[RepositoryMetadata]:
        if self.max_queries is not None and self.queries_used >= self.max_queries:
            self.budget_exhausted = True
            if self.partial_on_budget_exhaustion:
                return []
            raise ExternalToolError(f"GitHub search query budget exhausted ({self.max_queries})")
        self.queries_used += 1
        argv = ["gh", "search", "repos", " ".join(query.keywords)]
        for flag in ("language", "stars", "updated", "license"):
            value = getattr(query, flag)
            if value:
                argv.extend((f"--{flag}", value))
        argv.extend(
            (
                "--archived=false",
                "--limit",
                str(limit),
                "--json",
                "createdAt,defaultBranch,description,forksCount,fullName,isArchived,isFork,"
                "language,license,openIssuesCount,pushedAt,stargazersCount,updatedAt,url",
            )
        )
        raw = self._execute(argv) if self._execute else self.runner.run(argv).stdout
        try:
            repositories = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ExternalToolError("GitHub search returned invalid JSON") from exc
        if not isinstance(repositories, list):
            raise ExternalToolError("GitHub search returned a non-array response")
        results: list[RepositoryMetadata] = []
        for repository in repositories:
            if not isinstance(repository, dict):
                raise ExternalToolError("GitHub search returned a malformed repository")
            license_data = repository.get("license") or {}
            if not isinstance(license_data, dict):
                license_data = {}
            try:
                results.append(
                    RepositoryMetadata(
                        full_name=repository["fullName"],
                        url=repository["url"],
                        description=repository.get("description"),
                        stars=repository.get("stargazersCount", 0),
                        forks=repository.get("forksCount", 0),
                        open_issues=repository.get("openIssuesCount", 0),
                        pushed_at=_parse_datetime(repository.get("pushedAt")),
                        updated_at=_parse_datetime(repository.get("updatedAt")),
                        created_at=_parse_datetime(repository.get("createdAt")),
                        language=repository.get("language"),
                        license_key=license_data.get("key"),
                        license_spdx=None,
                        archived=bool(repository.get("isArchived", False)),
                        is_fork=bool(repository.get("isFork", False)),
                        default_branch=repository.get("defaultBranch"),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ExternalToolError(
                    "GitHub search returned invalid repository metadata"
                ) from exc
        self.executed_queries.append(
            GitHubSearchExecution(
                query=query,
                argv=tuple(argv),
                result_count=len(results),
            )
        )
        return results
