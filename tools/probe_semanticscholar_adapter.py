"""Exercise the real semanticscholar client against a deterministic local HTTP replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlparse
from uuid import uuid4


def _paper(identifier: str, title: str) -> dict[str, object]:
    return {
        "paperId": identifier,
        "title": title,
        "abstract": f"Abstract for {title}.",
        "year": 2026,
        "authors": [{"authorId": "author-1", "name": "Ada Example"}],
        "externalIds": {"DOI": f"10.9999/{identifier}"},
        "citationCount": 7,
        "url": f"https://www.semanticscholar.org/paper/{identifier}",
        "venue": "Fixture Venue",
        "publicationDate": "2026-08-27",
        "isOpenAccess": True,
        "openAccessPdf": {"url": f"https://example.test/{identifier}.pdf"},
    }


class ReplayHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[str]] = []

    def log_message(self, _format: str, *args: object) -> None:
        return

    def _send(self, status: int, value: object, *, content_type: str = "application/json") -> None:
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self.requests.append(self.path)
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/graph/v1/paper/search":
            phrase = query.get("query", [""])[0]
            offset = int(query.get("offset", ["0"])[0])
            if phrase == "fixture-pagination":
                if offset == 0:
                    self._send(
                        200,
                        {
                            "total": 3,
                            "offset": 0,
                            "next": 2,
                            "data": [_paper("paper-1", "First"), _paper("paper-2", "Second")],
                        },
                    )
                else:
                    self._send(
                        200,
                        {"total": 3, "offset": 2, "data": [_paper("paper-3", "Third")]},
                    )
                return
            if phrase == "fixture-empty":
                self._send(200, {"total": 0, "offset": 0, "data": []})
                return
            if phrase == "fixture-rate-limit":
                self._send(429, {"error": "rate limited"})
                return
            if phrase == "fixture-malformed":
                self._send(200, b"not-json", content_type="application/json")
                return
        if parsed.path.endswith("/DOI%3A10.9999%2Ffixture") or parsed.path.endswith(
            "/DOI:10.9999/fixture"
        ):
            self._send(200, _paper("paper-doi", "DOI fixture"))
            return
        if "/paper/" in parsed.path:
            self._send(404, {"error": "not found"})
            return
        self._send(400, {"error": "unexpected replay request"})


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _can_connect(target: tuple[str, int]) -> bool:
    try:
        connection = socket.create_connection(target, timeout=2)
    except OSError:
        return False
    connection.close()
    return True


def _run_case(
    python: Path,
    adapter: Path,
    request: dict[str, object],
    api_url: str,
) -> dict[str, Any]:
    environment = dict(os.environ)
    for name in list(environment):
        if any(token in name.upper() for token in ("SEMANTIC_SCHOLAR", "S2_API")):
            environment.pop(name)
    environment.update(
        {
            "BLACKRIDGE_EVALUATION_MODE": "1",
            "BLACKRIDGE_SEMANTICSCHOLAR_API_URL": api_url,
        }
    )
    started = perf_counter()
    completed = subprocess.run(
        [str(python), str(adapter)],
        input=json.dumps(request),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=20,
        env=environment,
        check=False,
    )
    duration = round(perf_counter() - started, 3)
    output = json.loads(completed.stdout)
    if not isinstance(output, dict):
        raise AssertionError("adapter stdout JSON is not an object")
    return {
        "request": request,
        "execution": {
            "exit_code": completed.returncode,
            "duration_seconds": duration,
            "stderr": completed.stderr,
        },
        "output": output,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assert-public-network-denied", action="store_true")
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", 0), ReplayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    api_url = f"http://127.0.0.1:{server.server_port}"
    definitions: list[tuple[str, dict[str, object], str, str | None, list[str]]] = [
        (
            "pagination",
            {
                "request_id": "pagination",
                "operation": "search",
                "query": "fixture-pagination",
                "page_size": 2,
                "max_results": 3,
            },
            "ok",
            None,
            ["paper-1", "paper-2", "paper-3"],
        ),
        (
            "empty",
            {"request_id": "empty", "operation": "search", "query": "fixture-empty"},
            "ok",
            None,
            [],
        ),
        (
            "get-paper",
            {
                "request_id": "get-paper",
                "operation": "get-paper",
                "paper_id": "DOI:10.9999/fixture",
            },
            "ok",
            None,
            ["paper-doi"],
        ),
        (
            "not-found",
            {"request_id": "not-found", "operation": "get-paper", "paper_id": "missing"},
            "error",
            "not-found",
            [],
        ),
        (
            "rate-limit",
            {
                "request_id": "rate-limit",
                "operation": "search",
                "query": "fixture-rate-limit",
            },
            "error",
            "rate-limited",
            [],
        ),
        (
            "malformed",
            {
                "request_id": "malformed",
                "operation": "search",
                "query": "fixture-malformed",
            },
            "error",
            "upstream-failure",
            [],
        ),
        (
            "invalid-extra-field",
            {
                "request_id": "invalid",
                "operation": "search",
                "query": "fixture-empty",
                "api_url": "https://attacker.example",
            },
            "error",
            "invalid-request",
            [],
        ),
    ]
    cases: list[dict[str, Any]] = []
    try:
        for case_id, request, status, error_code, paper_ids in definitions:
            before_requests = len(ReplayHandler.requests)
            case = _run_case(args.python.absolute(), args.adapter.resolve(), request, api_url)
            output = case["output"]
            if not isinstance(output, dict):
                raise AssertionError("adapter case output is not an object")
            observed_ids = [
                item.get("paper_id") for item in output.get("papers", []) if isinstance(item, dict)
            ]
            observed_error = output.get("error")
            observed_code = observed_error.get("code") if isinstance(observed_error, dict) else None
            checks = {
                "exit_zero": case["execution"]["exit_code"] == 0,
                "stderr_empty": case["execution"]["stderr"] == "",
                "status": output.get("status") == status,
                "error_code": observed_code == error_code,
                "paper_ids": observed_ids == paper_ids,
                "count_consistent": output.get("returned_count") == len(observed_ids),
                "invalid_request_used_no_network": case_id != "invalid-extra-field"
                or len(ReplayHandler.requests) == before_requests,
            }
            case.update({"case_id": case_id, "checks": checks, "matched": all(checks.values())})
            cases.append(case)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    version_command = subprocess.run(
        [
            str(args.python.absolute()),
            "-c",
            "import importlib.metadata as m,json; print(json.dumps({n:m.version(n) for n in "
            "['semanticscholar','httpx','tenacity']},sort_keys=True))",
        ],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    network_boundary = {
        "required": args.assert_public_network_denied,
        "direct_egress": _can_connect(("1.1.1.1", 443)),
        "dns_egress": _can_connect(("api.semanticscholar.org", 443)),
    }
    network_matched = not args.assert_public_network_denied or not (
        network_boundary["direct_egress"] or network_boundary["dns_egress"]
    )
    observations = {
        "probe_completed": all(case["matched"] for case in cases) and network_matched,
        "adapter_sha256": _sha256(args.adapter.resolve()),
        "lock_sha256": _sha256(args.lock.resolve()),
        "versions": json.loads(version_command.stdout),
        "case_count": len(cases),
        "matched_case_count": sum(bool(case["matched"]) for case in cases),
        "http_request_count": len(ReplayHandler.requests),
        "http_requests": ReplayHandler.requests,
        "network_boundary": network_boundary,
        "cases": cases,
    }
    evidence = {
        "schema_version": "1",
        "probe_id": uuid4().hex,
        "observed_at": datetime.now(UTC).isoformat(),
        "provider": "blackridge-semanticscholar-replay/1",
        "subject": f"semanticscholar-search-v1@{observations['adapter_sha256']}",
        "request": {
            "case_ids": [definition[0] for definition in definitions],
            "assert_public_network_denied": args.assert_public_network_denied,
        },
        "observations": observations,
        "sources": [
            "https://github.com/danielnsilva/semanticscholar/tree/v0.12.0",
            "https://pypi.org/project/semanticscholar/0.12.0/",
            "blackridge://components/semanticscholar-search-v1",
        ],
        "warnings": [
            "Replay evidence verifies the adapter contract, not current public API contents.",
            "Supply-chain provenance and project-posture blockers are reviewed separately.",
        ],
    }
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(
        json.dumps(evidence, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(
        json.dumps(
            {
                key: observations[key]
                for key in ("probe_completed", "case_count", "matched_case_count")
            }
        )
    )
    return 0 if observations["probe_completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
