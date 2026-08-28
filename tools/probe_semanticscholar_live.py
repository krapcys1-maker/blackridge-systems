"""Run one bounded live Semantic Scholar calibration without assigning a verdict."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    request = {
        "request_id": "live-doi-calibration",
        "operation": "get-paper",
        "paper_id": "DOI:10.1093/mind/LIX.236.433",
    }
    environment = dict(os.environ)
    for name in list(environment):
        if any(token in name.upper() for token in ("SEMANTIC_SCHOLAR", "S2_API")):
            environment.pop(name)
    started = perf_counter()
    completed = subprocess.run(
        [str(args.python.absolute()), str(args.adapter.resolve())],
        input=json.dumps(request),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
        env=environment,
        check=False,
    )
    duration = round(perf_counter() - started, 3)
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError:
        response = None
    papers = response.get("papers", []) if isinstance(response, dict) else []
    paper = papers[0] if len(papers) == 1 and isinstance(papers[0], dict) else {}
    checks = {
        "exit_zero": completed.returncode == 0,
        "stderr_empty": completed.stderr == "",
        "structured_response": isinstance(response, dict),
        "status_ok": isinstance(response, dict) and response.get("status") == "ok",
        "exactly_one_paper": len(papers) == 1,
        "paper_identity_present": bool(paper.get("paper_id")),
        "title_present": bool(paper.get("title")),
    }
    evidence = {
        "schema_version": "1",
        "observed_at": datetime.now(UTC).isoformat(),
        "probe_completed": all(checks.values()),
        "evidence_scope": "mutable live API calibration; not promotion or benchmark evidence",
        "endpoint_policy": "adapter default official endpoint; custom endpoint variables removed",
        "adapter_sha256": _sha256(args.adapter.resolve()),
        "request": request,
        "execution": {
            "exit_code": completed.returncode,
            "duration_seconds": duration,
            "stderr": completed.stderr,
        },
        "response": response,
        "checks": checks,
        "manual_verdict": None,
    }
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(
        json.dumps(evidence, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(
        json.dumps(
            {
                "probe_completed": evidence["probe_completed"],
                "duration_seconds": duration,
                "status": response.get("status") if isinstance(response, dict) else None,
            }
        )
    )
    return 0 if evidence["probe_completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
