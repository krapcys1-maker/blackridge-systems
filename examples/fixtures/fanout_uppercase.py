"""Calibration branch that uppercases a seed value."""

import json
import sys

payload = json.load(sys.stdin)
print(json.dumps({"uppercase": payload["text"].upper()}, sort_keys=True))
