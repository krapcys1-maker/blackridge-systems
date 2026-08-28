"""Calibration join that proves both contract-keyed inputs reached one component."""

import json
import sys

payload = json.load(sys.stdin)
inputs = payload["inputs"]
uppercase = inputs["uppercase/v1"]["uppercase"]
length = inputs["length/v1"]["length"]
print(
    json.dumps(
        {
            "summary": f"{uppercase}:{length}",
            "observed_contracts": sorted(inputs),
        },
        sort_keys=True,
    )
)
