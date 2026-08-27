"""Bounded, shell-free subprocess execution shared by Blackridge integrations."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import IO

DEFAULT_MAX_OUTPUT_BYTES_PER_STREAM = 8 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 120.0
_CHUNK_BYTES = 64 * 1024
_TERMINATION_GRACE_SECONDS = 2.0


def resolve_executable(name: str) -> str | None:
    """Resolve PATH tools and console scripts installed beside this interpreter."""

    resolved = shutil.which(name)
    if resolved is not None:
        return resolved
    scripts_directory = Path(sys.executable).resolve().parent
    candidates = [scripts_directory / name]
    if not Path(name).suffix:
        candidates.append(scripts_directory / f"{name}.exe")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


@dataclass(frozen=True)
class BoundedProcessResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
    output_limit_exceeded: bool
    stdout_bytes_seen: int
    stderr_bytes_seen: int

    @property
    def exit_code(self) -> int:
        """Compatibility alias used in retained evidence records."""

        return self.returncode


def _capture_pipe(
    stream: IO[bytes],
    *,
    limit: int,
    retained: bytearray,
    state: dict[str, int | bool],
    exceeded: threading.Event,
) -> None:
    try:
        while chunk := stream.read(_CHUNK_BYTES):
            state["seen"] = int(state["seen"]) + len(chunk)
            remaining = max(0, limit - len(retained))
            retained.extend(chunk[:remaining])
            if len(chunk) > remaining:
                state["exceeded"] = True
                exceeded.set()
    finally:
        stream.close()


def _write_stdin(stream: IO[bytes], data: bytes) -> None:
    try:
        stream.write(data)
        stream.flush()
    except (BrokenPipeError, OSError):
        pass
    finally:
        stream.close()


def _terminate_process_tree(process: subprocess.Popen[bytes], *, force: bool) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        kill_process_group = getattr(os, "killpg", None)
        kill_signal = getattr(signal, "SIGKILL", signal.SIGTERM) if force else signal.SIGTERM
        try:
            if callable(kill_process_group):
                kill_process_group(process.pid, kill_signal)
                return
        except ProcessLookupError:
            return
    if os.name == "nt":
        system_root = os.environ.get("SYSTEMROOT")
        taskkill = Path(system_root, "System32", "taskkill.exe") if system_root else None
        if taskkill is not None and taskkill.is_file():
            killer = subprocess.Popen(
                [str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
            )
            try:
                killer.wait(timeout=5)
            except subprocess.TimeoutExpired:
                killer.kill()
            return
    if force:
        process.kill()
    else:
        process.terminate()


def run_bounded(
    argv: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    maximum_output_bytes_per_stream: int = DEFAULT_MAX_OUTPUT_BYTES_PER_STREAM,
    encoding: str = "utf-8",
    errors: str = "replace",
) -> BoundedProcessResult:
    """Run one argv with finite time/output and terminate it when either limit is hit."""

    if not argv:
        raise ValueError("argv cannot be empty")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if maximum_output_bytes_per_stream <= 0:
        raise ValueError("maximum_output_bytes_per_stream must be positive")

    command = [str(value) for value in argv]
    creationflags = (
        int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        if os.name == "nt"
        else 0
    )
    started = perf_counter()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=os.name == "posix",
        creationflags=creationflags,
    )
    if process.stdin is None or process.stdout is None or process.stderr is None:
        _terminate_process_tree(process, force=True)
        raise RuntimeError("subprocess pipes were not created")

    retained_stdout = bytearray()
    retained_stderr = bytearray()
    stdout_state: dict[str, int | bool] = {"seen": 0, "exceeded": False}
    stderr_state: dict[str, int | bool] = {"seen": 0, "exceeded": False}
    exceeded = threading.Event()
    readers = [
        threading.Thread(
            target=_capture_pipe,
            kwargs={
                "stream": process.stdout,
                "limit": maximum_output_bytes_per_stream,
                "retained": retained_stdout,
                "state": stdout_state,
                "exceeded": exceeded,
            },
            daemon=True,
        ),
        threading.Thread(
            target=_capture_pipe,
            kwargs={
                "stream": process.stderr,
                "limit": maximum_output_bytes_per_stream,
                "retained": retained_stderr,
                "state": stderr_state,
                "exceeded": exceeded,
            },
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()
    writer = threading.Thread(
        target=_write_stdin,
        args=(process.stdin, (input_text or "").encode(encoding)),
        daemon=True,
    )
    writer.start()

    deadline = started + timeout_seconds
    timed_out = False
    output_limit_exceeded = False
    while process.poll() is None:
        if exceeded.wait(timeout=0.01):
            output_limit_exceeded = True
            _terminate_process_tree(process, force=False)
            break
        if perf_counter() >= deadline:
            timed_out = True
            _terminate_process_tree(process, force=False)
            break

    if process.poll() is None:
        try:
            process.wait(timeout=_TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(process, force=True)
    process.wait()
    writer.join(timeout=_TERMINATION_GRACE_SECONDS)
    for reader in readers:
        reader.join(timeout=_TERMINATION_GRACE_SECONDS)
    output_limit_exceeded = output_limit_exceeded or bool(
        stdout_state["exceeded"] or stderr_state["exceeded"]
    )
    return BoundedProcessResult(
        argv=tuple(command),
        returncode=process.returncode,
        stdout=retained_stdout.decode(encoding, errors=errors),
        stderr=retained_stderr.decode(encoding, errors=errors),
        duration_seconds=round(perf_counter() - started, 3),
        timed_out=timed_out,
        output_limit_exceeded=output_limit_exceeded,
        stdout_bytes_seen=int(stdout_state["seen"]),
        stderr_bytes_seen=int(stderr_state["seen"]),
    )
