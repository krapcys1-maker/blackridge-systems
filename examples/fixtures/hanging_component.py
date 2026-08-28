"""Hostile calibration component that records SIGTERM and refuses to exit."""

from __future__ import annotations

import json
import signal
import sys
import time
from pathlib import Path


def ignore_term(_signum: int, _frame: object) -> None:
    Path("/tmp/blackridge-timeout-term-seen").write_text("term\n", encoding="utf-8")


signal.signal(signal.SIGTERM, ignore_term)
json.load(sys.stdin)
while True:
    time.sleep(0.1)
