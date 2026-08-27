"""Calibration component that consumes one separately locked resource file."""

import json
import sys
from pathlib import Path

payload = json.load(sys.stdin)
resource = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(
    json.dumps(
        {"result": f"{resource['prefix']}:{payload['value'] * resource['multiplier']}"},
        sort_keys=True,
    )
)
