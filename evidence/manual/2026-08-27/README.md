# Manual verification — 2026-08-27

Environment: Windows, Python 3.12.10, Docker Desktop, SWE-ReX 1.4.0, and the exact
internal image recorded below. These verdicts cover one bounded production-sandbox
primitive; they do not approve a public image or claim hardened multi-tenant isolation.

## Segment verdicts

| Segment and scenario | Verdict | Manual observation |
| --- | --- | --- |
| production sandbox / `production-network-and-secret-boundary` | PASS for the primitive | The exact repository was prepared with network access. Blackridge then detached `bridge`, observed no remaining network, passed the real unittest and `41 -> 42` behavior, denied direct and DNS egress, exposed no sensitive host environment name, preserved the host, and removed the container. |
| paired unsafe control / `unisolated-boundary-control` | PASS for detection; unsafe arm FAILED as designed | Identical image, commit, preparation argv, and workload argv retained inherited networking. Baseline behavior still passed, both egress attempts became true, the exact hostile assertion exited 1, and the sentinel was not run. |
| production policy schema | PASS | Changing only the production request to `execution_network: inherit` was rejected before Docker with the named policy cause, and the container set did not change. |
| cleanup calibration | PASS after defect correction | Detaching the network made SWE-ReX stop unreachable and left the calibration container. The final adapter records exact Docker removal for isolated runs, verified exit 0 and no remaining container, while normal runs retain SWE-ReX cleanup. |
| generated system / `generated-system-sandbox-parity` | PASS for calibration; production disabled | Two locked components were copied without a host mount, rehashed 2/2 in the container, and executed after network removal. All contracts passed, adapter evidence stayed immutable, and the final report exactly matched the host calibration. |
| generated broken sink / `sandbox-green-invalid-output` | PASS for detection; broken pipeline FAILED as designed | The sandboxed sink exited zero, but exact missing `/report` and `/trace` schema paths kept the runtime incomplete. Preflight and cleanup still passed. |

## Important findings

1. A Docker internal network cannot be applied at SWE-ReX startup because it also severs the
   upstream runtime's host control channel.
2. Preparing through SWE-ReX, detaching every network, and executing workload argv through
   `docker exec` preserves control without preserving workload egress.
3. Network isolation must change cleanup ownership. Calling SWE-ReX stop after detachment is not
   reliable; exact Docker removal plus a second existence check is part of the boundary.
4. The A/B control changed only profile/network metadata. Equal baseline behavior prevents the
   egress result from being explained by a different repository, image, install, or workload.
5. Absence of sensitive environment names is observed, not inferred. Secret values were never
   read or forwarded during the experiment.
6. The primitive is wired only into the generated-system calibration runner. Writable rootfs,
   root-in-namespace, timeout/signal behavior, and resource exhaustion remain explicit next
   controls; production mode stays disabled.
7. A generated-system component needs no host mount: exact files can be copied, rehashed inside
   the container, and mapped from their declared argv to deterministic container paths.
8. Trusted JSON Schema and adapter orchestration can remain outside the untrusted component
   process. Both sandboxed components received and returned only JSON through stdin/stdout.
9. The experiment found host evidence mutation in the composition runner: JsonPatch changed the
   retained empty `add` value. Separate deep copies now keep definition/evidence immutable, and a
   real rerun produced the same report.
10. Production mode and host environment forwarding are rejected before Docker. The calibration
    backend forwards only a constant `PYTHONIOENCODING`, not a value taken from the host.
11. Installing the first wheel in a fresh venv exposed a stale-interpreter-path defect hidden by
    the editable checkout. The final wheel maps only a recognized Python argv zero, rejects other
    absolute paths, and completed the real sandbox workload from `site-packages`.

## Retained artifacts

- `production-sandbox-positive-probe.json` — raw two-phase preparation, network-detach,
  shell-free workload, behavior, hostile checks, host snapshot, and cleanup evidence.
- `production-sandbox-positive-review.json` — named manual verdict for the positive scenario.
- `production-sandbox-unisolated-control-probe.json` — identical workload with inherited network,
  retained egress assertion, skipped sentinel, host snapshot, and cleanup.
- `production-sandbox-unisolated-control-review.json` — named verdict that passes detection, not
  the unsafe execution.
- `production-sandbox-manual-run.txt` — both discarded calibrations, exact cleanup, paired-input
  comparison, generated-system experiments, adapter mutation correction, policy rejection,
  limitations, and final verdict.
- `composer-sandbox-positive-probe.json` and its review — copied hashes, boundary preflight,
  component processes, every contract, immutable adapter evidence, host parity, and cleanup.
- `composer-sandbox-broken-output-probe.json` and its review — paired green-exit invalid sink,
  exact schema failures, closed runtime result, and cleanup.

SHA-256 checksums of the canonical bytes retained in Git:

```text
production-sandbox-manual-run.txt                    6b90a6d7752021c2961b6eb97ba68f7e94c33c266ba96051fff7847e1f8f637f
production-sandbox-positive-probe.json               6dec94737f6cd24989a534143c20ac681366e1c7dd0575431d46b1a93d41585d
production-sandbox-positive-review.json              d5f33a6d0e4989fea7c75e6dc9c39b0f3ef50a0a85f05a9972459bb4589db73a
production-sandbox-unisolated-control-probe.json     0aecb1f7c8a0fa1f274e28b30445ad635ddce1872adc8c267eab1fce2e9b0403
production-sandbox-unisolated-control-review.json    3fe7c509871ea933cc41fb4bd990af6c05fefa76e4cc5eacc51027d1e8cede48
composer-sandbox-broken-output-probe.json            bd6ebacd7a5ff66539f924ef84143be1c097d613008522d962b5e8451971b9bc
composer-sandbox-broken-output-review.json           81a4550a9858f8bf491866c5b732385bc22849e6e09d55f8732d2b9cf810f183
composer-sandbox-positive-probe.json                 0e77e94ce1123085d8c8d6498e3743d8653ad9139a64cc51126b2fd8f0d96d99
composer-sandbox-positive-review.json                a3186f5b164fcf342f711bf601dffbc77dccee16150ba761f0fed6ff998f5428
```
