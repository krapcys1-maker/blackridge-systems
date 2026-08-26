"""Blackridge command-line interface."""

from __future__ import annotations

from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from blackridge.blueprint import build_blueprint
from blackridge.depsdev import DepsDevClient, PackageSystem
from blackridge.doctor import check_tools
from blackridge.errors import BlackridgeError
from blackridge.evidence import ManualReview, ManualVerdict, ProbeEvidence
from blackridge.github import GitHubCli
from blackridge.io import (
    load_probe,
    load_request,
    load_run,
    load_sandbox_experiment,
    write_blueprint,
    write_manual_review,
    write_probe,
    write_run,
)
from blackridge.octocode import DEFAULT_OCTOCODE_PACKAGE, OctocodeDiscovery
from blackridge.quality import OpenSSFScorecardClient
from blackridge.sandbox import SWEREX_SOURCE, SwerexDockerProbe
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
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(".blackridge/discovery.json"),
    limit: Annotated[int, typer.Option(min=1, max=30)] = 10,
    workers: Annotated[int, typer.Option(min=1, max=16)] = 8,
    capability: Annotated[str | None, typer.Option("--capability")] = None,
    octocode_package: Annotated[str, typer.Option()] = DEFAULT_OCTOCODE_PACKAGE,
) -> None:
    """Discover and provisionally rank repositories for a capability specification."""

    try:
        request = load_request(request_file)
        if capability is not None:
            selected = [item for item in request.capabilities if item.id == capability]
            if not selected:
                raise BlackridgeError(f"capability not found in request: {capability}")
            request = request.model_copy(update={"capabilities": selected})
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
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(".blackridge/blueprint.yaml"),
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


@app.command("probe-package")
def probe_package(
    system: Annotated[PackageSystem, typer.Argument()],
    name: Annotated[str, typer.Argument()],
    version: Annotated[str | None, typer.Option()] = None,
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/evidence/package-probe.json"
    ),
) -> None:
    """Collect raw package and dependency facts from deps.dev without approving them."""

    client = DepsDevClient()
    try:
        probe = client.probe_package(system, name, version=version)
        write_probe(probe, output)
    except BlackridgeError as exc:
        failure = ProbeEvidence.failure(
            provider="deps.dev-v3",
            subject=f"{system.value}:{name}@{version or 'default'}",
            request={"system": system.value, "name": name, "version": version},
            sources=client.source_urls(system, name, version=version),
            error=exc,
        )
        try:
            write_probe(failure, output)
        except OSError as write_error:
            console.print(f"[red]Cannot retain failed probe:[/red] {write_error}")
            raise typer.Exit(code=2) from write_error
        console.print(f"[red]Package probe failed:[/red] {exc}")
        console.print(f"Failure evidence written to {output}")
        raise typer.Exit(code=2) from exc
    except (ValidationError, OSError) as exc:
        console.print(f"[red]Package probe failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    selected = probe.observations["selected_version"]
    graph = probe.observations["dependency_graph"]
    console.print(f"[bold]Raw package evidence:[/bold] {probe.subject}")
    console.print(f"Licenses: {selected.get('licenses') or 'unknown'}")
    console.print(f"Advisories: {selected.get('advisories') or 'none reported'}")
    console.print(
        "Dependency graph: "
        f"available={graph.get('available')}, nodes={graph.get('node_count', 'unknown')}, "
        f"direct={graph.get('direct_count', 'unknown')}"
    )
    console.print(f"Evidence written to {output}")
    console.print("[yellow]No PASS/FAIL was assigned. A manual review is still required.[/yellow]")


@app.command("probe-environment")
def probe_environment(
    experiment_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/evidence/environment-probe.json"
    ),
    host_root: Annotated[Path, typer.Option("--host-root")] = Path("."),
) -> None:
    """Run a pinned repository experiment in Docker through SWE-ReX and retain raw evidence."""

    experiment = None
    try:
        experiment = load_sandbox_experiment(experiment_file)
        probe = SwerexDockerProbe().probe(experiment, host_root)
        write_probe(probe, output)
    except (BlackridgeError, ValidationError, OSError) as exc:
        if experiment is not None:
            repository = experiment.repository_url.removesuffix(".git")
            failure = ProbeEvidence.failure(
                provider="swe-rex-docker/1.4.0",
                subject=f"{repository}@{experiment.commit}",
                request=experiment.model_dump(),
                sources=[f"{repository}/commit/{experiment.commit}", SWEREX_SOURCE],
                error=exc,
            )
            with suppress(OSError):
                write_probe(failure, output)
        console.print(f"[red]Environment probe failed:[/red] {exc}")
        if output.exists():
            console.print(f"Failure evidence written to {output}")
        raise typer.Exit(code=2) from exc

    observations = probe.observations
    command_results = observations["commands"]
    failed = next(
        (
            item
            for item in command_results
            if item["transport_error"] is not None or item["exit_code"] != 0
        ),
        None,
    )
    console.print(f"[bold]Raw environment evidence:[/bold] {probe.subject}")
    console.print(f"Resolved image: {observations['image']['resolved_id']}")
    console.print(f"Commands executed: {len(command_results)}")
    console.print(f"Host source unchanged: {observations['host_workspace']['unchanged']}")
    console.print(
        "Container remaining after stop: "
        f"{observations['cleanup']['container_exists_after_stop']}"
    )
    if failed:
        console.print(
            f"[yellow]Captured failure:[/yellow] {failed['id']} exit={failed['exit_code']} "
            f"transport_error={failed['transport_error'] or 'none'}"
        )
    console.print(f"Evidence written to {output}")
    console.print("[yellow]No PASS/FAIL was assigned. A manual review is still required.[/yellow]")


@app.command("review-probe")
def review_probe(
    request_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    probe_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    capability: Annotated[str, typer.Option("--capability")],
    scenario: Annotated[str, typer.Option("--scenario")],
    verdict: Annotated[ManualVerdict, typer.Option("--verdict")],
    reviewer: Annotated[str, typer.Option("--reviewer")],
    observed: Annotated[list[str] | None, typer.Option("--observed")] = None,
    notes: Annotated[str, typer.Option("--notes")] = "Manual comparison completed.",
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/evidence/manual-review.json"
    ),
) -> None:
    """Record a named manual verdict after comparing a probe with a behavior contract."""

    try:
        request = load_request(request_file)
        probe = load_probe(probe_file)
        selected_capability = next(
            (item for item in request.capabilities if item.id == capability), None
        )
        if selected_capability is None:
            raise BlackridgeError(f"capability not found in request: {capability}")
        selected_scenario = next(
            (item for item in selected_capability.acceptance if item.id == scenario), None
        )
        if selected_scenario is None:
            raise BlackridgeError(f"acceptance scenario not found in {capability}: {scenario}")
        if not observed:
            raise BlackridgeError("at least one --observed statement is required")
        review = ManualReview.create(
            reviewer=reviewer,
            verdict=verdict,
            capability_id=capability,
            scenario_id=scenario,
            scenario_description=selected_scenario.description,
            expected=selected_scenario.then,
            observed=observed,
            probe_id=probe.probe_id,
            probe_file=str(probe_file),
            probe_sha256=sha256(probe_file.read_bytes()).hexdigest(),
            notes=notes,
        )
        write_manual_review(review, output)
    except (BlackridgeError, ValidationError, OSError) as exc:
        console.print(f"[red]Manual review failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    console.print(
        f"Manual verdict recorded: {capability}/{scenario} = [bold]{verdict.value}[/bold]"
    )
    console.print(f"Review written to {output}")


if __name__ == "__main__":
    app()
