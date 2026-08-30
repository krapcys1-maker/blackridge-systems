"""Independent adversarial evaluator for the self-hosting duplicate-finder task."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DuplicateFinderEvaluation(unittest.TestCase):
    program: Path

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "input"
        self.root.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_program(
        self,
        output: Path,
        *,
        timeout: float = 5,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.program), str(self.root), str(output)],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

    def test_nested_duplicates_are_deterministic_and_inputs_are_unchanged(self) -> None:
        first = self.root / "z.txt"
        second = self.root / "nested" / "a.txt"
        unique = self.root / "unique.txt"
        second.parent.mkdir()
        first.write_bytes(b"duplicate")
        second.write_bytes(b"duplicate")
        unique.write_bytes(b"unique")
        before = {
            path.name: (_sha256(path), path.stat().st_mode, path.stat().st_mtime_ns)
            for path in (first, second, unique)
        }
        output_one = self.base / "one.json"
        output_two = self.base / "two.json"

        first_run = self.run_program(output_one)
        second_run = self.run_program(output_two)

        self.assertEqual(first_run.returncode, 0, first_run.stderr)
        self.assertEqual(second_run.returncode, 0, second_run.stderr)
        self.assertEqual(output_one.read_bytes(), output_two.read_bytes())
        payload = json.loads(output_one.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["duplicate_groups"]), 1)
        self.assertEqual(
            set(payload["duplicate_groups"][0]["files"]),
            {str(first), str(second)},
        )
        after = {
            path.name: (_sha256(path), path.stat().st_mode, path.stat().st_mtime_ns)
            for path in (first, second, unique)
        }
        self.assertEqual(before, after)

    def test_same_path_output_is_rejected_without_mutation(self) -> None:
        input_file = self.root / "input.txt"
        input_file.write_bytes(b"must-survive")
        before = _sha256(input_file)
        result = self.run_program(input_file)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(_sha256(input_file), before)

    def test_external_hardlink_output_is_rejected_without_mutation(self) -> None:
        input_file = self.root / "input.txt"
        output = self.base / "outside-output.json"
        input_file.write_bytes(b"must-survive-hardlink")
        os.link(input_file, output)
        before = _sha256(input_file)
        result = self.run_program(output)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(_sha256(input_file), before)
        self.assertEqual(_sha256(output), before)

    def test_output_created_inside_tree_is_not_reported_as_input(self) -> None:
        (self.root / "a.txt").write_bytes(b"same")
        (self.root / "b.txt").write_bytes(b"same")
        output = self.root / "report.json"
        result = self.run_program(output)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(output.read_text(encoding="utf-8"))
        paths = [path for group in payload["duplicate_groups"] for path in group["files"]]
        self.assertNotIn(str(output), paths)

    def test_file_and_directory_symlinks_cannot_escape_root(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_bytes(b"outside-secret")
        (self.root / "file-link").symlink_to(secret)
        (self.root / "directory-link").symlink_to(outside, target_is_directory=True)
        output = self.base / "report.json"
        result = self.run_program(output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("outside-secret", output.read_text(encoding="utf-8"))
        payload = json.loads(output.read_text(encoding="utf-8"))
        paths = json.dumps(payload)
        self.assertNotIn(str(secret), paths)
        self.assertNotIn(str(self.root / "file-link"), paths)

    def test_internal_symlink_cycle_terminates(self) -> None:
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "file.txt").write_bytes(b"cycle")
        (nested / "back").symlink_to(self.root, target_is_directory=True)
        result = self.run_program(self.base / "report.json", timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unreadable_file_has_explicit_error(self) -> None:
        unreadable = self.root / "unreadable.txt"
        unreadable.write_bytes(b"no-read")
        unreadable.chmod(0)
        try:
            output = self.base / "report.json"
            result = self.run_program(output)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            matching = [
                item for item in payload["unreadable_files"] if item.get("path") == str(unreadable)
            ]
            self.assertEqual(len(matching), 1)
            self.assertTrue(matching[0].get("error"))
        finally:
            unreadable.chmod(stat.S_IRUSR | stat.S_IWUSR)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("program", type=Path)
    args = parser.parse_args()
    DuplicateFinderEvaluation.program = args.program.resolve()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DuplicateFinderEvaluation)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() and result.testsRun == 7 else 1


if __name__ == "__main__":
    raise SystemExit(main())
