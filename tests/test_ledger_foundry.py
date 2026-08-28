from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import pytest

from blackridge import ledger_foundry as foundry
from blackridge.errors import ExternalToolError

REVISION = "cpython-3.12.14@sha256:" + "a" * 64


def request_text() -> str:
    return """
capabilities:
  - id: cli-entrypoint
    acceptance:
      - id: cli-runs
"""


def verified_text() -> str:
    return json.dumps(
        [
            {
                "capability_id": "cli-entrypoint",
                "identity": "python-standard-library",
                "immutable_revision": REVISION,
                "evidence_level": 2,
            }
        ]
    )


def proposal(program: str = "print('ok')\n", tests: str | None = None) -> dict[str, object]:
    test_source = tests or "\n".join(
        f"def test_case_{number}():\n    assert True" for number in range(9)
    )
    return {
        "files": [
            {"path": "program.py", "content": program},
            {"path": "tests/test_program.py", "content": test_source},
        ],
        "program_path": "program.py",
        "test_command": ["python", "-m", "unittest"],
        "acceptance_coverage": [
            {
                "acceptance_id": "cli-runs",
                "test_file": "tests/test_program.py",
                "test_name": "test_case_0",
                "rationale": "The black-box test executes the public CLI behavior.",
            }
        ],
        "component_decisions": [
            {
                "capability_id": "cli-entrypoint",
                "source": "standard-library",
                "identity": "python-standard-library",
                "immutable_revision": REVISION,
                "evidence_level": 2,
                "rationale": "Exact supplied interpreter evidence.",
            }
        ],
        "limitations": [],
    }


class CompilerTests(unittest.TestCase):
    def test_rejects_unsafe_generated_path(self) -> None:
        raw = proposal()
        raw["files"][0]["path"] = "../program.py"  # type: ignore[index]

        with self.assertRaisesRegex(ValueError, "traverses"):
            foundry.compile_proposal(raw, request_text(), verified_text())

    def test_rejects_overclaimed_component_evidence(self) -> None:
        raw = proposal()
        raw["component_decisions"][0]["evidence_level"] = 3  # type: ignore[index]

        with self.assertRaisesRegex(ValueError, "overclaims"):
            foundry.compile_proposal(raw, request_text(), verified_text())

    def test_rejects_acceptance_mapping_to_missing_test_function(self) -> None:
        raw = proposal()
        raw["acceptance_coverage"][0]["test_name"] = "test_missing"  # type: ignore[index]

        with self.assertRaisesRegex(ValueError, "missing test function"):
            foundry.compile_proposal(raw, request_text(), verified_text())

    def test_component_lock_preserves_program_and_accepts_repaired_tests(self) -> None:
        prior, _ = foundry.compile_proposal(
            proposal(
                "print('trusted')\n",
                "\n".join(f"def test_case_{number}():\n    assert False" for number in range(9)),
            ),
            request_text(),
            verified_text(),
        )
        candidate, _ = foundry.compile_proposal(
            proposal("print('replacement')\n"), request_text(), verified_text()
        )

        composed, evidence = foundry.compose_locked_files(prior, candidate, ["program.py"])

        files = {item["path"]: item["content"] for item in composed["files"]}
        self.assertEqual(files["program.py"], "print('trusted')\n")
        self.assertEqual(
            files["tests/test_program.py"],
            next(
                item["content"]
                for item in candidate["files"]
                if item["path"] == "tests/test_program.py"
            ),
        )
        self.assertIn("program.py", evidence["locked_file_sha256"])

    def test_test_only_compiler_rejects_product_rewrites_and_preserves_controls(self) -> None:
        prior, _ = foundry.compile_proposal(
            proposal("print('trusted')\n"), request_text(), verified_text()
        )
        repaired_tests = "\n".join(
            f"def test_case_{number}():\n    assert True" for number in range(9)
        )
        raw = {
            "files": [{"path": "tests/test_program.py", "content": repaired_tests}],
            "acceptance_coverage": prior["acceptance_coverage"],
            "limitations": ["Sandbox execution remains required."],
            "program_path": "replacement.py",
        }

        repaired, ignored = foundry.compile_test_repair(raw, prior, request_text(), verified_text())

        files = {item["path"]: item["content"] for item in repaired["files"]}
        self.assertEqual(files["program.py"], "print('trusted')\n")
        self.assertEqual(repaired["program_path"], prior["program_path"])
        self.assertEqual(repaired["test_command"], prior["test_command"])
        self.assertEqual(repaired["component_decisions"], prior["component_decisions"])
        self.assertEqual(ignored, ["program_path"])

        raw["files"] = [{"path": "replacement.py", "content": "print('rewrite')\n"}]
        with self.assertRaisesRegex(ValueError, "only test files"):
            foundry.compile_test_repair(raw, prior, request_text(), verified_text())

        record = foundry.test_repair_rejection_record(
            prior,
            {"content_sha256": "b" * 64},
            ["program.py"],
            ValueError("unsafe provider value"),
        )
        self.assertEqual(record["status"], "schema-rejected")
        self.assertEqual(record["completion_sha256"], "b" * 64)
        self.assertEqual(set(record["locked_file_sha256"]), {"program.py"})
        json.dumps(record)

    def test_prompt_requires_portable_black_box_tests(self) -> None:
        _, user = foundry.prompt("task", "request", "evaluator", "[]", None)

        self.assertIn("only the public CLI contract", user)
        self.assertIn("never import the generated program", user)
        self.assertIn("absolute path", user)
        self.assertIn("VERIFIED COMPONENTS", user)

        prior, _ = foundry.compile_proposal(
            proposal("print('trusted')\n"), request_text(), verified_text()
        )
        _, repair_user = foundry.prompt("task", "request", "evaluator", "[]", "tests failed", prior)
        self.assertIn("Return only files under a test or tests directory", repair_user)
        self.assertIn("IMMUTABLE PRIOR PROPOSAL", repair_user)
        self.assertNotIn("Return a full replacement project", repair_user)


