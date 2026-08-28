"""Build a deliberately provisional component map from an L0 discovery run."""

from __future__ import annotations

from datetime import UTC, datetime

from blackridge.models import (
    BlueprintComponent,
    DiscoveryRun,
    SystemBlueprint,
)


def build_blueprint(run: DiscoveryRun, *, now: datetime | None = None) -> SystemBlueprint:
    """Choose inspection targets without claiming that they are integration-ready."""

    components: list[BlueprintComponent] = []
    for result in run.results:
        eligible = [
            candidate for candidate in result.candidates if candidate.decision != "rejected"
        ]
        chosen = eligible[0] if eligible else None
        components.append(
            BlueprintComponent(
                capability_id=result.capability.id,
                repository=chosen.metadata.full_name if chosen else None,
                alternatives=[candidate.metadata.full_name for candidate in eligible[1:5]],
                status="provisional" if chosen else "no-candidate",
                current_evidence_level=chosen.evidence_level if chosen else None,
                accepts=result.capability.accepts,
                produces=result.capability.produces,
                warnings=(
                    [
                        "L0 metadata choice only; inspect source, license, API, and dependencies",
                        "must boot in a sandbox before selection",
                        *(chosen.warnings if chosen else []),
                        *(chosen.blockers if chosen else []),
                    ]
                    if chosen
                    else ["no non-rejected candidate was discovered"]
                ),
            )
        )

    return SystemBlueprint(
        generated_at=(now or datetime.now(UTC)).astimezone(UTC),
        system_name=run.request.name,
        goal=run.request.goal,
        components=components,
    )
