# Wheel Release Auditor v1

This independent workload pressures the generic Blackridge foundry with a real binary release
artifact rather than scientific text. It composes a wheel inventory component with a policy
component through explicit `wheel-audit-request/v1`, `wheel-inventory/v1`, and `wheel-audit/v1`
contracts.

The calibration artifact is the locally built Blackridge wheel identified in `artifact.json`.
Generated bundles copy the exact wheel bytes as a locked component resource. The workload must be
rerun and the manifest updated whenever those bytes change.

Manual review covers member-path safety, duplicate archive names, exact METADATA identity,
streamed verification of every wheel `RECORD` entry, dependency-name normalization, embedded
license/notice hashes, an intentionally forbidden real dependency, malformed input, resource
tampering, source absence, and the networkless sandbox. Generated resources preserve their source
basename so host and sandbox results do not change merely because the execution boundary changed.

A `policy-passed` result remains `release_ready: false`. This workload is a technical artifact
policy auditor, not legal approval, vulnerability analysis, or complete release certification.
