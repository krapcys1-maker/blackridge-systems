"""Safe subprocess boundary for invoking established upstream tools."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from blackridge.errors import ExternalToolError
from blackridge.process_boundary import run_bounded


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandRunner:
    """Runs an argument vector without a shell and returns captured text."""

    def resolve(self, executable: str) -> str:
        resolved = shutil.which(executable)
        if resolved is None:
            raise ExternalToolError(f"required executable not found: {executable}")
        return str(Path(resolved))

    def run(self, argv: list[str], *, timeout_seconds: int = 120) -> CommandResult:
        if not argv:
            raise ValueError("argv cannot be empty")
        command = [self.resolve(argv[0]), *argv[1:]]
        process = run_bounded(command, timeout_seconds=timeout_seconds)
        if process.timed_out:
            raise ExternalToolError(
                f"upstream command timed out after {timeout_seconds}s: {argv[0]}"
            )
        if process.output_limit_exceeded:
            raise ExternalToolError(f"upstream command exceeded the output limit: {argv[0]}")

        result = CommandResult(
            argv=tuple(argv),
            returncode=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no output"
            raise ExternalToolError(f"{argv[0]} failed ({result.returncode}): {detail[:1000]}")
        return result
