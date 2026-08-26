"""Safe subprocess boundary for invoking established upstream tools."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from blackridge.errors import ExternalToolError


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
        try:
            process = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExternalToolError(
                f"upstream command timed out after {timeout_seconds}s: {argv[0]}"
            ) from exc

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
