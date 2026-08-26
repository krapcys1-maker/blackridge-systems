"""Typed contracts for discovery, evidence, scoring, and blueprints."""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceLevel(IntEnum):
    """How strongly a claim about a candidate has been verified."""

    DISCOVERED = 0
    INSPECTED = 1
    BOOTED = 2
    CONTRACT_TESTED = 3
    SYSTEM_VERIFIED = 4


class SearchQuery(StrictModel):
    """One repository search. Keywords are ANDed by the upstream provider."""

    keywords: list[str] = Field(min_length=1)
    language: str | None = None
    stars: str | None = None
    updated: str | None = None
    license: str | None = None

    @field_validator("keywords")
    @classmethod
    def non_empty_keywords(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned:
            raise ValueError("at least one non-empty keyword is required")
        return cleaned


class AcceptanceScenario(StrictModel):
    """Observable behavior that must be checked against concrete evidence."""

    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(min_length=10)
    given: str = Field(min_length=3)
    when: str = Field(min_length=3)
    then: list[str] = Field(min_length=1)

    @field_validator("then")
    @classmethod
    def non_empty_expectations(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned:
            raise ValueError("at least one non-empty expected observation is required")
        return cleaned


class Capability(StrictModel):
    """A replaceable unit of functionality with explicit data contracts."""

    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(min_length=10)
    accepts: list[str] = Field(default_factory=list)
    produces: list[str] = Field(default_factory=list)
    searches: list[SearchQuery] = Field(min_length=1)
    seeds: list[str] = Field(default_factory=list)
    acceptance: list[AcceptanceScenario] = Field(default_factory=list)
    required: bool = True

    @field_validator("seeds")
    @classmethod
    def valid_seed_repositories(cls, value: list[str]) -> list[str]:
        import re

        pattern = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
        invalid = [repository for repository in value if not pattern.fullmatch(repository)]
        if invalid:
            raise ValueError(f"invalid seed repository names: {', '.join(invalid)}")
        return value

    @field_validator("acceptance")
    @classmethod
    def unique_acceptance_ids(cls, value: list[AcceptanceScenario]) -> list[AcceptanceScenario]:
        ids = [scenario.id for scenario in value]
        if len(ids) != len(set(ids)):
            raise ValueError("acceptance scenario ids must be unique within a capability")
        return value


class SystemRequest(StrictModel):
    """Desired outcome already decomposed into capabilities."""

    schema_version: Literal["1"] = "1"
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    goal: str = Field(min_length=20)
    constraints: dict[str, object] = Field(default_factory=dict)
    capabilities: list[Capability] = Field(min_length=1)

    @field_validator("capabilities")
    @classmethod
    def unique_capability_ids(cls, value: list[Capability]) -> list[Capability]:
        ids = [capability.id for capability in value]
        if len(ids) != len(set(ids)):
            raise ValueError("capability ids must be unique")
        return value


class RepositoryMetadata(StrictModel):
    """Normalized repository facts collected from upstream providers."""

    full_name: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    url: str
    description: str | None = None
    stars: int = Field(default=0, ge=0)
    forks: int = Field(default=0, ge=0)
    open_issues: int = Field(default=0, ge=0)
    pushed_at: datetime | None = None
    updated_at: datetime | None = None
    created_at: datetime | None = None
    language: str | None = None
    topics: list[str] = Field(default_factory=list)
    license_spdx: str | None = None
    archived: bool = False
    is_fork: bool = False
    default_branch: str | None = None
    latest_release: str | None = None
    security_score: float | None = Field(default=None, ge=0, le=10)


class ScoreBreakdown(StrictModel):
    """Transparent L0 ranking. It is not a functional verification score."""

    search_fit: float = Field(ge=0, le=25)
    maintenance: float = Field(ge=0, le=20)
    adoption: float = Field(ge=0, le=10)
    community: float = Field(ge=0, le=5)
    issue_health: float = Field(ge=0, le=5)
    license_confidence: float = Field(ge=0, le=15)
    security_posture: float = Field(ge=0, le=20)
    total: float = Field(ge=0, le=100)


CandidateDecision = Literal["eligible-for-inspection", "manual-review", "rejected"]


class Candidate(StrictModel):
    """A repository candidate and the current evidence supporting it."""

    capability_id: str
    metadata: RepositoryMetadata
    search_query: SearchQuery
    search_position: int = Field(ge=1)
    evidence_level: EvidenceLevel = EvidenceLevel.DISCOVERED
    decision: CandidateDecision
    selection_ready: bool = False
    score: ScoreBreakdown
    warnings: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class CapabilityResult(StrictModel):
    capability: Capability
    candidates: list[Candidate] = Field(default_factory=list)


class DiscoveryRun(StrictModel):
    schema_version: Literal["1"] = "1"
    created_at: datetime
    provider: str
    request: SystemRequest
    results: list[CapabilityResult]
    warnings: list[str] = Field(default_factory=list)


class BlueprintComponent(StrictModel):
    capability_id: str
    repository: str | None
    alternatives: list[str] = Field(default_factory=list)
    status: Literal["provisional", "no-candidate"]
    current_evidence_level: EvidenceLevel | None
    required_evidence_level: EvidenceLevel = EvidenceLevel.BOOTED
    accepts: list[str]
    produces: list[str]
    warnings: list[str] = Field(default_factory=list)


class SystemBlueprint(StrictModel):
    schema_version: Literal["1"] = "1"
    generated_at: datetime
    system_name: str
    goal: str
    release_ready: bool = False
    components: list[BlueprintComponent]
    next_gate: str = "inspect-license-security-and-boot-in-sandbox"
