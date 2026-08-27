from __future__ import annotations

import sys
from pathlib import Path

from blackridge.process_boundary import resolve_executable, run_bounded


def test_resolve_executable_finds_script_beside_interpreter(
    tmp_path: Path, monkeypatch
) -> None:
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    interpreter = scripts / "python.exe"
    interpreter.touch()
    console_script = scripts / "fixture-tool.exe"
    console_script.touch()
    monkeypatch.setattr("blackridge.process_boundary.shutil.which", lambda _name: None)
    monkeypatch.setattr("blackridge.process_boundary.sys.executable", str(interpreter))

    assert resolve_executable("fixture-tool") == str(console_script)


def test_bounded_process_retains_normal_output() -> None:
    result = run_bounded(
        [sys.executable, "-c", "print('ready')"],
        timeout_seconds=5,
        maximum_output_bytes_per_stream=1024,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "ready"
    assert result.timed_out is False
    assert result.output_limit_exceeded is False


def test_bounded_process_stops_excessive_output() -> None:
    result = run_bounded(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 100_000)"],
        timeout_seconds=5,
        maximum_output_bytes_per_stream=1024,
    )

    assert result.output_limit_exceeded is True
    assert len(result.stdout.encode()) == 1024
    assert result.stdout_bytes_seen > 1024


def test_bounded_process_stops_timeout() -> None:
    result = run_bounded(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout_seconds=0.05,
        maximum_output_bytes_per_stream=1024,
    )

    assert result.timed_out is True
    assert result.duration_seconds < 5
