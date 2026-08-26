"""Raw probe evidence and explicit human review records."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class ManualVerdict(StrEnum):
    """A verdict deliberately entered by a named reviewer."""

    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"


class ProbeEvidence(BaseModel):
    """Facts collected by a probe; intentionally contains no pass/fail field."""

    schema_version: Literal["1"] = "1"
    probe_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    observed_at: datetime
    provider: str
    subject: str
    request: dict[str, object]
    observations: dict[str, object]
    sources: list[str] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def failure(
        cls,
        *,
        provider: str,
        subject: str,
        request: dict[str, object],
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
            warnings=["The probe failed; no package facts were collected."],
        )


class ManualReview(BaseModel):
    """A manual comparison of raw evidence with one acceptance scenario."""

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
    notes: str = Field(min_length=10)

    @classmethod
    def create(cls, **values: object) -> ManualReview:
        return cls(reviewed_at=datetime.now(UTC), **values)
