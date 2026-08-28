from __future__ import annotations

import json
import unittest

import foundry


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


def proposal(
    program: str = "print('ok')\n", tests: str | None = None
) -> dict[str, object]:
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
        "acceptance_ids": ["cli-runs"],
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

    def test_component_lock_preserves_program_and_accepts_repaired_tests(self) -> None:
        prior, _ = foundry.compile_proposal(
            proposal(
                "print('trusted')\n",
                "\n".join(
                    f"def test_case_{number}():\n    assert False"
                    for number in range(9)
                ),
            ),
            request_text(),
            verified_text(),
        )
        candidate, _ = foundry.compile_proposal(
            proposal("print('replacement')\n"), request_text(), verified_text()
        )

        composed, evidence = foundry.compose_locked_files(
            prior, candidate, ["program.py"]
        )

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

    def test_prompt_requires_portable_black_box_tests(self) -> None:
        _, user = foundry.prompt("task", "request", "evaluator", "[]", None)

        self.assertIn("only the public CLI contract", user)
        self.assertIn("never import the generated program", user)
        self.assertIn("absolute path", user)
        self.assertIn("VERIFIED COMPONENTS", user)


if __name__ == "__main__":
    unittest.main()
