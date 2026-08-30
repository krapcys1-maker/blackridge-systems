"""Independent evaluator for the composition-reuse benchmark.

Every other workload measures either code generation or the execution of a composition a
human already chose. This evaluator measures the decision in between: whether the solver
reuses a reviewed implementation when one qualifies, and whether it fails closed — with an
explicit reason — when the pool does not.

The evaluator owns the expected outcomes. Candidates must not read or modify this file
during measurement.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

from blackridge.composition import (
    CompositionDefinition,
    CompositionPlan,
    generate_system,
    run_generated_system,
    solve_composition,
)
from blackridge.formats import load_yaml

EXECUTABLE_CASE = "reuse-complete"


def _solve(case: Path) -> CompositionPlan:
    definition = CompositionDefinition.model_validate(load_yaml(case))
    return solve_composition(definition, definition_file=case)


def _reasons(plan: CompositionPlan, subject_id: str) -> list[str]:
    for observation in plan.qualifications:
        if observation.subject_id == subject_id:
            return observation.reasons
    raise AssertionError(f"no qualification observation for {subject_id}")


class CompositionReuseEvaluation(unittest.TestCase):
    cases: Path

    def plan(self, stem: str) -> CompositionPlan:
        return _solve(self.cases / f"{stem}.yaml")

    def test_qualified_component_is_reused(self) -> None:
        plan = self.plan("reuse-complete")
        self.assertTrue(plan.complete)
        self.assertEqual(plan.selected_component_ids, ["grounded-researcher-v1"])
        self.assertEqual(plan.unresolved_capabilities, [])

    def test_reuse_writes_no_new_code(self) -> None:
        plan = self.plan("reuse-complete")
        self.assertEqual(plan.selected_adapter_ids, [])
        self.assertEqual([step.step_type for step in plan.steps], ["component"])

    def test_blocked_preferred_entry_is_never_selected(self) -> None:
        plan = self.plan("blocked-preferred-fallback")
        self.assertTrue(plan.complete)
        self.assertNotIn("grounded-researcher-vendor-fork", plan.selected_component_ids)
        self.assertEqual(plan.selected_component_ids, ["grounded-researcher-v1"])
        self.assertIn(
            "vendor fork has no independent manual review",
            _reasons(plan, "grounded-researcher-vendor-fork"),
        )

    def test_evidence_below_the_required_level_is_rejected(self) -> None:
        plan = self.plan("evidence-floor")
        self.assertFalse(plan.complete)
        self.assertEqual(plan.unresolved_capabilities, ["grounded-research-synthesis"])
        self.assertIn("evidence L3 is below required L4", _reasons(plan, "grounded-researcher-v1"))

    def test_license_outside_the_allowed_set_is_rejected(self) -> None:
        plan = self.plan("license-blocked")
        self.assertFalse(plan.complete)
        self.assertEqual(plan.unresolved_capabilities, ["grounded-research-synthesis"])
        self.assertIn(
            "license GPL-3.0-only is not allowed", _reasons(plan, "grounded-researcher-v1")
        )

    def test_artifact_hash_drift_is_rejected(self) -> None:
        plan = self.plan("hash-drift")
        self.assertFalse(plan.complete)
        self.assertEqual(plan.unresolved_capabilities, ["grounded-research-synthesis"])
        reasons = _reasons(plan, "grounded-researcher-v1")
        self.assertIn("command launch artifact SHA-256 does not match its lock", reasons)
        self.assertIn("review promotion artifact does not match qualified subject", reasons)

    def test_unroutable_graph_is_distinguished_from_an_empty_pool(self) -> None:
        plan = self.plan("adapter-gap")
        self.assertFalse(plan.complete)
        # The component qualifies; only the contract route is missing. Reporting this as an
        # empty pool would hide the real defect.
        self.assertEqual(_reasons(plan, "grounded-researcher-v1"), [])
        self.assertIn(
            "Eligible components exist, but no complete contract route was found.",
            plan.warnings,
        )

    def test_selected_plan_executes_and_satisfies_the_output_contract(self) -> None:
        case = self.cases / f"{EXECUTABLE_CASE}.yaml"
        definition = CompositionDefinition.model_validate(load_yaml(case))
        plan = solve_composition(definition, definition_file=case)
        request = json.loads(
            (
                self.cases.parent.parent.parent
                / "components"
                / "grounded_researcher_v1"
                / "fixtures"
                / "policy-answerable.json"
            ).read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as workspace:
            bundle = Path(workspace) / "bundle"
            generated = generate_system(
                definition, plan, definition_file=case, output_directory=bundle
            )
            self.assertTrue(generated.execution_ready)
            evidence = run_generated_system(
                bundle,
                request,
                expected_provenance_sha256=generated.artifact_sha256["provenance.json"],
            )
        observations = evidence.observations
        self.assertTrue(observations["all_steps_completed"])
        self.assertIsNone(observations["failure_reason"])
        self.assertEqual(observations["final_contract"], "grounded-research-response/v1")
        self.assertEqual(observations["final_validation_errors"], [])
        self.assertEqual(observations["final_artifact"]["status"], "answered")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "cases",
        type=Path,
        nargs="?",
        default=Path(__file__).resolve().parent.parent
        / "benchmarks"
        / "composition-reuse-v1"
        / "cases",
    )
    args = parser.parse_args()
    CompositionReuseEvaluation.cases = args.cases.resolve()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CompositionReuseEvaluation)
    result = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(suite)
    return 0 if result.wasSuccessful() and result.testsRun == 8 else 1


if __name__ == "__main__":
    raise SystemExit(main())
