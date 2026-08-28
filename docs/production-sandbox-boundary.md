# Production sandbox boundary v1

Blackridge now has a tested two-phase repository experiment for production-boundary
calibration. It reuses SWE-ReX for environment preparation and Docker for the security
boundary; it does not replace either project with a new container runtime.

## Boundary sequence

1. Resolve the declared local image to its immutable SHA-256 ID.
2. Start a disposable SWE-ReX container with all capabilities dropped,
   `no-new-privileges`, 1 GiB memory with swap disabled, 256 processes, and two CPUs.
3. Fetch only the requested Git commit and run explicitly declared preparation commands.
   Preparation is the only phase with the container's normal network attached.
4. Inspect every attached Docker network, detach each one, and inspect again. Any remaining
   network or failed detach keeps the workload from running.
5. Execute workload commands as UID/GID 65534 through argv-only `docker exec --workdir`; no shell
   and no host environment value is forwarded. GNU `timeout` inside the container sends TERM at
   the declared deadline and escalates to KILL one second later.
6. Remove the exact container through Docker after the control network has been severed,
   then verify that it no longer exists and that the host source snapshot is unchanged.

This split is necessary because placing SWE-ReX itself on a Docker internal network also blocks
its host-to-container control channel. A real calibration attempt waited without becoming ready.
Detaching the network after preparation preserved execution through `docker exec`, but also proved
that SWE-ReX's normal stop path can no longer reach its server. The final adapter therefore records
an exact `docker rm --force` cleanup and verifies the result.

## Paired real-world experiment

Both fixtures use the same image, `pypa/sampleproject` commit
`621e4974ca25ce531773def586ba3ed8e736b3fc`, preparation argv, and four workload argv lists:

- `examples/sandbox-pypa-sampleproject-production.yaml` detaches every network before workload;
- `examples/sandbox-pypa-sampleproject-unisolated-control.yaml` deliberately inherits networking.

In both arms the repository unittest and independent `add_one(41) == 42` artifact pass through the
same non-root Docker-exec and in-container-timeout boundary. In the production arm, direct
`1.1.1.1:443` and DNS-based `pypi.org:443` connections both fail, no sensitive host environment
name is visible, and the sentinel runs. In the deliberately unisolated arm, both connections
succeed, the identical hostile assertion exits 1, and the sentinel is not run. Both containers are
removed and both host snapshots remain unchanged.

## Generated-system calibration backend

`blackridge compose-run-sandbox` applies the same primitive to an already generated calibration
bundle. It first compares `provenance.json` with the independently retained SHA-256 supplied via
`--provenance-sha256`. Before Docker starts, it then verifies the complete generated map, runtime mode,
component launch hashes, and the absence of requested environment forwarding. It copies each
locked component artifact into the disposable container with `docker cp`, verifies the SHA-256
again inside the container, removes every network, and runs a thirteen-check hostile preflight.

Component argv then execute through `docker exec -i` with JSON on stdin. JSON Schema validation and
reviewed RFC 6902 adapters remain in the trusted Blackridge orchestrator. The calibration backend
maps only a recognized Python interpreter in argv position zero and the locked artifact path; any
other absolute argv path fails closed. Each component runs as UID/GID 65534 with fixed `HOME`,
`TMPDIR`, and `PYTHONIOENCODING` values rather than values read from the host. Component hashes are
verified again after execution.

The retained positive experiment produced the same final artifact as the prior host calibration.
The paired broken sink still exited zero, but schema inspection retained missing `/report/title`
and `/trace/source`, kept the system incomplete, and cleaned the sandbox. Production runtime mode
and nonempty environment allowlists are rejected before container creation.

## Hostile-control calibration

The implementation followed paired attacks rather than configuration inspection alone:

- killing only the host `docker exec` client left the exact Python workload alive in `/proc` and
  delivered no TERM; GNU `timeout` inside the container produced the recorded TERM marker,
  escalated to KILL, returned 137, and left no exact workload process;
- UID 0 with every capability dropped still changed the copied component and wrote under `/etc`;
  UID/GID 65534 with zero effective capabilities was denied both writes while `/tmp` remained a
  writable scratch location;
- Docker's default `--memory=1g` exposed another 1 GiB of swap and a touched 1.2 GiB allocation
  succeeded; adding `--memory-swap=1g` made live `memory.swap.max` equal zero and the same allocation
  exit 137 before its ten-second deadline;
- a component attempting 400 child processes was refused with `errno 11` after 252 children under
  live `pids.max=256`, then terminated and waited for every child it had created.

The final positive composition still matches the host-calibration artifact. Separate generated
timeout, memory, PID, and green-exit-invalid probes retain the exact process, contract, cgroup,
post-execution hash, and cleanup observations.

## What this does not prove

This is a production-boundary primitive with a calibration composer backend, not an enabled
production composer and not a hardened multi-tenant sandbox claim. Repository preparation can
still have network access and executes as root before the workload boundary. The root filesystem
is not mounted read-only, `/tmp` is intentionally writable, there is not yet a measured per-run
disk quota, and the Docker daemon is a trusted host boundary. The bounded controls above pass, but
production mode stays disabled until a read-only-root/scratch design and disk-exhaustion control
also pass and the broader threat model is reviewed.
