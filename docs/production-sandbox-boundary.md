# Production sandbox boundary v1

Blackridge now has a tested two-phase repository experiment for production-boundary
calibration. It reuses SWE-ReX for environment preparation and Docker for the security
boundary; it does not replace either project with a new container runtime.

## Boundary sequence

1. Resolve the declared local image to its immutable SHA-256 ID.
2. Start a disposable SWE-ReX container with all capabilities dropped,
   `no-new-privileges`, 1 GiB memory, 256 processes, and two CPUs.
3. Fetch only the requested Git commit and run explicitly declared preparation commands.
   Preparation is the only phase with the container's normal network attached.
4. Inspect every attached Docker network, detach each one, and inspect again. Any remaining
   network or failed detach keeps the workload from running.
5. Execute workload commands through argv-only `docker exec --workdir`; no shell and no host
   environment variable is forwarded.
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

In both arms the repository unittest and independent `add_one(41) == 42` artifact pass. In the
production arm, direct `1.1.1.1:443` and DNS-based `pypi.org:443` connections both fail, no
sensitive host environment name is visible, and the sentinel runs. In the deliberately unisolated
arm, both connections succeed, the identical hostile assertion exits 1, and the sentinel is not
run. Both containers are removed and both host snapshots remain unchanged.

## Generated-system calibration backend

`blackridge compose-run-sandbox` applies the same primitive to an already generated calibration
bundle. Before Docker starts, it verifies the complete generated provenance map, runtime mode,
component launch hashes, and the absence of requested environment forwarding. It copies each
locked component artifact into the disposable container with `docker cp`, verifies the SHA-256
again inside the container, removes every network, and runs an egress/secret-name preflight.

Component argv then execute through `docker exec -i` with JSON on stdin. JSON Schema validation and
reviewed RFC 6902 adapters remain in the trusted Blackridge orchestrator. The calibration backend
maps only a recognized Python interpreter in argv position zero and the locked artifact path; any
other absolute argv path fails closed. It forwards only a fixed `PYTHONIOENCODING=utf-8`, never a
host environment value.

The retained positive experiment produced the same final artifact as the prior host calibration.
The paired broken sink still exited zero, but schema inspection retained missing `/report/title`
and `/trace/source`, kept the system incomplete, and cleaned the sandbox. Production runtime mode
and nonempty environment allowlists are rejected before container creation.

## What this does not prove

This is a production-boundary primitive with a calibration composer backend, not an enabled
production composer and not a hardened multi-tenant sandbox claim. Repository preparation can
still have network access, the container runs as root inside its namespace, the filesystem is
writable and ephemeral, and the Docker daemon is a trusted host boundary. The next step is to test
filesystem, resource-exhaustion, timeout, and signal controls before allowing any production
runtime mode.
