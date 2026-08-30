"""Blackridge command-line interface."""

from __future__ import annotations

import json
from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import ValidationError
from rich.console import Console

from blackridge.adaptation import (
    JSON_PATCH_SOURCE,
    JSON_SCHEMA_SOURCE,
    CompositionPairProbe,
    JsonPatchAdapterProbe,
)
from blackridge.benchmark import (
    BENCHMARK_SOURCE,
    BenchmarkCalibrationProbe,
    BenchmarkComparisonProbe,
    BenchmarkEvaluator,
)
from blackridge.blueprint import build_blueprint
from blackridge.composition import (
    COMPOSITION_SOURCE,
    CompositionSystemProbe,
    generate_system,
    run_generated_system,
    run_generated_system_sandboxed,
    solve_composition,
)
from blackridge.depsdev import DepsDevClient, PackageSystem
from blackridge.doctor import check_tools
from blackridge.errors import BlackridgeError
from blackridge.evidence import (
    EvidencePromotion,
    ManualReview,
    ManualVerdict,
    ProbeEvidence,
)
from blackridge.evolution import (
    ChallengerProposalRejected,
    propose_challenger_architecture,
    repair_challenger_interfaces,
    select_champion,
)
from blackridge.generation import (
    GenerationProposalRejected,
    materialize_proposal,
    propose_gap_system,
)
from blackridge.github import GitHubCli, GitHubSearchDiscovery
from blackridge.holdout import verify_sealed_holdout
from blackridge.io import (
    load_adapter_experiment,
    load_challenger_rejection,
    load_composition_definition,
    load_composition_plan,
    load_evolution_round,
    load_generated_proposal,
    load_probe,
    load_request,
    load_run,
    load_sandbox_experiment,
    load_supply_chain_experiment,
    load_verified_components,
    write_blueprint,
    write_challenger_interface_repair,
    write_challenger_proposal,
    write_challenger_proposal_record,
    write_challenger_rejection,
    write_champion_selection,
    write_composition_plan,
    write_generated_proposal,
    write_generation_record,
    write_generation_rejection,
    write_manual_review,
    write_planning_record,
    write_probe,
    write_request,
    write_run,
)
from blackridge.models import EvidenceLevel
from blackridge.octocode import DEFAULT_OCTOCODE_PACKAGE, OctocodeDiscovery
from blackridge.operator import DeepSeekBackend, load_secret
from blackridge.planning import plan_system
from blackridge.provenance import (
    audit_source_provenance,
    load_provenance_manifest,
    load_source_audit_definition,
    provenance_gate,
)
from blackridge.quality import OpenSSFScorecardClient
from blackridge.release_compliance import (
    DEFAULT_SYFT_IMAGE,
    load_distribution_manifest,
    probe_image_release,
    probe_wheel_release,
    write_or_check_notices,
)
from blackridge.sandbox import SWEREX_SOURCE, SwerexDockerProbe
from blackridge.supply_chain import SupplyChainProbe
from blackridge.workflow import discover as run_discovery

app = typer.Typer(
    no_args_is_help=True,
    help="Evidence-driven, reuse-first composition of software systems.",
)
console = Console()


def _publish_completed_artifact(probe: ProbeEvidence, output: Path) -> bool:
    """Publish the normal output channel only for a completed system run."""

    if probe.observations.get("all_steps_completed") is not True:
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(probe.observations["final_artifact"], indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return True


@app.command("select-champion")
def select_evolution_champion(
    round_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/evolution/champion-selection.json"
    ),
) -> None:
    """Select a round winner using frozen gates, metrics, and evaluator identity."""

    try:
        selection = select_champion(load_evolution_round(round_file))
        write_champion_selection(selection, output)
    except (ValidationError, OSError, ValueError) as exc:
        console.print(f"[red]Champion selection failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(
        f"[green]Selected {selection.selected_candidate_id} "
        f"({selection.selected_architecture_line}).[/green]"
    )
    console.print(f"Selection evidence written to {output}")


