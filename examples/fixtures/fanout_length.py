"""Calibration branch that measures a seed value."""

import json
import sys

payload = json.load(sys.stdin)
print(json.dumps({"length": len(payload["text"])}, sort_keys=True))
