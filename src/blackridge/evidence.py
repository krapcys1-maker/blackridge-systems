"""Raw probe evidence and explicit human review records."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from blackridge.models import EvidenceLevel


class ManualVerdict(StrEnum):
    """A verdict deliberately entered by a named reviewer."""

    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"


class ProbeEvidence(BaseModel):
    """Facts collected by a probe; intentionally contains no pass/fail field."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    probe_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    observed_at: datetime
    provider: str
    subject: str
    request: dict[str, Any]
    observations: dict[str, Any]
    sources: list[str] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def failure(
        cls,
        *,
        provider: str,
        subject: str,
        request: dict[str, Any],
        sources: list[str],
        error: Exception,
    ) -> ProbeEvidence:
        return cls(
            probe_id=uuid4().hex,
            observed_at=datetime.now(UTC),
            provider=provider,
            subject=subject,
            request=request,
            observations={
                "probe_completed": False,
                "error_type": type(error).__name__,
                "error": str(error),
            },
            sources=sources,
            warnings=["The probe failed; requested observations were not collected."],
        )


class EvidencePromotion(BaseModel):
    """The exact subject bindings reviewed when promoting evidence above L0."""

    model_config = ConfigDict(extra="forbid")

    target_level: EvidenceLevel
    subject_type: Literal["component", "adapter"]
    probe_provider: str = Field(min_length=2)
    probe_subject: str = Field(min_length=1)
    probe_completed: Literal[True]
    subject_revision: str = Field(min_length=7)
    subject_license_spdx: str = Field(min_length=2)
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ManualReview(BaseModel):
    """A manual comparison of raw evidence with one acceptance scenario."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    reviewed_at: datetime
    reviewer: str = Field(min_length=2)
    verdict: ManualVerdict
    capability_id: str
    scenario_id: str
    scenario_description: str
    expected: list[str] = Field(min_length=1)
    observed: list[str] = Field(min_length=1)
    probe_id: str
    probe_file: str
    probe_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    promotion: EvidencePromotion | None = None
    notes: str = Field(min_length=10)

    @classmethod
    def create(cls, **values: object) -> ManualReview:
        payload = {"reviewed_at": datetime.now(UTC), **values}
        return cls.model_validate(payload)