@app.command("propose-challenger")
def propose_evolution_challenger(
    public_brief_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    public_benchmark_file: Annotated[
        Path, typer.Option("--benchmark", exists=True, dir_okay=False, readable=True)
    ],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/evolution/challenger-v2-proposal.json"
    ),
    evidence: Annotated[Path, typer.Option("--evidence")] = Path(
        ".blackridge/evolution/challenger-v2-evidence.json"
    ),
    rejection_evidence: Annotated[Path, typer.Option("--rejection-evidence")] = Path(
        ".blackridge/evolution/challenger-v2-rejection.json"
    ),
    review_feedback_file: Annotated[
        Path | None,
        typer.Option("--review-feedback", exists=True, dir_okay=False, readable=True),
    ] = None,
    env_file: Annotated[Path, typer.Option("--env-file")] = Path(".env"),
    model: Annotated[str, typer.Option()] = "deepseek-v4-flash",
    max_cost_usd: Annotated[float, typer.Option(min=0.02, max=10.0)] = 2.0,
) -> None:
    """Propose fresh architecture B from public inputs, without champion source."""

    try:
        backend = DeepSeekBackend(
            api_key=load_secret("DEEPSEEK_API_KEY", env_file=env_file),
            model=model,
            max_total_cost_usd=max_cost_usd,
        )
        proposal, record = propose_challenger_architecture(
            public_brief_file.read_text(encoding="utf-8"),
            public_benchmark_file.read_text(encoding="utf-8"),
            backend=backend,
            review_feedback=(
                review_feedback_file.read_text(encoding="utf-8")
                if review_feedback_file is not None
                else None
            ),
        )
        write_challenger_proposal(proposal, output)
        write_challenger_proposal_record(record, evidence)
    except ChallengerProposalRejected as exc:
        try:
            write_challenger_rejection(exc.record, rejection_evidence)
        except OSError as write_error:
            console.print(f"[red]Cannot retain rejected challenger:[/red] {write_error}")
            raise typer.Exit(code=2) from write_error
        console.print(f"[red]Challenger proposal failed:[/red] {exc}")
        console.print(f"Rejected completion retained at {rejection_evidence}")
        raise typer.Exit(code=2) from exc
    except (BlackridgeError, ValidationError, OSError, ValueError) as exc:
        console.print(f"[red]Challenger proposal failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(f"[green]Fresh v2 architecture proposal written to {output}[/green]")
    console.print(f"Proposal evidence written to {evidence}")
    console.print(f"Manual review required for SHA-256: {record.proposal_sha256}")


@app.command("repair-challenger-interfaces")
def repair_evolution_challenger_interfaces(
    rejection_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    approved_completion_sha256: Annotated[str, typer.Option("--approved-completion-sha256")],
    output: Annotated[Path, typer.Option("--output", "-o")],
    evidence: Annotated[Path, typer.Option("--evidence")],
) -> None:
    """Repair only flow-interface declarations in an exact rejected challenger."""

    try:
        proposal, record = repair_challenger_interfaces(
            load_challenger_rejection(rejection_file),
            approved_completion_sha256=approved_completion_sha256,
        )
        write_challenger_proposal(proposal, output)
        write_challenger_interface_repair(record, evidence)
    except (ValidationError, OSError, ValueError) as exc:
        console.print(f"[red]Challenger interface repair failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(f"[green]Mechanically repaired challenger written to {output}[/green]")
    console.print(f"Repair evidence written to {evidence}")
    console.print(f"Manual review required for SHA-256: {record.proposal_sha256}")


@app.command("plan")
def plan_brief(
    brief_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/system-request.yaml"
    ),
    evidence: Annotated[Path, typer.Option("--evidence")] = Path(
        ".blackridge/evidence/planning.json"
    ),
    provider: Annotated[Literal["deepseek"], typer.Option()] = "deepseek",
    env_file: Annotated[Path, typer.Option("--env-file")] = Path(".env"),
    model: Annotated[str, typer.Option()] = "deepseek-v4-flash",
    max_cost_usd: Annotated[float, typer.Option(min=0.01, max=10.0)] = 2.0,
) -> None:
    """Turn a natural-language brief into a validated capability request."""

    try:
        brief = brief_file.read_text(encoding="utf-8")
        if provider != "deepseek":  # pragma: no cover - Typer enforces the literal.
            raise BlackridgeError(f"unsupported operator provider: {provider}")
        backend = DeepSeekBackend(
            api_key=load_secret("DEEPSEEK_API_KEY", env_file=env_file),
            model=model,
            max_total_cost_usd=max_cost_usd,
        )
        request, record = plan_system(brief, backend=backend)
        write_request(request, output)
        write_planning_record(record, evidence)
    except (BlackridgeError, ValidationError, OSError, ValueError) as exc:
        console.print(f"[red]Planning failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(f"[green]Capability request written to {output}[/green]")
    console.print(f"Planning evidence written to {evidence}")
    console.print("The plan is a model proposal; no component has been functionally verified.")


@app.command("propose-gap")
def propose_gap(
    brief_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    request_file: Annotated[
        Path, typer.Option("--request", exists=True, dir_okay=False, readable=True)
    ],
    discovery_file: Annotated[
        Path, typer.Option("--discovery", exists=True, dir_okay=False, readable=True)
    ],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/generated-proposal.json"
    ),
    evidence: Annotated[Path, typer.Option("--evidence")] = Path(
        ".blackridge/evidence/generation.json"
    ),
    env_file: Annotated[Path, typer.Option("--env-file")] = Path(".env"),
    model: Annotated[str, typer.Option()] = "deepseek-v4-flash",
    max_cost_usd: Annotated[float, typer.Option(min=0.02, max=10.0)] = 2.0,
    verified_components_file: Annotated[
        Path | None,
        typer.Option("--verified-components", exists=True, dir_okay=False, readable=True),
    ] = None,
    public_evaluator_file: Annotated[
        Path | None,
        typer.Option("--public-evaluator", exists=True, dir_okay=False, readable=True),
    ] = None,
    review_feedback_file: Annotated[
        Path | None,
        typer.Option("--review-feedback", exists=True, dir_okay=False, readable=True),
    ] = None,
    rejection_evidence: Annotated[Path, typer.Option("--rejection-evidence")] = Path(
        ".blackridge/evidence/generation-rejection.json"
    ),
) -> None:
    """Propose only the remaining code gap after discovery; never execute it."""

    try:
        brief = brief_file.read_text(encoding="utf-8")
        request = load_request(request_file)
        discovery = load_run(discovery_file)
        backend = DeepSeekBackend(
            api_key=load_secret("DEEPSEEK_API_KEY", env_file=env_file),
            model=model,
            max_total_cost_usd=max_cost_usd,
        )
        proposal, record = propose_gap_system(
            brief,
            request=request,
            discovery=discovery,
            backend=backend,
            verified_components=(
                load_verified_components(verified_components_file)
                if verified_components_file is not None
                else None
            ),
            public_evaluator_contract=(
                public_evaluator_file.read_text(encoding="utf-8")
                if public_evaluator_file is not None
                else None
            ),
            review_feedback=(
                review_feedback_file.read_text(encoding="utf-8")
                if review_feedback_file is not None
                else None
            ),
        )
        write_generated_proposal(proposal, output)
        write_generation_record(record, evidence)
    except GenerationProposalRejected as exc:
        try:
            write_generation_rejection(exc.record, rejection_evidence)
        except OSError as write_error:
            console.print(f"[red]Cannot retain rejected proposal:[/red] {write_error}")
            raise typer.Exit(code=2) from write_error
        console.print(f"[red]Gap proposal failed:[/red] {exc}")
        console.print(f"Rejected completion retained at {rejection_evidence}")
        raise typer.Exit(code=2) from exc
    except (BlackridgeError, ValidationError, OSError, ValueError) as exc:
        console.print(f"[red]Gap proposal failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(f"[green]Gap proposal written to {output}[/green]")
    console.print(f"Generation evidence written to {evidence}")
    console.print(f"Manual review required for proposal SHA-256: {record.proposal_sha256}")


@app.command("materialize-proposal")
def materialize_generated_proposal(
    proposal_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    workspace: Annotated[Path, typer.Option("--workspace")],
    approved_sha256: Annotated[str, typer.Option("--approved-sha256")],
) -> None:
    """Materialize an exact reviewed proposal without executing its command."""

    try:
        proposal = load_generated_proposal(proposal_file)
        hashes = materialize_proposal(
            proposal,
            workspace,
            approved_sha256=approved_sha256,
        )
    except (ValidationError, OSError, ValueError) as exc:
        console.print(f"[red]Materialization failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(f"[green]Materialized {len(hashes)} reviewed file(s) in {workspace}.[/green]")
    console.print("Nothing was executed; sandbox verification is still required.")


@app.command()
def doctor() -> None:
    """Check the local MVP prerequisites and upcoming sandbox tools."""

    console.print("Blackridge toolchain")
    missing_required = False
    for check in check_tools():
        status = "ready" if check.available else "missing"
        required = "yes" if check.required_for_mvp else "later"
        # Keep the complete diagnostic in captured output and legacy Windows
        # terminals. Rich tables otherwise truncate with a Unicode ellipsis or
        # split tool names according to the current terminal width.
        console.print(
            f"- {check.name}: {status}; required={required}; "
            f"purpose={check.purpose}; diagnostic={check.detail}",
            markup=False,
            soft_wrap=True,
        )
        missing_required |= check.required_for_mvp and not check.available
    if missing_required:
        raise typer.Exit(code=1)


@app.command()
def discover(
    request_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(".blackridge/discovery.json"),
    limit: Annotated[int, typer.Option(min=1, max=30)] = 10,
    workers: Annotated[int, typer.Option(min=1, max=16)] = 8,
    capability: Annotated[str | None, typer.Option("--capability")] = None,
    search_provider: Annotated[Literal["octocode", "github"], typer.Option("--provider")] = (
        "octocode"
    ),
    max_queries: Annotated[int, typer.Option(min=1, max=100)] = 60,
    allow_partial_budget: Annotated[bool, typer.Option("--allow-partial-budget")] = False,
    deny_repository: Annotated[list[str] | None, typer.Option("--deny-repository")] = None,
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
        discovery = (
            GitHubSearchDiscovery(
                max_queries=max_queries,
                partial_on_budget_exhaustion=allow_partial_budget,
            )
            if search_provider == "github"
            else OctocodeDiscovery(package=octocode_package)
        )
        run = run_discovery(
            request,
            discovery=discovery,
            github=GitHubCli(),
            scorecard=OpenSSFScorecardClient(),
            limit=limit,
            workers=workers,
            denied_repositories=set(deny_repository or []),
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

    console.print(f"[bold]{run.request.name}[/bold] - {run.request.goal}")
    for result in run.results:
        console.print(f"Capability: {result.capability.id}", markup=False)
        for candidate in result.candidates[:top]:
            console.print(
                f"- {candidate.metadata.full_name}: score={candidate.score.total:.2f}; "
                f"license={candidate.metadata.license_spdx or 'unknown'}; "
                f"evidence=L{int(candidate.evidence_level)}; decision={candidate.decision}",
                markup=False,
                soft_wrap=True,
            )
        if not result.candidates:
            console.print("- no candidates")


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


def _environment_probe_gate_failures(probe: ProbeEvidence) -> list[str]:
    """Return fail-closed automation reasons without changing raw probe evidence."""

    observations = probe.observations
    failures: list[str] = []
    if observations.get("probe_completed") is not True:
        failures.append("probe did not complete")

    boundary = observations.get("execution_boundary")
    if not isinstance(boundary, dict):
        failures.append("execution boundary evidence is missing")
    elif boundary.get("requested") == "none" and boundary.get("applied") is not True:
        failures.append("requested network isolation was not applied")

    commands = observations.get("commands")
    if not isinstance(commands, list):
        failures.append("command evidence is missing")
    else:
        for item in commands:
            if not isinstance(item, dict):
                failures.append("command evidence contains an invalid record")
                continue
            command_id = str(item.get("id") or "unknown")
            transport_error = item.get("transport_error")
            if transport_error is not None:
                failures.append(f"command {command_id} transport failed: {transport_error}")
            elif item.get("exit_code") != 0:
                failures.append(f"command {command_id} exited with code {item.get('exit_code')}")

    not_run = observations.get("not_run_command_ids")
    if not isinstance(not_run, list):
        failures.append("not-run command evidence is missing")
    elif not_run:
        failures.append(f"commands were not run: {', '.join(str(item) for item in not_run)}")

    host_workspace = observations.get("host_workspace")
    if not isinstance(host_workspace, dict) or host_workspace.get("unchanged") is not True:
        failures.append("host workspace integrity was not confirmed")

    cleanup = observations.get("cleanup")
    if not isinstance(cleanup, dict):
        failures.append("container cleanup evidence is missing")
    else:
        if cleanup.get("stop_error"):
            failures.append(f"container stop failed: {cleanup['stop_error']}")
        force_remove = cleanup.get("force_remove")
        if isinstance(force_remove, dict) and force_remove.get("exit_code") != 0:
            failures.append(
                f"forced container cleanup exited with code {force_remove.get('exit_code')}"
            )
        if cleanup.get("container_exists_after_stop") is not False:
            failures.append("container cleanup was not confirmed")

    return failures


@app.command("probe-environment")
def probe_environment(
    experiment_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/evidence/environment-probe.json"
    ),
    host_root: Annotated[Path, typer.Option("--host-root")] = Path("."),
    require_complete: Annotated[
        bool,
        typer.Option(
            "--require-complete",
            help="Exit nonzero unless commands, isolation, host integrity, and cleanup all pass.",
        ),
    ] = False,
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
        "Container remaining after cleanup: "
        f"{observations['cleanup']['container_exists_after_stop']}"
    )
    if failed:
        console.print(
            f"[yellow]Captured failure:[/yellow] {failed['id']} exit={failed['exit_code']} "
            f"transport_error={failed['transport_error'] or 'none'}"
        )
    console.print(f"Evidence written to {output}")
    if require_complete:
        failures = _environment_probe_gate_failures(probe)
        if failures:
            console.print("[red]Strict environment gate: FAIL[/red]")
            for reason in failures:
                console.print(f"- {reason}")
            raise typer.Exit(code=1)
        console.print("[green]Strict environment gate: PASS[/green]")
    else:
        console.print(
            "[yellow]No PASS/FAIL was assigned. A manual review is still required.[/yellow]"
        )


@app.command("probe-adapter")
def probe_adapter(
    experiment_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/evidence/adapter-probe.json"
    ),
) -> None:
    """Apply a declarative JSON Patch and retain before/after contract evidence."""

    experiment = None
    try:
        experiment = load_adapter_experiment(experiment_file)
        probe = JsonPatchAdapterProbe().probe(experiment)
        write_probe(probe, output)
    except (BlackridgeError, ValidationError, OSError) as exc:
        if experiment is not None:
            failure = ProbeEvidence.failure(
                provider="jsonpatch-rfc6902+jsonschema-draft2020-12",
                subject=experiment.name,
                request=experiment.model_dump(),
                sources=[JSON_PATCH_SOURCE, JSON_SCHEMA_SOURCE],
                error=exc,
            )
            with suppress(OSError):
                write_probe(failure, output)
        console.print(f"[red]Adapter probe failed:[/red] {exc}")
        if output.exists():
            console.print(f"Failure evidence written to {output}")
        raise typer.Exit(code=2) from exc

    before = probe.observations["before_adapter"]
    after = probe.observations["after_adapter"]
    patch = probe.observations["patch"]
    preservation = probe.observations["preservation"]
    console.print(f"[bold]Raw adapter evidence:[/bold] {probe.subject}")
    console.print(f"Target valid before adapter: {before['target_contract_valid']}")
    console.print(f"Patch error: {patch['error'] or 'none'}")
    console.print(f"Target valid after adapter: {after['target_contract_valid']}")
    console.print(f"All source values preserved: {preservation['all_source_values_preserved']}")
    console.print(f"Evidence written to {output}")
    console.print("[yellow]No PASS/FAIL was assigned. A manual review is still required.[/yellow]")


@app.command("probe-composition")
def probe_composition(
    working_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    broken_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/evidence/composition-probe.json"
    ),
) -> None:
    """Compare a working and one-operation-broken composition on the same workload."""

    working = None
    broken = None
    try:
        working = load_adapter_experiment(working_file)
        broken = load_adapter_experiment(broken_file)
        probe = CompositionPairProbe().probe(working, broken)
        write_probe(probe, output)
    except (BlackridgeError, ValidationError, OSError) as exc:
        if working is not None and broken is not None:
            failure = ProbeEvidence.failure(
                provider="blackridge-composition-pair/jsonpatch+jsonschema",
                subject=f"{working.name}::vs::{broken.name}",
                request={
                    "working": working.model_dump(),
                    "deliberate_negative": broken.model_dump(),
                },
                sources=[JSON_PATCH_SOURCE, JSON_SCHEMA_SOURCE],
                error=exc,
            )
            with suppress(OSError):
                write_probe(failure, output)
        console.print(f"[red]Composition probe failed:[/red] {exc}")
        if output.exists():
            console.print(f"Failure evidence written to {output}")
        raise typer.Exit(code=2) from exc

    comparison = probe.observations["artifact_comparison"]
    difference = probe.observations["adapter_difference"]
    console.print(f"[bold]Raw composition evidence:[/bold] {probe.subject}")
    console.print(
        "Both patch applications returned without error: "
        f"{comparison['both_patch_applications_returned_without_error']}"
    )
    console.print(f"Working target valid: {comparison['working_target_contract_valid']}")
    console.print(
        f"Deliberate negative target valid: {comparison['negative_target_contract_valid']}"
    )
    console.print(f"Removed operations in negative: {len(difference['removed_operations'])}")
    console.print(f"Evidence written to {output}")
    console.print("[yellow]No PASS/FAIL was assigned. A manual review is still required.[/yellow]")


@app.command("probe-supply-chain")
def probe_supply_chain(
    experiment_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/evidence/supply-chain-probe.json"
    ),
    work_root: Annotated[Path, typer.Option("--work-root")] = Path(".blackridge/supply-chain/work"),
    artifact_dir: Annotated[Path, typer.Option("--artifact-dir")] = Path(
        ".blackridge/supply-chain/artifacts"
    ),
) -> None:
    """Run independent license, SBOM, vulnerability, posture, and provenance probes."""

    experiment = None
    try:
        experiment = load_supply_chain_experiment(experiment_file)
        probe = SupplyChainProbe().probe(
            experiment,
            work_root=work_root,
            artifact_dir=artifact_dir,
        )
        write_probe(probe, output)
    except (BlackridgeError, ValidationError, OSError) as exc:
        if experiment is not None:
            failure = ProbeEvidence.failure(
                provider="github+deps.dev+scorecard+syft+osv-scanner+pypi-integrity",
                subject=(
                    f"{experiment.repository}@{experiment.commit}::"
                    f"{experiment.package_system.value}:{experiment.package_name}"
                    f"@{experiment.package_version}"
                ),
                request=experiment.model_dump(),
                sources=[f"https://github.com/{experiment.repository}/commit/{experiment.commit}"],
                error=exc,
            )
            with suppress(OSError):
                write_probe(failure, output)
        console.print(f"[red]Supply-chain probe failed:[/red] {exc}")
        if output.exists():
            console.print(f"Failure evidence written to {output}")
        raise typer.Exit(code=2) from exc

    observations = probe.observations
    console.print(f"[bold]Raw supply-chain evidence:[/bold] {probe.subject}")
    console.print(
        f"Repository license: {observations['repository_license']['spdx_id'] or 'unknown'}"
    )
    console.print(
        "Direct dependency license concerns: "
        f"{observations['dependency_licenses']['concern_count']}"
    )
    console.print(f"Scorecard: {observations['security_posture']['scorecard']['status']}")
    console.print(
        "Vulnerable lock-scope package entries: "
        f"{observations['known_vulnerabilities']['vulnerable_package_entry_count']}"
    )
    console.print(
        "PyPI provenance: "
        f"{observations['release_provenance']['status']}; "
        f"missing files={len(observations['release_provenance']['missing_files'])}"
    )
    console.print(f"Evidence written to {output}")
    console.print("[yellow]No PASS/FAIL was assigned. A manual review is still required.[/yellow]")


@app.command("benchmark-evaluate")
def benchmark_evaluate(
    definition_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    run_plan_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/evidence/benchmark-run-probe.json"
    ),
) -> None:
    """Execute frozen benchmark cases and retain artifact-level observations."""

    try:
        probe = BenchmarkEvaluator().evaluate(definition_file, run_plan_file)
        write_probe(probe, output)
    except (BlackridgeError, ValidationError, OSError) as exc:
        failure = ProbeEvidence.failure(
            provider="blackridge-benchmark-harness/1",
            subject=f"{definition_file}::{run_plan_file}",
            request={
                "definition_file": str(definition_file),
                "run_plan_file": str(run_plan_file),
            },
            sources=[BENCHMARK_SOURCE],
            error=exc,
        )
        with suppress(OSError):
            write_probe(failure, output)
        console.print(f"[red]Benchmark evaluation failed:[/red] {exc}")
        if output.exists():
            console.print(f"Failure evidence written to {output}")
        raise typer.Exit(code=2) from exc

    observations = probe.observations
    console.print(f"[bold]Raw benchmark evidence:[/bold] {probe.subject}")
    console.print(
        "Critical checks matched: "
        f"{observations['matched_critical_check_count']}/"
        f"{observations['critical_check_count']}"
    )
    console.print(f"All critical checks matched: {observations['all_critical_matched']}")
    console.print(f"Evidence written to {output}")
    console.print("[yellow]No manual PASS/FAIL was assigned.[/yellow]")


