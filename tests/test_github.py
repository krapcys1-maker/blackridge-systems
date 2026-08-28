from __future__ import annotations

import json

import pytest

from blackridge.errors import ExternalToolError
from blackridge.github import GitHubCli, GitHubSearchDiscovery
from blackridge.models import Capability, RepositoryMetadata, SearchQuery


def test_github_rejects_non_object_and_invalid_repository_metadata() -> None:
    current = RepositoryMetadata(full_name="example/repo", url="https://github.com/example/repo")

    with pytest.raises(ExternalToolError, match="non-object JSON"):
        GitHubCli(execute=lambda _argv: "[]").enrich(current)

    with pytest.raises(ExternalToolError, match="invalid repository metadata"):
        GitHubCli(execute=lambda _argv: '{"stargazers_count": -1, "license": null}').enrich(current)


def test_github_search_discovery_builds_auditable_hits() -> None:
    calls: list[list[str]] = []

    def execute(argv: list[str]) -> str:
        calls.append(argv)
        return json.dumps(
            [
                {
                    "fullName": "example/foundry",
                    "url": "https://github.com/example/foundry",
                    "description": "Composable software foundry",
                    "stargazersCount": 12,
                    "forksCount": 2,
                    "openIssuesCount": 1,
                    "license": {"key": "apache-2.0", "name": "Apache License 2.0"},
                    "isArchived": False,
                    "isFork": False,
                    "defaultBranch": "main",
                    "language": "Python",
                }
            ]
        )

    capability = Capability(
        id="component-discovery",
        description="Find reusable components through an auditable repository search.",
        searches=[SearchQuery(keywords=["software", "foundry"], language="Python")],
    )
    hits = GitHubSearchDiscovery(execute=execute).search(capability, limit=5)
    assert [hit.metadata.full_name for hit in hits] == ["example/foundry"]
    assert hits[0].query.keywords == ["software", "foundry"]
    assert hits[0].metadata.license_key == "apache-2.0"
    assert hits[0].metadata.license_spdx is None
    assert calls[0][:4] == ["gh", "search", "repos", "software foundry"]
    assert "--json" in calls[0]


def test_github_search_discovery_records_a_loosened_query_after_empty_exact_search() -> None:
    calls: list[list[str]] = []

    def execute(argv: list[str]) -> str:
        calls.append(argv)
        if len(calls) == 1:
            return "[]"
        return json.dumps(
            [
                {
                    "fullName": "example/requirements",
                    "url": "https://github.com/example/requirements",
                    "stargazersCount": 1,
                    "forksCount": 0,
                    "openIssuesCount": 0,
                }
            ]
        )

    capability = Capability(
        id="requirements",
        description="Parse requirements into a structured and reviewable representation.",
        searches=[
            SearchQuery(
                keywords=[
                    "natural language requirement parsing",
                    "structured specification extraction",
                ],
                stars=">100",
            )
        ],
    )
    hit = GitHubSearchDiscovery(execute=execute).search(capability, limit=3)[0]
    assert hit.query.keywords == ["natural", "language"]
    assert hit.query.stars is None
    assert calls[1][3] == "natural language"


def test_github_search_discovery_enforces_query_budget() -> None:
    discovery = GitHubSearchDiscovery(execute=lambda _argv: "[]", max_queries=1)
    capability = Capability(
        id="bounded-search",
        description="Search within a deterministic upstream query budget for evidence.",
        searches=[SearchQuery(keywords=["too", "specific", "query"])],
    )
    with pytest.raises(ExternalToolError, match="query budget exhausted"):
        discovery.search(capability, limit=3)


def test_github_search_can_retain_explicitly_partial_budget_results() -> None:
    responses = iter(
        [
            json.dumps(
                [
                    {
                        "fullName": "example/first",
                        "url": "https://github.com/example/first",
                    }
                ]
            )
        ]
    )
    discovery = GitHubSearchDiscovery(
        execute=lambda _argv: next(responses),
        max_queries=1,
        partial_on_budget_exhaustion=True,
    )
    capability = Capability(
        id="bounded-search",
        description="Retain partial search results when the explicit budget is exhausted.",
        searches=[
            SearchQuery(keywords=["first", "query"]),
            SearchQuery(keywords=["second", "query"]),
        ],
    )
    hits = discovery.search(capability, limit=2)
    assert [hit.metadata.full_name for hit in hits] == ["example/first"]
    assert discovery.budget_exhausted is True


def test_github_search_records_empty_queries_and_blocks_denied_repositories() -> None:
    response = json.dumps(
        [
            {
                "fullName": "blocked/repository",
                "url": "https://github.com/blocked/repository",
                "license": {"key": "mit", "name": "MIT License"},
            },
            {
                "fullName": "allowed/repository",
                "url": "https://github.com/allowed/repository",
                "license": {"key": "apache-2.0", "name": "Apache License 2.0"},
            },
        ]
    )
    discovery = GitHubSearchDiscovery(
        execute=lambda _argv: response,
        denied_repositories={"BLOCKED/REPOSITORY"},
    )
    capability = Capability(
        id="bounded-search",
        description="Search while enforcing a case-insensitive denied repository policy.",
        searches=[SearchQuery(keywords=["software", "component"])],
    )
    hits = discovery.search(capability, limit=3)
    assert [hit.metadata.full_name for hit in hits] == ["allowed/repository"]
    assert discovery.executed_queries[0].result_count == 2
    assert discovery.executed_queries[0].argv[3] == "software component"
