"""Blackridge command-line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from blackridge.blueprint import build_blueprint
from blackridge.doctor import check_tools
from blackridge.errors import BlackridgeError
from blackridge.github import GitHubCli
from blackridge.io import load_request, load_run, write_blueprint, write_run
from blackridge.octocode import DEFAULT_OCTOCODE_PACKAGE, OctocodeDiscovery
from blackridge.quality import OpenSSFScorecardClient
from blackridge.workflow import discover as run_discovery

app = typer.Typer(
    no_args_is_help=True,
    help="Evidence-driven, reuse-first composition of software systems.",
)
console = Console()


@app.command()
def doctor() -> None:
    """Check the local MVP prerequisites and upcoming sandbox tools."""

    table = Table(title="Blackridge toolchain")
    table.add_column("Tool")
    table.add_column("Status")
    table.add_column("Required now")
    table.add_column("Purpose")
    missing_required = False
    for check in check_tools():
        status = "[green]ready[/green]" if check.available else "[yellow]missing[/yellow]"
        required = "yes" if check.required_for_mvp else "later"
        table.add_row(check.name, status, required, check.purpose)
        missing_required |= check.required_for_mvp and not check.available
    console.print(table)
    if missing_required:
        raise typer.Exit(code=1)


@app.command()
def discover(
    request_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/discovery.json"
    ),
    limit: Annotated[int, typer.Option(min=1, max=30)] = 10,
    workers: Annotated[int, typer.Option(min=1, max=16)] = 8,
    octocode_package: Annotated[str, typer.Option()] = DEFAULT_OCTOCODE_PACKAGE,
) -> None:
    """Discover and provisionally rank repositories for a capability specification."""

    try:
        request = load_request(request_file)
        run = run_discovery(
            request,
            discovery=OctocodeDiscovery(package=octocode_package),
            github=GitHubCli(),
            scorecard=OpenSSFScorecardClient(),
            limit=limit,
            workers=workers,
        )
        write_run(run, output)
    except (BlackridgeError, ValidationError, OSError) as exc:
        console.print(f"[red]Discovery failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    candidate_count = sum(len(result.candidates) for result in run.results)
    console.print(
        f"[green]Discovery complete.[/green] {candidate_count} candidates written to {output}"
    )
    console.print("These are L0 inspection targets, not approved components.")


@app.command()
def report(
    run_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    top: Annotated[int, typer.Option(min=1, max=20)] = 5,
) -> None:
    """Render a compact report from a discovery run."""

    try:
        run = load_run(run_file)
    except (ValidationError, OSError) as exc:
        console.print(f"[red]Cannot read discovery run:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    console.print(f"[bold]{run.request.name}[/bold] — {run.request.goal}")
    for result in run.results:
        table = Table(title=f"Capability: {result.capability.id}")
        table.add_column("Repository")
        table.add_column("Score", justify="right")
        table.add_column("License")
        table.add_column("Evidence")
        table.add_column("Decision")
        for candidate in result.candidates[:top]:
            table.add_row(
                candidate.metadata.full_name,
                f"{candidate.score.total:.2f}",
                candidate.metadata.license_spdx or "unknown",
                f"L{int(candidate.evidence_level)}",
                candidate.decision,
            )
        if not result.candidates:
            table.add_row("—", "—", "—", "—", "no candidates")
        console.print(table)


@app.command()
def blueprint(
    run_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/blueprint.yaml"
    ),
) -> None:
    """Create a provisional component map that preserves the remaining evidence gates."""

    try:
        run = load_run(run_file)
        result = build_blueprint(run)
        write_blueprint(result, output)
    except (ValidationError, OSError) as exc:
        console.print(f"[red]Cannot create blueprint:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(f"[green]Provisional blueprint written to {output}[/green]")
    console.print("No component is release-ready until sandbox and contract gates pass.")


if __name__ == "__main__":
    app()
