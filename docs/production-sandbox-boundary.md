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

## What this does not prove

This is a production-boundary primitive, not yet the production composer backend and not a
hardened multi-tenant sandbox claim. Preparation code still has network access, the container runs
as root inside its namespace, the filesystem is writable and ephemeral, and the Docker daemon is a
trusted host boundary. The next integration step is to route generated production component
commands through this primitive, add explicit preparation-source policy, and test filesystem,
resource-exhaustion, timeout, and signal controls before enabling production execution.
