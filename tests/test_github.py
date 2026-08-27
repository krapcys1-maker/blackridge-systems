from __future__ import annotations

import pytest

from blackridge.errors import ExternalToolError
from blackridge.github import GitHubCli
from blackridge.models import RepositoryMetadata


def test_github_rejects_non_object_and_invalid_repository_metadata() -> None:
    current = RepositoryMetadata(full_name="example/repo", url="https://github.com/example/repo")

    with pytest.raises(ExternalToolError, match="non-object JSON"):
        GitHubCli(execute=lambda _argv: "[]").enrich(current)

    with pytest.raises(ExternalToolError, match="invalid repository metadata"):
        GitHubCli(execute=lambda _argv: '{"stargazers_count": -1, "license": null}').enrich(current)
