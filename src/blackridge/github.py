"""Official GitHub CLI adapter for metadata enrichment."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

from blackridge.errors import ExternalToolError
from blackridge.models import RepositoryMetadata
from blackridge.runner import CommandRunner


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


class GitHubCli:
    """Fetches repository facts through the user's authenticated `gh` installation."""

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        execute: Callable[[list[str]], str] | None = None,
    ) -> None:
        self.runner = runner or CommandRunner()
        self._execute = execute

    def enrich(self, current: RepositoryMetadata) -> RepositoryMetadata:
        argv = ["gh", "api", f"repos/{current.full_name}"]
        raw = self._execute(argv) if self._execute else self.runner.run(argv).stdout
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            message = f"GitHub returned invalid JSON for {current.full_name}"
            raise ExternalToolError(message) from exc

        license_data = data.get("license") or {}
        return current.model_copy(
            update={
                "url": data.get("html_url") or current.url,
                "description": data.get("description") or current.description,
                "stars": data.get("stargazers_count", current.stars),
                "forks": data.get("forks_count", current.forks),
                "open_issues": data.get("open_issues_count", current.open_issues),
                "pushed_at": _parse_datetime(data.get("pushed_at")) or current.pushed_at,
                "updated_at": _parse_datetime(data.get("updated_at")) or current.updated_at,
                "created_at": _parse_datetime(data.get("created_at")) or current.created_at,
                "language": data.get("language") or current.language,
                "topics": data.get("topics") or current.topics,
                "license_spdx": license_data.get("spdx_id"),
                "archived": bool(data.get("archived", False)),
                "is_fork": bool(data.get("fork", False)),
                "default_branch": data.get("default_branch"),
            }
        )
