"""Freeze the composition-reuse benchmark cases from the live component pool.

The benchmark measures component *selection*, which no existing workload covers. The
Duplicate Finder workload measures generation and records only the Python standard library
in its component decisions; the calibration and system-E2E workloads execute a composition
that a human already chose. Neither answers the product's actual claim: given a pool that
contains a reviewed implementation, does the solver reuse it, and does it fail closed when
the pool is inadequate?

Running this tool rewrites the frozen case files. That is a deliberate act: the cases lock
the exact component artifact and manual-review hashes, so drift must be re-frozen and
re-reviewed rather than silently absorbed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
COMPONENT_SOURCE = (
    REPOSITORY_ROOT / "components" / "grounded_researcher_v1" / "grounded_researcher.py"
)
REVIEW_RELATIVE = (
    "../../../evidence/manual/2026-08-27/grounded-researcher-component-l3-v2-review.json"
)
REVIEW_SOURCE = (
    REPOSITORY_ROOT
    / "evidence"
    / "manual"
    / "2026-08-27"
    / "grounded-researcher-component-l3-v2-review.json"
)
COMPONENT_REVISION = "fc3b7705f620132f5c5ad866b75a66ab5cc9c775"
CAPABILITY_ID = "grounded-research-synthesis"
SCENARIO_ID = "independent-domains-and-negative-control"

REQUEST_CONTRACT = "grounded-research-request/v1"
RESPONSE_CONTRACT = "grounded-research-response/v1"
UNMATCHED_CONTRACT = "literature-brief/v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contracts() -> list[dict[str, Any]]:
    """Declare the exact contracts the reviewed component reads and writes."""

    return [
        {
            "contract_id": REQUEST_CONTRACT,
            "schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "additionalProperties": False,
                "required": ["schema_version", "request_id", "question", "documents"],
                "properties": {
                    "schema_version": {"const": "1"},
                    "request_id": {"type": "string", "minLength": 1},
                    "question": {"type": "string", "minLength": 3},
                    "minimum_sources": {"type": "integer", "minimum": 1},
                    "documents": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["document_id", "title", "full_text"],
                            "properties": {
                                "document_id": {"type": "string", "minLength": 1},
                                "title": {"type": "string", "minLength": 1},
                                "full_text": {"type": "string", "minLength": 1},
                            },
                        },
                    },
                },
            },
        },
        {
            "contract_id": RESPONSE_CONTRACT,
            "schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "schema_version",
                    "request_id",
                    "status",
                    "answer",
                    "claims",
                    "sources",
                ],
                "properties": {
                    "schema_version": {"const": "1"},
                    "request_id": {"type": ["string", "null"]},
                    "status": {"enum": ["answered", "abstained"]},
                    "answer": {"type": "string"},
                    "claims": {"type": "array"},
                    "sources": {"type": "array"},
                },
            },
        },
        {
            "contract_id": UNMATCHED_CONTRACT,
            "schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "additionalProperties": False,
                "required": ["topic"],
                "properties": {"topic": {"type": "string", "minLength": 3}},
            },
        },
    ]


def _component(
    *,
    component_id: str,
    artifact_sha256: str,
    license_spdx: str = "Apache-2.0",
    selection_priority: int = 10,
    blocked_reasons: list[str] | None = None,
) -> dict[str, Any]:
    """Build one pool entry for the reviewed grounded-research component."""

    entry: dict[str, Any] = {
        "component_id": component_id,
        "capability_id": CAPABILITY_ID,
        "source_uri": "blackridge://components/grounded-researcher-v1",
        "revision": COMPONENT_REVISION,
        "license_spdx": license_spdx,
        "integration": "command-json",
        "accepts": [REQUEST_CONTRACT],
        "produces": [RESPONSE_CONTRACT],
        "launch": {
            "argv": [
                "{python}",
                "{definition_dir}/../../../components/grounded_researcher_v1/grounded_researcher.py",
            ],
            "artifact_file": (
                "{definition_dir}/../../../components/grounded_researcher_v1/grounded_researcher.py"
            ),
            "artifact_sha256": artifact_sha256,
            "working_directory": "{definition_dir}",
            "timeout_seconds": 30,
            "environment_allowlist": [],
        },
        "evidence": {
            "level": 3,
            "review_file": REVIEW_RELATIVE,
            "review_sha256": _sha256(REVIEW_SOURCE),
            "capability_id": CAPABILITY_ID,
            "scenario_id": SCENARIO_ID,
            "probe_subject": f"grounded-researcher-v1@{_sha256(COMPONENT_SOURCE)}",
        },
        "selection_priority": selection_priority,
    }
    if blocked_reasons:
        entry["blocked_reasons"] = blocked_reasons
    return entry


def _definition(
    *,
    name: str,
    goal: str,
    components: list[dict[str, Any]],
    minimum_evidence_level: int = 2,
    allowed_licenses: list[str] | None = None,
    external_input: str = REQUEST_CONTRACT,
) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "name": name,
        "goal": goal,
        "mode": "calibration",
        "external_input": external_input,
        "required_output": RESPONSE_CONTRACT,
        "required_capabilities": [CAPABILITY_ID],
        "allowed_licenses": allowed_licenses or ["Apache-2.0"],
        "allowed_integrations": ["command-json"],
        "minimum_evidence_level": minimum_evidence_level,
        "contracts": _contracts(),
        "components": components,
    }


def build_cases() -> dict[str, dict[str, Any]]:
    """Return every frozen case keyed by its file stem."""

    artifact_hash = _sha256(COMPONENT_SOURCE)
    drifted_hash = "0" * 64

    return {
        "reuse-complete": _definition(
            name="reuse-complete",
            goal=(
                "Reuse the reviewed grounded-research component instead of writing a new "
                "implementation for the same capability."
            ),
            components=[
                _component(component_id="grounded-researcher-v1", artifact_sha256=artifact_hash)
            ],
        ),
        "blocked-preferred-fallback": _definition(
            name="blocked-preferred-fallback",
            goal=(
                "Prefer a qualified alternative when the highest-priority implementation carries "
                "an explicit block, and never select the blocked entry silently."
            ),
            components=[
                _component(
                    component_id="grounded-researcher-vendor-fork",
                    artifact_sha256=artifact_hash,
                    selection_priority=0,
                    blocked_reasons=["vendor fork has no independent manual review"],
                ),
                _component(
                    component_id="grounded-researcher-v1",
                    artifact_sha256=artifact_hash,
                    selection_priority=10,
                ),
            ],
        ),
        "evidence-floor": _definition(
            name="evidence-floor",
            goal=(
                "Reject a reviewed L3 component when the composition requires L4 system "
                "verification, rather than lowering the bar to complete the plan."
            ),
            components=[
                _component(component_id="grounded-researcher-v1", artifact_sha256=artifact_hash)
            ],
            minimum_evidence_level=4,
        ),
        "license-blocked": _definition(
            name="license-blocked",
            goal=(
                "Reject the only implementation for a required capability when its declared "
                "license is outside the allowed set."
            ),
            components=[
                _component(
                    component_id="grounded-researcher-v1",
                    artifact_sha256=artifact_hash,
                    license_spdx="GPL-3.0-only",
                )
            ],
        ),
        "hash-drift": _definition(
            name="hash-drift",
            goal=(
                "Reject a component whose on-disk artifact no longer matches the hash the "
                "manual review approved, instead of trusting the declared identity."
            ),
            components=[
                _component(component_id="grounded-researcher-v1", artifact_sha256=drifted_hash)
            ],
        ),
        "adapter-gap": _definition(
            name="adapter-gap",
            goal=(
                "Report an unroutable contract graph when the external input cannot reach the "
                "required output and no adapter bridges the gap."
            ),
            components=[
                _component(component_id="grounded-researcher-v1", artifact_sha256=artifact_hash)
            ],
            external_input=UNMATCHED_CONTRACT,
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "benchmarks" / "composition-reuse-v1" / "cases",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail instead of writing when a frozen case would change.",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    changed: list[str] = []
    manifest: dict[str, str] = {}
    for stem, definition in build_cases().items():
        path = args.output / f"{stem}.yaml"
        # Frozen cases are hashed as bytes, so they must not pick up platform line endings.
        rendered = yaml.safe_dump(definition, sort_keys=False, width=100).encode("utf-8")
        previous = path.read_bytes() if path.is_file() else None
        if previous != rendered:
            changed.append(stem)
            if not args.check:
                path.write_bytes(rendered)
        manifest[f"{stem}.yaml"] = hashlib.sha256(rendered).hexdigest()

    manifest_path = args.output.parent / "case-manifest.json"
    manifest_payload = json.dumps(
        {
            "schema_version": "1",
            "component_artifact_sha256": _sha256(COMPONENT_SOURCE),
            "manual_review_sha256": _sha256(REVIEW_SOURCE),
            "cases": manifest,
        },
        indent=2,
    )
    if not args.check:
        manifest_path.write_text(manifest_payload + "\n", encoding="utf-8")

    if args.check and changed:
        print("frozen cases would change: " + ", ".join(sorted(changed)))
        return 1
    print(f"froze {len(manifest)} cases; changed: {', '.join(sorted(changed)) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
