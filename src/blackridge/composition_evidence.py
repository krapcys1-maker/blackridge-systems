"""Typed, hash-bound evidence promotion for composition qualification."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from blackridge.evidence import ManualReview, ManualVerdict, ProbeEvidence
from blackridge.models import EvidenceLevel


class EvidenceReference(BaseModel):
    """A claimed evidence level plus the exact named review supporting it."""

    model_config = ConfigDict(extra="forbid")

    level: EvidenceLevel
    review_file: str | None = None
    review_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    capability_id: str | None = None
    scenario_id: str | None = None
    probe_subject: str | None = None

    @model_validator(mode="after")
    def review_fields_are_complete(self) -> EvidenceReference:
        fields = [
            self.review_file,
            self.review_sha256,
            self.capability_id,
            self.scenario_id,
            self.probe_subject,
        ]
        if any(value is not None for value in fields) and not all(
            value is not None for value in fields
        ):
            raise ValueError("all review fields must be supplied together")
        return self


def _sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _repository_root(start: Path) -> Path:
    for candidate in [start.resolve(), *start.resolve().parents]:
        if (candidate / ".git").exists():
            return candidate
    return start.resolve()


def verify_evidence(
    evidence: EvidenceReference,
    *,
    definition_directory: Path,
    mode: Literal["calibration", "production"],
    subject_type: Literal["component", "adapter"],
    subject_revision: str,
    subject_license_spdx: str,
    artifact_sha256: str | None,
) -> tuple[list[str], dict[str, object]]:
    """Verify review/probe hashes and every typed promotion binding."""

    reasons: list[str] = []
    observations: dict[str, object] = {"claimed_level": int(evidence.level)}
    if evidence.review_file is None:
        observations["review_supplied"] = False
        if mode == "production" or evidence.level > EvidenceLevel.DISCOVERED:
            reasons.append("claimed evidence level has no named manual review")
        return reasons, observations

    repository_root = _repository_root(definition_directory)
    review_path = (definition_directory / evidence.review_file).resolve()
    observations.update(
        {
            "review_supplied": True,
            "review_file": str(review_path),
            "review_within_repository": review_path.is_relative_to(repository_root),
            "review_exists": review_path.is_file(),
        }
    )
    if not review_path.is_relative_to(repository_root):
        reasons.append("manual review file resolves outside the repository")
        return reasons, observations
    if not review_path.is_file():
        reasons.append("manual review file does not exist")
        return reasons, observations
    actual_review_hash = _sha256_file(review_path)
    observations["review_sha256"] = actual_review_hash
    observations["review_hash_matches"] = actual_review_hash == evidence.review_sha256
    if actual_review_hash != evidence.review_sha256:
        reasons.append("manual review SHA-256 does not match")
        return reasons, observations
    try:
        review = ManualReview.model_validate_json(review_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        reasons.append(f"manual review is invalid: {type(exc).__name__}: {exc}")
        return reasons, observations

    observations.update(
        {
            "review_verdict": review.verdict.value,
            "reviewer": review.reviewer,
            "review_capability_id": review.capability_id,
            "review_scenario_id": review.scenario_id,
        }
    )
    if review.verdict != ManualVerdict.PASS:
        reasons.append("manual review verdict is not pass")
    if review.capability_id != evidence.capability_id:
        reasons.append("manual review capability does not match evidence reference")
    if review.scenario_id != evidence.scenario_id:
        reasons.append("manual review scenario does not match evidence reference")

    promotion_required = mode == "production" or evidence.level > EvidenceLevel.DISCOVERED
    promotion = review.promotion
    observations["promotion_required"] = promotion_required
    observations["promotion_supplied"] = promotion is not None
    if promotion_required and promotion is None:
        reasons.append("manual review has no typed evidence promotion")
    if promotion is not None:
        observations.update(
            {
                "promotion_target_level": int(promotion.target_level),
                "promotion_subject_type": promotion.subject_type,
                "promotion_subject_revision": promotion.subject_revision,
                "promotion_subject_license_spdx": promotion.subject_license_spdx,
                "promotion_artifact_sha256": promotion.artifact_sha256,
                "promotion_probe_provider": promotion.probe_provider,
                "promotion_probe_subject": promotion.probe_subject,
                "promotion_probe_completed": promotion.probe_completed,
            }
        )
        if promotion.target_level != evidence.level:
            reasons.append("review promotion level does not match evidence reference")
        if promotion.subject_type != subject_type:
            reasons.append("review promotion subject type does not match qualified subject")
        if promotion.subject_revision != subject_revision:
            reasons.append("review promotion revision does not match qualified subject")
        if promotion.subject_license_spdx != subject_license_spdx:
            reasons.append("review promotion license does not match qualified subject")
        if artifact_sha256 is None:
            reasons.append("qualified subject has no artifact hash for evidence binding")
        elif promotion.artifact_sha256 != artifact_sha256:
            reasons.append("review promotion artifact does not match qualified subject")

    probe_path = Path(review.probe_file)
    if not probe_path.is_absolute():
        probe_path = (repository_root / probe_path).resolve()
    observations["probe_file"] = str(probe_path)
    observations["probe_within_repository"] = probe_path.is_relative_to(repository_root)
    observations["probe_exists"] = probe_path.is_file()
    if not probe_path.is_relative_to(repository_root):
        reasons.append("probe referenced by manual review resolves outside the repository")
    elif not probe_path.is_file():
        reasons.append("probe referenced by manual review does not exist")
    else:
        actual_probe_hash = _sha256_file(probe_path)
        observations["probe_sha256"] = actual_probe_hash
        observations["probe_hash_matches_review"] = actual_probe_hash == review.probe_sha256
        if actual_probe_hash != review.probe_sha256:
            reasons.append("probe SHA-256 no longer matches the manual review")
        else:
            try:
                probe = ProbeEvidence.model_validate_json(
                    probe_path.read_text(encoding="utf-8")
                )
            except (ValueError, OSError) as exc:
                reasons.append(f"reviewed probe is invalid: {type(exc).__name__}: {exc}")
            else:
                observations["probe_id"] = probe.probe_id
                observations["probe_provider"] = probe.provider
                observations["probe_subject"] = probe.subject
                probe_completed = probe.observations.get("probe_completed") is True
                observations["probe_completed"] = probe_completed
                observations["probe_id_matches_review"] = probe.probe_id == review.probe_id
                observations["probe_subject_matches"] = probe.subject == evidence.probe_subject
                if probe.probe_id != review.probe_id:
                    reasons.append("reviewed probe id does not match the manual review")
                if probe.subject != evidence.probe_subject:
                    reasons.append("reviewed probe subject does not match evidence reference")
                if promotion is not None:
                    if probe.provider != promotion.probe_provider:
                        reasons.append("reviewed probe provider does not match promotion")
                    if probe.subject != promotion.probe_subject:
                        reasons.append("reviewed probe subject does not match promotion")
                    if not probe_completed:
                        reasons.append("reviewed probe did not complete successfully")
    return reasons, observations