@app.command("benchmark-calibrate")
def benchmark_calibrate(
    definition_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    reference_plan_file: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, readable=True)
    ],
    broken_plan_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/evidence/benchmark-calibration-probe.json"
    ),
) -> None:
    """Compare a known-good fixture with a green-exit broken control."""

    try:
        probe = BenchmarkCalibrationProbe().probe(
            definition_file,
            reference_plan_file,
            broken_plan_file,
        )
        write_probe(probe, output)
    except (BlackridgeError, ValidationError, OSError) as exc:
        failure = ProbeEvidence.failure(
            provider="blackridge-benchmark-calibration/1",
            subject=f"{reference_plan_file}::vs::{broken_plan_file}",
            request={
                "definition_file": str(definition_file),
                "reference_plan_file": str(reference_plan_file),
                "broken_plan_file": str(broken_plan_file),
            },
            sources=[BENCHMARK_SOURCE],
            error=exc,
        )
        with suppress(OSError):
            write_probe(failure, output)
        console.print(f"[red]Benchmark calibration failed:[/red] {exc}")
        if output.exists():
            console.print(f"Failure evidence written to {output}")
        raise typer.Exit(code=2) from exc

    comparison = probe.observations["comparison"]
    console.print(f"[bold]Raw benchmark calibration:[/bold] {probe.subject}")
    console.print(f"Reference all critical matched: {comparison['reference_all_critical_matched']}")
    console.print(
        f"Broken processes all exited zero: {comparison['broken_all_processes_exited_zero']}"
    )
    console.print(f"Broken all critical matched: {comparison['broken_all_critical_matched']}")
    console.print(f"Detected broken checks: {comparison['detected_broken_check_count']}")
    console.print(f"Evidence written to {output}")
    console.print("[yellow]No manual PASS/FAIL was assigned.[/yellow]")