def test_main_retains_provider_failures_and_finishes_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = tmp_path / "task.md"
    request = tmp_path / "request.yaml"
    verified = tmp_path / "verified.json"
    evaluator = tmp_path / "evaluate_duplicate_finder.py"
    env_file = tmp_path / ".env"
    output = tmp_path / "output"
    task.write_text("Build the public duplicate finder contract.\n", encoding="utf-8")
    request.write_text(request_text(), encoding="utf-8")
    verified.write_text(verified_text(), encoding="utf-8")
    evaluator.write_text("# public evaluator fixture\n", encoding="utf-8")
    env_file.write_text("DEEPSEEK_API_KEY=fixture-secret\n", encoding="utf-8")

    class _FailingBackend:
        def __init__(self, **_: object) -> None:
            pass

        def complete_json(self, **_: object) -> object:
            raise ExternalToolError("truncated provider JSON")

    monkeypatch.setattr(foundry, "DeepSeekBackend", _FailingBackend)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "blackridge-ledger",
            "--task",
            str(task),
            "--request",
            str(request),
            "--verified-components",
            str(verified),
            "--evaluator",
            str(evaluator),
            "--env-file",
            str(env_file),
            "--output",
            str(output),
            "--max-repairs",
            "1",
        ],
    )

    assert foundry.main() == 1
    ledger = json.loads((output / "ledger.json").read_text(encoding="utf-8"))
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert len(ledger) == 2
    assert {event["status"] for event in ledger} == {"builder-failed"}
    assert {event["failure"] for event in ledger} == {"ExternalToolError"}
    assert summary["status"] == "failed"
    assert summary["frozen_inputs_unchanged"] is True
    assert summary["manual_interventions"] == 0


if __name__ == "__main__":
    unittest.main()
