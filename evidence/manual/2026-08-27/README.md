# Manual verification — 2026-08-27

Environment: Windows, Python 3.12.10, Docker Desktop, SWE-ReX 1.4.0, and the exact
internal image recorded below. These verdicts cover one bounded production-sandbox
primitive; they do not approve a public image or claim hardened multi-tenant isolation.

## Segment verdicts

| Segment and scenario | Verdict | Manual observation |
| --- | --- | --- |
| production sandbox / `production-network-and-secret-boundary` | PASS for the primitive | The exact repository was prepared with network access. Blackridge detached `bridge`; every workload then ran as UID/GID 65534 through the in-container timeout, passed unittest and `41 -> 42`, denied both egress paths and sensitive names, preserved the host, and cleaned up. |
| paired unsafe control / `unisolated-boundary-control` | PASS for detection; unsafe arm FAILED as designed | The same non-root Docker-exec and timeout boundary retained inherited networking. Baseline behavior passed, both egress attempts became true, the hostile assertion exited 1, and the sentinel was not run. |
| production policy schema | PASS | Changing only the production request to `execution_network: inherit` was rejected before Docker with the named policy cause, and the container set did not change. |
| cleanup calibration | PASS after defect correction | Detaching the network made SWE-ReX stop unreachable and left the calibration container. The final adapter records exact Docker removal for isolated runs, verified exit 0 and no remaining container, while normal runs retain SWE-ReX cleanup. |
| generated system / `generated-system-sandbox-parity` | PASS for calibration; production disabled | Thirteen hostile preflight checks passed, two locked components ran non-root through inner deadlines, pre/post hashes matched 2/2, every contract passed, and the final report matched host calibration. |
| generated broken sink / `sandbox-green-invalid-output` | PASS for detection; broken pipeline FAILED as designed | The non-root sink exited zero, but exact missing `/report` and `/trace` schema paths kept the runtime incomplete. Preflight, post-hashes, and cleanup passed. |
| generated timeout / `sandbox-timeout-signal-control` | PASS for detection; hostile pipeline FAILED as designed | A component ignored TERM; verbose stderr retained TERM and KILL, exit was 137 after about two seconds, `timed_out=true`, the hash matched, and cleanup passed. |
| generated memory / `sandbox-memory-pressure-control` | PASS for detection; hostile pipeline FAILED as designed | With live memory max 1 GiB and swap max 0, a touched 1.2 GiB allocation exited 137 before its deadline and was not mislabeled as a timeout. |
| generated PID pressure / `sandbox-pids-pressure-control` | PASS | Under live `pids.max=256`, the 400-process attempt was refused at 252 with errno 11, cleaned every child, emitted a valid `blocked=true` artifact, and cleaned up. |

## Important findings

1. A Docker internal network cannot be applied at SWE-ReX startup because it also severs the
   upstream runtime's host control channel.
2. Preparing through SWE-ReX, detaching every network, and executing workload argv through
   `docker exec` preserves control without preserving workload egress.
3. Network isolation must change cleanup ownership. Calling SWE-ReX stop after detachment is not
   reliable; exact Docker removal plus a second existence check is part of the boundary.
4. The final A/B uses the same image, repository, preparation, workload argv, non-root UID, Docker
   executor, and timeout wrapper. Equal baseline behavior isolates the observed network difference.
5. Absence of sensitive environment names is observed, not inferred. Secret values were never
   read or forwarded during the experiment.
6. Host-only `docker exec` timeout left the exact workload alive. The inner GNU timeout delivered
   TERM and escalated to KILL; both generic and generated workloads now use that boundary.
7. A generated-system component needs no host mount: exact files can be copied, rehashed inside
   the container, and mapped from their declared argv to deterministic container paths.
8. Trusted JSON Schema and adapter orchestration can remain outside the untrusted component
   process. Both sandboxed components received and returned only JSON through stdin/stdout.
9. The experiment found host evidence mutation in the composition runner: JsonPatch changed the
   retained empty `add` value. Separate deep copies now keep definition/evidence immutable, and a
   real rerun produced the same report.