@app.command("benchmark-compare")
def benchmark_compare(
    definition_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    baseline_plan_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    blackridge_plan_file: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, readable=True)
    ],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/evidence/benchmark-comparison-probe.json"
    ),
) -> None:
    """Run one controlled from-scratch versus Blackridge pair."""

    try:
        probe = BenchmarkComparisonProbe().probe(
            definition_file,
            baseline_plan_file,
            blackridge_plan_file,
        )
        write_probe(probe, output)
    except (BlackridgeError, ValidationError, OSError) as exc:
        failure = ProbeEvidence.failure(
            provider="blackridge-benchmark-comparison/1",
            subject=f"{baseline_plan_file}::vs::{blackridge_plan_file}",
            request={
                "definition_file": str(definition_file),
                "baseline_plan_file": str(baseline_plan_file),
                "blackridge_plan_file": str(blackridge_plan_file),
            },
            sources=[BENCHMARK_SOURCE],
            error=exc,
        )
        with suppress(OSError):
            write_probe(failure, output)
        console.print(f"[red]Benchmark comparison failed:[/red] {exc}")
        if output.exists():
            console.print(f"Failure evidence written to {output}")
        raise typer.Exit(code=2) from exc

    baseline = probe.observations["baseline"]
    blackridge = probe.observations["blackridge"]
    console.print(f"[bold]Raw controlled comparison:[/bold] {probe.subject}")
    console.print(
        f"Baseline: task_success={baseline['task_success']}, "
        f"critical_match_rate={baseline['critical_match_rate']}"
    )
    console.print(
        f"Blackridge: task_success={blackridge['task_success']}, "
        f"critical_match_rate={blackridge['critical_match_rate']}"
    )
    console.print("Automatic winner: none")
    console.print(f"Evidence written to {output}")
    console.print("[yellow]A named manual comparison is still required.[/yellow]")


