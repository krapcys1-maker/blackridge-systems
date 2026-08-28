"""Calibration component that turns one research query into a paper record."""

from __future__ import annotations

import json
import sys

request = json.load(sys.stdin)
topic = request["topic"]
json.dump(
    {
        "query": request,
        "paper": {
            "title": f"Evidence for {topic}",
            "identifier": "fixture-paper-001",
        },
        "trace": {"source": "research-source-calibration"},
    },
    sys.stdout,
)