10. Production mode and host environment forwarding are rejected before Docker. The calibration
    backend passes only fixed `HOME`, `TMPDIR`, and `PYTHONIOENCODING` values.
11. Installing the first wheel in a fresh venv exposed a stale-interpreter-path defect hidden by
    the editable checkout. The final wheel maps only a recognized Python argv zero, rejects other
    absolute paths, and completed the real sandbox workload from `site-packages`.
12. UID 0 with zero capabilities still changed the component and `/etc`. UID/GID 65534 denied both
    while allowing `/tmp`; post-execution hashes make a later mutation observable.
13. Docker's default memory setting retained 1 GiB of swap and allowed 1.2 GiB. Setting memory-swap
    equal to memory produced live swap max 0 and killed the same allocation.
14. Memory, PID, timeout, and signal controls pass their frozen attacks, but the root filesystem is
    not read-only and no measured disk quota exists. Production mode therefore remains disabled.

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
- `composer-sandbox-timeout-probe.json` and its review — ignored TERM, KILL escalation, timeout
  classification, post-execution hash, and cleanup.
- `composer-sandbox-memory-probe.json` and its review — exact live memory/swap cgroup, fast exit
  137 distinct from timeout, closed pipeline, hash, and cleanup.
- `composer-sandbox-pids-probe.json` and its review — live PID cgroup, exact refusal count/errno,
  structured contract result, hash, and cleanup.
- `sandbox-hostile-controls-calibration.json` — raw before/after Docker attacks that selected the
  non-root, inner-timeout, and no-swap controls, including discarded harness limitations.

SHA-256 checksums of the canonical bytes retained in Git:

```text
composer-sandbox-broken-output-probe.json            5645e0c4a33509b18fb46b8b9075d34b2dac0abbdfa0a21ad44a600fd453e2c0
composer-sandbox-broken-output-review.json           ac0ef6c10c14abc3dd505f9f95227760d9cf542aecc33cf8f00984f6f239b283
composer-sandbox-memory-probe.json                   643e5b3f9aef8e42872678dfaae99b91711b99b124c26b0770945f77274920bb
composer-sandbox-memory-review.json                  583be38042cab19e131b4ec2263134ad20b11c37b0947a55d71c4e14d23e8366
composer-sandbox-pids-probe.json                     1ddf0da61fedde3e4bc4ce72f080fb05378a1abfad56de5edefdf2542182bda3
composer-sandbox-pids-review.json                    c2255834925224ca9f8f90f03006b34c01eecc0fc5f0f9fd66e52049132fa4fc
composer-sandbox-positive-probe.json                 3ac816cd819a0033bad6e31b33064a9fdc2f92ccfffc6d30d5293ac27f69ee85
composer-sandbox-positive-review.json                f0e9f17fdf8604d319f8c927b887273a108375858835ba3fed786b6a3497334b
composer-sandbox-timeout-probe.json                  8d7b9f8d7447eb3b6b893aae36831c5232208a2458e57847718454dbad207d2f
composer-sandbox-timeout-review.json                 5b84d88f388d04767a24641a4902f1ae5d98d35abea71fbffb1004cb38d5a3ca
production-sandbox-manual-run.txt                    70fd92547f3d82cdd84e7e91e96e3cac78839b55a5172e7c344c61b7448ed3b9
production-sandbox-positive-probe.json               bea46e45bf45fe3a5cf872e2ad3fe5572d4a4eed463131e2ebaaae888f131b40
production-sandbox-positive-review.json              906e9ece3bddf1bc854692536ac02f6a51ca951e8b07a2ddd64659440418eca8
production-sandbox-unisolated-control-probe.json     6e152b73831dec7906c7a20e0bb2896c2d27a337e24735e7388ee3f347fb3e04
production-sandbox-unisolated-control-review.json    93617d5317b90d0f9563684d938d0ed3a30c23117b1daf7332d8d0420bb208fb
sandbox-hostile-controls-calibration.json            93fcaaecff1dca724c92d48eaa86676a3faad25853833e38b8d7fbe5810f599e
```

## Cross-boundary audit and remediation rerun

