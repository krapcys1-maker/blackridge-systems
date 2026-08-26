from __future__ import annotations

import json

from blackridge.models import Capability, SearchQuery
from blackridge.octocode import OctocodeDiscovery


def test_octocode_parses_and_deduplicates_hits() -> None:
    envelope = {
        "results": [
            {
                "id": "ghSearchRepos-1",
                "data": {
                    "repositories": [
                        {
                            "owner": "example",
                            "repo": "parser",
                            "stars": 123,
                            "forks": 12,
                            "openIssuesCount": 3,
                            "description": "A parser",
                            "pushedAt": "2026-08-01",
                            "updatedAt": "2026-08-02",
                            "createdAt": "2024-01-01",
                            "topics": ["parser"],
                        }
                    ]
                },
            }
        ]
    }
    calls: list[list[str]] = []

    def execute(argv: list[str]) -> str:
        calls.append(argv)
        return json.dumps(envelope)

    capability = Capability(
        id="document-parser",
        description="Parse documents into structured evidence.",
        searches=[
            SearchQuery(keywords=["PDF", "parser"]),
            SearchQuery(keywords=["document", "parser"]),
        ],
    )
    provider = OctocodeDiscovery(execute=execute)

    hits = provider.search(capability, limit=5)

    assert len(hits) == 1
    assert hits[0].metadata.full_name == "example/parser"
    assert hits[0].metadata.stars == 123
    assert len(calls) == 2
    assert all("--compact" in call for call in calls)
    assert all(call[0] == "npx" for call in calls)


def test_octocode_adds_curated_seed_without_executing_it() -> None:
    provider = OctocodeDiscovery(
        execute=lambda _argv: json.dumps(
            {"results": [{"id": "ghSearchRepos-1", "status": "empty"}]}
        )
    )
    capability = Capability(
        id="document-parser",
        description="Parse documents into structured evidence.",
        searches=[SearchQuery(keywords=["scientific", "PDF", "parser"])],
        seeds=["grobidOrg/grobid"],
    )

    hits = provider.search(capability)

    assert [hit.metadata.full_name for hit in hits] == ["grobidOrg/grobid"]
    assert hits[0].metadata.stars == 0


def test_octocode_handles_an_empty_search() -> None:
    provider = OctocodeDiscovery(
        execute=lambda _argv: json.dumps(
            {"results": [{"id": "ghSearchRepos-1", "status": "empty"}]}
        )
    )
    capability = Capability(
        id="rare-capability",
        description="A capability with no current implementation.",
        searches=[SearchQuery(keywords=["definitely-no-results"])],
    )

    assert provider.search(capability) == []
