from __future__ import annotations

import pytest
from pydantic import ValidationError

from blackridge.errors import ExternalToolError
from blackridge.evidence import EvidencePromotion, ManualReview, ManualVerdict, ProbeEvidence


def test_manual_review_requires_concrete_observations() -> None:
    with pytest.raises(ValidationError):
        ManualReview.create(
            reviewer="manual-reviewer",
            verdict=ManualVerdict.PASS,
            capability_id="ecosystem-intelligence",
            scenario_id="paper-qa-package-evidence",
            scenario_description="Inspect a real package and its dependency evidence.",
            expected=["A selected version is present."],
            observed=[],
            probe_id="a" * 32,
            probe_file="evidence.json",
            probe_sha256="b" * 64,
            notes="The result was inspected manually.",
        )


def test_failed_probe_retains_error_without_assigning_verdict() -> None:
    probe = ProbeEvidence.failure(
        provider="deps.dev-v3",
        subject="pypi:missing@default",
        request={"system": "pypi", "name": "missing", "version": None},
        sources=["https://api.deps.dev/v3/systems/pypi/packages/missing"],
        error=ExternalToolError("HTTP 404"),
    )

    assert probe.observations["probe_completed"] is False
    assert probe.observations["error_type"] == "ExternalToolError"
    assert "verdict" not in probe.model_dump()


def test_evidence_promotion_requires_a_completed_probe() -> None:
    with pytest.raises(ValidationError):
        EvidencePromotion(
            target_level=3,
            subject_type="component",
            probe_provider="component-contract-probe/v1",
            probe_subject="fixture-component",
            probe_completed=False,
            subject_revision="a" * 40,
            subject_license_spdx="MIT",
            artifact_sha256="b" * 64,
        )