The later audit reran the generated system against the rebuilt runtime image
`sha256:38de6ac1db8d82217f6d3d7268bde4a742afcee431e6dc17b66b5fb279b6d904`.
It retained an unchanged positive host run, an unchanged sandbox run, and four distinct
negative controls. It also added an external provenance root: the original bundle completed,
but a wrong root and a modified bundle with every internal hash recomputed were both rejected
before component execution.

The exact old image environment contained seven vulnerability entries for `pip 25.0.1`.
The rebuilt image contains `pip 26.2.1`; pip-audit reported zero findings and OSV-Scanner
reported zero vulnerable entries across 160 package entries. The release probe completed with
all runtime locks and license reviews intact. Its public gate remains deliberately closed for
qualified reciprocal-license review and corresponding-source obligations; this is a policy
result, not a failed internal build.

`audit-remediation-probe.json` is the compact index over the raw artifacts and
`audit-remediation-review.json` is the hash-bound manual verdict. Docker Scout was not counted
as evidence because its local CLI required an authenticated Docker session; the exact image was
scanned independently with pip-audit and the pinned OSV-Scanner image.

The final wheel `blackridge_systems-0.1.0-py3-none-any.whl` has SHA-256
`9c3039d15c2a5bcd760369f4fd8f60c59a2844da87850cd69f3cf7f35cfc88eb`.
Twine accepted it; an independent archive inspection found 30 files, the three required
license/notice files, and no tests or environment files. A non-editable installation outside
the repository imported version 0.1.0 from `site-packages`, built the real CLI, passed
`pip check`, and had no known dependency vulnerabilities.

The final source-provenance probe covered all 23 tracked production files, reported no
untracked source and no exact fragments matching the pinned upstream trees. As the raw probe
states, zero exact matches supports the provenance review but does not prove originality.

```text
audit-host-positive-probe.json                             3b18a000af169f41b6bce18f43bc3197db7c177c24db1875446fb858cdfd9ed8
audit-host-relocked-attack-probe.json                      60d3da5f9dd04ff4837c752f4b1205f616dd6037c61ac5fcc8fc103e354b607c
audit-host-wrong-trust-root-probe.json                     e5a2e4be4e7f265569a0014f25c4ad80c63179f23b9244b6599d8a8f2903ede0
audit-image-osv-fixed.json                                 d15a0d9c92cc59f586105b7581a34a254eb7a6732ef6640c29b3067f1904d3f4
audit-image-release-fixed-probe.json                       5a425a78e8e229ede90d3b2ec53f57e44047c105838054e5fddd0823b5d21058
audit-remediation-probe.json                               9a6d984a8dbab87897d0b809cd9bb7899d3c6aa73004d657910d5fee950fd135
audit-remediation-review.json                              717a2e428ec30827eba9c982efca747d46b7760eb6f2d9ed9de63a5838c9c511
audit-runtime-pip-after.json                               bc85e105be2b3780ef0e6d8fd4b19eea620ecdc7414a161dbcf85f5b50749d71
audit-runtime-pip-before.json                              e680ec0378a28fc3ec78c83c8e00a8829b6f29666272cfa984ac8154676625af
audit-sandbox-broken-output-fixed-image-probe.json         ec8a27d2632ab3c6e7943259e713bcbd9e5c6c38e24942ba2c105078c8a6ff0f
audit-sandbox-memory-fixed-image-probe.json                40cd15094cf72ff970e1b86571f980234f9004f7bdd9927d8f138ecc7d04fe20
audit-sandbox-pids-fixed-image-probe.json                  fad1596f4493a817398d28c832562c8490544b43e0134d9aba5c3d290cd2fd82
audit-sandbox-positive-fixed-image-probe.json              50973c988f551ba04b1ac002b84613e729cf6f0c5c0d56c0b02b1284674b7654
audit-sandbox-timeout-fixed-image-probe.json               a866db19bdfb36d7eeb6646b947a3667dbd050ef77fae4e246224a0e12635a10
audit-source-provenance-final.json                         6836c37c3914a5c0b7e787e255f36026b4d7f255af017f4efa8b305278bc7891
```
