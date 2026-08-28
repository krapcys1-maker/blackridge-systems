"""Hostile calibration component for real memory and PID cgroup controls."""

from __future__ import annotations

import json
import subprocess
import sys

request = json.load(sys.stdin)
case = request["case"]
if case == "memory":
    allocation = bytearray(1200 * 1024 * 1024)
    json.dump({"case": case, "blocked": False, "allocated": len(allocation)}, sys.stdout)
elif case == "pids":
    children: list[subprocess.Popen[bytes]] = []
    error: OSError | None = None
    try:
        for _ in range(400):
            children.append(subprocess.Popen(["sleep", "30"]))
    except OSError as exc:
        error = exc
    finally:
        for child in children:
            child.terminate()
        for child in children:
            child.wait()
    json.dump(
        {
            "case": case,
            "blocked": error is not None and error.errno == 11,
            "spawned": len(children),
            "errno": error.errno if error is not None else None,
        },
        sys.stdout,
    )
else:
    raise ValueError(f"unsupported hostile resource case: {case}")
