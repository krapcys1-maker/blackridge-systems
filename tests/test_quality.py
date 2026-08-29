from __future__ import annotations

from blackridge.quality import OpenSSFScorecardClient


def test_scorecard_absence_is_an_explicit_observation() -> None:
    observation = OpenSSFScorecardClient(fetch=lambda _name: None).inspect("example/repo")

    assert observation.score is None
    assert observation.status == "not-found"
    assert "no Scorecard" in observation.detail


def test_scorecard_out_of_range_value_is_not_promoted() -> None:
    observation = OpenSSFScorecardClient(fetch=lambda _name: 11.0).inspect("example/repo")

    assert observation.score is None
    assert observation.status == "invalid-response"


def test_injected_scorecard_exception_becomes_an_error_observation() -> None:
    def fail(_name: str) -> float | None:
        raise RuntimeError("provider unavailable")

    observation = OpenSSFScorecardClient(fetch=fail).inspect("example/repo")

    assert observation.score is None
    assert observation.status == "error"
    assert observation.detail == (
        "injected Scorecard provider failed: RuntimeError: provider unavailable"
    )