@app.command("compose-solve")
def compose_solve(
    definition_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/composition-plan.yaml"
    ),
) -> None:
    """Solve a frozen component-and-contract compatibility problem."""

    try:
        definition = load_composition_definition(definition_file)
        plan = solve_composition(definition, definition_file=definition_file)
        write_composition_plan(plan, output)
    except (BlackridgeError, ValidationError, OSError) as exc:
        console.print(f"[red]Compatibility solving failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(f"Compatibility plan complete: {plan.complete}")
    console.print(f"Selected components: {', '.join(plan.selected_component_ids) or 'none'}")
    console.print(f"Selected adapters: {', '.join(plan.selected_adapter_ids) or 'none'}")
    console.print(f"Plan written to {output}")
    console.print("[yellow]No L4 or release verdict was assigned.[/yellow]")
    if not plan.complete:
        raise typer.Exit(code=1)


@app.command("compose-generate")
def compose_generate(
    definition_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    plan_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    output_directory: Annotated[Path, typer.Argument()],
) -> None:
    """Generate a provenance-locked system from a complete compatibility plan."""

    try:
        definition = load_composition_definition(definition_file)
        plan = load_composition_plan(plan_file)
        generated = generate_system(
            definition,
            plan,
            definition_file=definition_file,
            output_directory=output_directory,
        )
    except (BlackridgeError, ValidationError, OSError) as exc:
        console.print(f"[red]System generation failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(f"Generated system written to {generated.output_directory}")
    console.print(f"Execution ready: {generated.execution_ready}")
    console.print(f"Release ready: {generated.release_ready}")
    console.print(f"Trusted provenance SHA-256: {generated.artifact_sha256['provenance.json']}")
    console.print("[yellow]Generation completeness is not an L4 verdict.[/yellow]")


@app.command("compose-run")
def compose_run(
    bundle_directory: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    input_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    provenance_sha256: Annotated[str, typer.Option("--provenance-sha256")],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/composition-output.json"
    ),
    evidence: Annotated[Path, typer.Option("--evidence")] = Path(
        ".blackridge/evidence/composition-run-probe.json"
    ),
) -> None:
    """Execute a generated calibration system and retain every contract boundary."""

    try:
        input_artifact = json.loads(input_file.read_text(encoding="utf-8"))
        probe = run_generated_system(
            bundle_directory,
            input_artifact,
            expected_provenance_sha256=provenance_sha256,
        )
        write_probe(probe, evidence)
        output_published = _publish_completed_artifact(probe, output)
    except (BlackridgeError, ValidationError, OSError, json.JSONDecodeError) as exc:
        failure = ProbeEvidence.failure(
            provider="blackridge-generated-graph-runtime/1",
            subject=str(bundle_directory),
            request={
                "bundle_directory": str(bundle_directory),
                "input_file": str(input_file),
                "expected_provenance_sha256": provenance_sha256,
            },
            sources=[COMPOSITION_SOURCE],
            error=exc,
        )
        with suppress(OSError):
            write_probe(failure, evidence)
        console.print(f"[red]Generated system execution failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    complete = probe.observations["all_steps_completed"]
    console.print(f"All generated steps completed: {complete}")
    if output_published:
        console.print(f"Output artifact written to {output}")
    else:
        console.print("[yellow]Output artifact not published after failed execution.[/yellow]")
    console.print(f"Raw evidence written to {evidence}")
    console.print("[yellow]No manual PASS/FAIL was assigned.[/yellow]")
    if not complete:
        raise typer.Exit(code=1)


@app.command("compose-run-sandbox")
def compose_run_sandbox(
    bundle_directory: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    input_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    provenance_sha256: Annotated[str, typer.Option("--provenance-sha256")],
    image: Annotated[str | None, typer.Option("--image")] = None,
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/composition-sandbox-output.json"
    ),
    evidence: Annotated[Path, typer.Option("--evidence")] = Path(
        ".blackridge/evidence/composition-sandbox-probe.json"
    ),
) -> None:
    """Execute a generated calibration bundle through the networkless sandbox primitive."""

    try:
        input_artifact = json.loads(input_file.read_text(encoding="utf-8"))
        probe = run_generated_system_sandboxed(
            bundle_directory,
            input_artifact,
            expected_provenance_sha256=provenance_sha256,
            image_ref=image,
        )
        write_probe(probe, evidence)
        output_published = _publish_completed_artifact(probe, output)
    except (BlackridgeError, ValidationError, OSError, json.JSONDecodeError) as exc:
        failure = ProbeEvidence.failure(
            provider="blackridge-generated-sandbox-runtime/1",
            subject=str(bundle_directory),
            request={
                "bundle_directory": str(bundle_directory),
                "input_file": str(input_file),
                "image": image,
                "expected_provenance_sha256": provenance_sha256,
            },
            sources=[COMPOSITION_SOURCE, SWEREX_SOURCE],
            error=exc,
        )
        with suppress(OSError):
            write_probe(failure, evidence)
        console.print(f"[red]Sandboxed generated-system execution failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    complete = probe.observations["all_steps_completed"]
    sandbox = probe.observations["sandbox"]
    console.print(f"All generated steps completed: {complete}")
    console.print(f"Resolved image: {sandbox['image']['resolved_id']}")
    console.print(
        f"Container remaining after cleanup: {sandbox['cleanup']['container_exists_after']}"
    )
    if output_published:
        console.print(f"Output artifact written to {output}")
    else:
        console.print("[yellow]Output artifact not published after failed execution.[/yellow]")
    console.print(f"Raw evidence written to {evidence}")
    console.print(
        "[yellow]Calibration only; no production or release verdict was assigned.[/yellow]"
    )
    if not complete:
        raise typer.Exit(code=1)


@app.command("probe-composer")
def probe_composer(
    definition_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    input_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    bundle_directory: Annotated[Path, typer.Argument()],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/evidence/composer-probe.json"
    ),
) -> None:
    """Solve, generate, execute, and retain one complete composer experiment."""

    try:
        definition = load_composition_definition(definition_file)
        input_artifact = json.loads(input_file.read_text(encoding="utf-8"))
        probe = CompositionSystemProbe().probe(
            definition,
            definition_file=definition_file,
            output_directory=bundle_directory,
            input_artifact=input_artifact,
        )
        write_probe(probe, output)
    except (BlackridgeError, ValidationError, OSError, json.JSONDecodeError) as exc:
        failure = ProbeEvidence.failure(
            provider="blackridge-composition-system-probe/1",
            subject=str(definition_file),
            request={
                "definition_file": str(definition_file),
                "input_file": str(input_file),
                "bundle_directory": str(bundle_directory),
            },
            sources=[COMPOSITION_SOURCE],
            error=exc,
        )
        with suppress(OSError):
            write_probe(failure, output)
        console.print(f"[red]Composer probe failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    plan = probe.observations["plan"]
    runtime = probe.observations["runtime"]
    runtime_complete = runtime is not None and runtime["observations"]["all_steps_completed"]
    console.print(f"Compatibility plan complete: {plan['complete']}")
    console.print(f"Generated runtime complete: {runtime_complete}")
    console.print(f"Raw evidence written to {output}")
    console.print("[yellow]No manual PASS/FAIL was assigned.[/yellow]")
    if not plan["complete"] or not runtime_complete:
        raise typer.Exit(code=1)


@app.command("compliance-notices")
def compliance_notices(
    manifest_file: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, readable=True)
    ] = Path("compliance/distribution-manifest.yaml"),
    output: Annotated[Path, typer.Option("--output", "-o")] = Path("THIRD_PARTY_NOTICES.md"),
    check: Annotated[bool, typer.Option("--check")] = False,
) -> None:
    """Generate or verify notices from the active distribution manifest."""

    try:
        manifest = load_distribution_manifest(manifest_file)
        matches, _ = write_or_check_notices(manifest, output, check=check)
    except (BlackridgeError, ValidationError, OSError) as exc:
        console.print(f"[red]Notice generation failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    if check and not matches:
        console.print(f"[red]Generated notices differ from {output}[/red]")
        raise typer.Exit(code=1)
    action = "match" if check else "written to"
    console.print(f"Third-party notices {action} {output}")


@app.command("probe-wheel-release")
def probe_wheel_compliance(
    wheel: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    manifest_file: Annotated[
        Path, typer.Option("--manifest", exists=True, dir_okay=False, readable=True)
    ] = Path("compliance/distribution-manifest.yaml"),
    output_directory: Annotated[Path, typer.Option("--output-directory", "-o")] = Path(
        ".blackridge/release/wheel"
    ),
    syft_image: Annotated[
        str, typer.Option("--syft-image", show_default=False)
    ] = DEFAULT_SYFT_IMAGE,
) -> None:
    """Create wheel SBOMs, component inventory, license bundle, and raw evidence."""

    evidence_file = output_directory / "probe.json"
    try:
        manifest = load_distribution_manifest(manifest_file)
        probe = probe_wheel_release(
            wheel,
            manifest,
            output_dir=output_directory,
            syft_image=syft_image,
        )
        write_probe(probe, evidence_file)
    except (BlackridgeError, ValidationError, OSError) as exc:
        console.print(f"[red]Wheel release probe failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    blockers = probe.observations["release_blockers"]
    console.print(f"Wheel release blockers: {len(blockers)}")
    console.print(f"Evidence and artifacts written to {output_directory}")
    console.print("[yellow]The probe is not a legal approval or manual release verdict.[/yellow]")
    if blockers:
        raise typer.Exit(code=1)


@app.command("probe-image-release")
def probe_image_compliance(
    image_ref: Annotated[str, typer.Argument()],
    manifest_file: Annotated[
        Path, typer.Option("--manifest", exists=True, dir_okay=False, readable=True)
    ] = Path("compliance/distribution-manifest.yaml"),
    output_directory: Annotated[Path, typer.Option("--output-directory", "-o")] = Path(
        ".blackridge/release/image"
    ),
    license_review: Annotated[
        Path, typer.Option("--license-review", exists=True, dir_okay=False, readable=True)
    ] = Path("docker/python-license-review.yaml"),
    syft_image: Annotated[
        str, typer.Option("--syft-image", show_default=False)
    ] = DEFAULT_SYFT_IMAGE,
) -> None:
    """Inspect an exact image and block while distribution obligations remain unresolved."""

    evidence_file = output_directory / "probe.json"
    try:
        manifest = load_distribution_manifest(manifest_file)
        probe = probe_image_release(
            image_ref,
            manifest,
            output_dir=output_directory,
            syft_image=syft_image,
            license_review_file=license_review,
        )
        write_probe(probe, evidence_file)
    except (BlackridgeError, ValidationError, OSError) as exc:
        console.print(f"[red]Image release probe failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    blockers = probe.observations["release_blockers"]
    console.print(f"Image release blockers: {len(blockers)}")
    for blocker in blockers:
        console.print(f"- {blocker}")
    console.print(f"Evidence and artifacts written to {output_directory}")
    console.print("[yellow]The image remains blocked pending manual obligation review.[/yellow]")
    if blockers:
        raise typer.Exit(code=1)


@app.command("probe-source-provenance")
def probe_source_provenance(
    definition_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    repo_root: Annotated[Path, typer.Option("--repo-root")] = Path("."),
    work_root: Annotated[Path, typer.Option("--work-root")] = Path(
        ".blackridge/provenance/upstreams"
    ),
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/evidence/source-provenance-probe.json"
    ),
) -> None:
    """Compare tracked source history with exact, pinned upstream trees."""

    try:
        definition = load_source_audit_definition(definition_file)
        probe = audit_source_provenance(
            definition,
            repo_root=repo_root,
            work_root=work_root,
        )
        write_probe(probe, output)
    except (BlackridgeError, ValidationError, OSError) as exc:
        console.print(f"[red]Source provenance probe failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    observations = probe.observations
    console.print(f"Tracked source files: {observations['tracked_source_file_count']}")
    console.print(f"Exact fragment matches: {observations['exact_fragment_match_count']}")
    console.print(f"Raw evidence written to {output}")
    console.print(
        "[yellow]Review the result manually; zero matches is not proof of originality.[/yellow]"
    )


@app.command("check-provenance")
def check_provenance(
    manifest_file: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, readable=True)
    ] = Path("provenance/derived-code.yaml"),
    repo_root: Annotated[Path, typer.Option("--repo-root")] = Path("."),
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/evidence/provenance-gate-probe.json"
    ),
) -> None:
    """Fail closed when copied/adapted code lacks immutable provenance evidence."""

    try:
        manifest = load_provenance_manifest(manifest_file)
        probe = provenance_gate(manifest, repo_root=repo_root)
        write_probe(probe, output)
    except (BlackridgeError, ValidationError, OSError) as exc:
        console.print(f"[red]Provenance gate failed to run:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    issues = probe.observations["issues"]
    console.print(f"Provenance issues: {len(issues)}")
    console.print(f"Raw evidence written to {output}")
    if issues:
        raise typer.Exit(code=1)


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
    promotion_level: Annotated[int | None, typer.Option("--promotion-level")] = None,
    subject_type: Annotated[str | None, typer.Option("--subject-type")] = None,
    subject_revision: Annotated[str | None, typer.Option("--subject-revision")] = None,
    subject_license_spdx: Annotated[str | None, typer.Option("--subject-license-spdx")] = None,
    artifact_sha256: Annotated[str | None, typer.Option("--artifact-sha256")] = None,
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
        promotion_values = [
            promotion_level,
            subject_type,
            subject_revision,
            subject_license_spdx,
            artifact_sha256,
        ]
        if any(value is not None for value in promotion_values) and not all(
            value is not None for value in promotion_values
        ):
            raise BlackridgeError("all evidence promotion options must be supplied together")
        promotion = None
        if promotion_level is not None:
            if probe.observations.get("probe_completed") is not True:
                raise BlackridgeError("an incomplete probe cannot promote evidence")
            if (
                subject_type is None
                or subject_revision is None
                or subject_license_spdx is None
                or artifact_sha256 is None
            ):
                raise BlackridgeError("all evidence promotion options must be supplied together")
            if subject_type not in {"component", "adapter"}:
                raise BlackridgeError("subject type must be component or adapter")
            promotion_subject_type: Literal["component", "adapter"] = (
                "component" if subject_type == "component" else "adapter"
            )
            try:
                target_level = EvidenceLevel(promotion_level)
            except ValueError as exc:
                raise BlackridgeError("promotion level must be an integer from 0 to 4") from exc
            if target_level == EvidenceLevel.DISCOVERED:
                raise BlackridgeError("L0 discovery does not require a manual promotion")
            promotion = EvidencePromotion(
                target_level=target_level,
                subject_type=promotion_subject_type,
                probe_provider=probe.provider,
                probe_subject=probe.subject,
                probe_completed=True,
                subject_revision=subject_revision,
                subject_license_spdx=subject_license_spdx,
                artifact_sha256=artifact_sha256,
            )
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
            promotion=promotion,
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


@app.command("verify-holdout")
def verify_holdout(
    suite_root: Annotated[Path, typer.Argument(exists=True, file_okay=False, readable=True)],
    manifest_sha256: Annotated[str, typer.Option("--manifest-sha256")],
    system_revision: Annotated[str, typer.Option("--system-revision")],
    output: Annotated[Path, typer.Option("--output", "-o")] = Path(
        ".blackridge/evidence/sealed-holdout-verification.json"
    ),
) -> None:
    """Verify an externally authored holdout inventory without executing hidden cases."""

    try:
        probe = verify_sealed_holdout(
            suite_root,
            expected_manifest_sha256=manifest_sha256,
            expected_system_revision=system_revision,
        )
        write_probe(probe, output)
    except (BlackridgeError, ValidationError, OSError) as exc:
        failure = ProbeEvidence.failure(
            provider="blackridge-sealed-holdout-verifier/1",
            subject=str(suite_root),
            request={
                "suite_root": str(suite_root),
                "expected_manifest_sha256": manifest_sha256,
                "expected_system_revision": system_revision,
            },
            sources=[str(suite_root / "holdout-manifest.json")],
            error=exc,
        )
        with suppress(OSError):
            write_probe(failure, output)
        console.print(f"[red]Sealed holdout verification failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(f"Sealed holdout verified: {probe.subject}")
    console.print(f"Files verified: {probe.observations['file_count']}")
    console.print(f"Case files retained: {probe.observations['case_file_count']}")
    console.print(f"Raw evidence written to {output}")
    console.print("[yellow]No evaluator cases were interpreted or executed.[/yellow]")


if __name__ == "__main__":
    app()
