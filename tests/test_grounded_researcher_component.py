from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from blackridge.process_boundary import run_bounded

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "components" / "grounded_researcher_v1"
CANDIDATE = COMPONENT / "grounded_researcher.py"
FIXTURES = COMPONENT / "fixtures"


@pytest.mark.parametrize(
    ("fixture", "expected_sources", "excluded_sources"),
    [
        (
            "policy-answerable.json",
            {
                "policy-identity",
                "policy-approval",
                "policy-rollback",
                "policy-negative",
                "policy-audit",
            },
            {"garden-compost", "kitchen-bread", "keyword-stuffing"},
        ),
        (
            "backup-answerable.json",
            {
                "backup-snapshot",
                "backup-restore",
                "backup-offsite",
                "backup-review",
            },
            {"recipe-soup", "fitness-recovery"},
        ),
    ],
)
def test_component_selects_grounded_topic_cluster(
    fixture: str, expected_sources: set[str], excluded_sources: set[str]
) -> None:
    request = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))

    result = run_bounded(
        [sys.executable, str(CANDIDATE)],
        input_text=json.dumps(request),
        timeout_seconds=10,
        maximum_output_bytes_per_stream=100_000,
    )

    assert result.exit_code == 0
    assert result.stderr == ""
    output = json.loads(result.stdout)
    source_ids = {item["document_id"] for item in output["sources"]}
    documents = {item["document_id"]: item for item in request["documents"]}
    assert output["status"] == "answered"
    assert source_ids == expected_sources
    assert source_ids.isdisjoint(excluded_sources)
    assert len(output["claims"]) == len(expected_sources)
    assert {
        citation["document_id"] for claim in output["claims"] for citation in claim["citations"]
    } == expected_sources
    assert all(
        citation["quote"] in documents[citation["document_id"]]["full_text"]
        for claim in output["claims"]
        for citation in claim["citations"]
    )


def test_component_abstains_when_corpus_is_unrelated() -> None:
    request = json.loads((FIXTURES / "astronomy-insufficient.json").read_text(encoding="utf-8"))

    result = run_bounded(
        [sys.executable, str(CANDIDATE)],
        input_text=json.dumps(request),
        timeout_seconds=10,
        maximum_output_bytes_per_stream=100_000,
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["request_id"] == request["request_id"]
    assert output["status"] == "insufficient-evidence"
    assert output["claims"] == []
    assert output["sources"] == []
