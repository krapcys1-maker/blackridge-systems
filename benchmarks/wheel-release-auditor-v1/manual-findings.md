# Wheel Release Auditor v1 — manual findings

Reviewed locally on 2026-08-28 (evidence timestamps are UTC). This is a calibration result, not an
L4 review, legal approval, vulnerability verdict, or production-release decision. External spend:
USD 0.

Wheel `RECORD` behavior was checked against the current PyPA binary distribution format:
<https://packaging.python.org/en/latest/specifications/binary-distribution-format/>.

## Frozen subjects

- Calibration wheel: `blackridge_systems-0.1.0-py3-none-any.whl`, 117083 bytes,
  SHA-256 `5836e84abb7ebd9131a7673022959d907f8ded78283288dc1deba335a5ce72af`.
- Inspector component SHA-256:
  `1bb8276c0c75f332247612a4c7b92d98f5a00619cf06bbdfbd8c42e263517094`.
- Policy component SHA-256:
  `fe4d688af5a21a79c486d187cfdca41a81e1a7721fc5939abe3bbe9555e0cba6`.
- Final plan SHA-256:
  `0ee8bba0b51b8a33e734684c5a8b1f5fddb0a10de797c8c2e1a4a4c9bd89ad26`.
- Final generated-bundle provenance SHA-256:
  `1a8dbdf8a79c023702328548672d996bd3e1a1f1bb78d277e6e51cd6ba32a7d0`.
- Locked sandbox image ID:
  `sha256:a03f1852c1c437df005ee33b01a26d5e55714c670d3e2273e007c56fd16a5903`.

## Segment-by-segment observations

1. **Inventory on the real wheel.** The inspector read 34 archive members without extraction,
   found one `METADATA`, one `RECORD`, no duplicate names, and no unsafe paths. It streamed and
   verified every required non-`RECORD` entry against its secure hash and size. The validator also
   follows the standard's deprecated `RECORD.jws`/`RECORD.p7s` exception and accepts algorithms at
   least as strong as SHA-256. `record_valid` was true with no errors. The embedded LICENSE, NOTICE,
   and THIRD_PARTY_NOTICES hashes exactly matched the three repository files.
2. **Positive policy.** Both generated steps completed; both public output contracts validated.
   All nine declared checks passed. The result deliberately remained `release_ready: false` with
   two explicit blockers. Output SHA-256:
   `1296b6fe03eaf72c742b00c03f48deca1153bd8c6fb7353128dcac70f6413416`;
   evidence SHA-256:
   `55eb49225f5b830fe39e2159267cd3fdbf6621d0d4eef75ea013abd711b0d6b1`.
3. **Forbidden real dependency.** The same wheel with `jsonschema` forbidden completed normally
   and returned `policy-failed`; the sole failed check was `forbidden-dependencies`. Output
   SHA-256 `44ddcd9b3575dff31f9a8cb2147c0785f1f5f6a48ac895a6a0c0b8fbc0c22d8e`;
   evidence SHA-256
   `13cb28af1785a6a3fecc1aed2751ca88dfe248f928d1e9d9837287018da925ac`.
4. **Malformed external input.** An undeclared field caused contract rejection before execution;
   both steps were `skipped`, no output was published, and the CLI returned 1. Evidence SHA-256:
   `b35d55b1217a712d4bc2d220fa4fe5ae8009dfc7f5c44fd0328ae5331654c4c5`.
5. **Source absence.** The original inspector, policy component, and wheel were moved out of view in
   one guarded PowerShell `try/finally`. The generated bundle still produced the exact positive
   output, then all three originals were restored. Evidence SHA-256:
   `92dbd66f1271cabfd8dff8a9b3a7232b2f89c5b124aef80fa4ed4827e11eda87`.
6. **Bundle tampering.** Appending one byte changed the bundled wheel hash to
   `dfdbbff21306318136ca3c1cd6490d23aa61e6409a9f00ea3219e3b34cc5702f`.
   Execution stopped before any output, returned 2, and named the exact resource path. Failure
   evidence SHA-256:
   `b38f42dc89540c0b6dec472cd14b1ec9c6a6237f0fd0b81b5dcdfa14893742e0`.
7. **Corrupt internal RECORD.** A separately hash-locked wheel contained the real artifact plus
   `blackridge/unrecorded_probe.py`, absent from `RECORD`. Both generated steps and both contracts
   completed, but the sole failed check was `record-integrity` with that exact path. Corrupt wheel
   SHA-256 `1e93d27d6c69c0d599c69822b5ac2c582d58d0a6b48a482ad41782498c7b4430`;
   output SHA-256 `d86fa70795d804119d55ea487544fc7c0e177c6df7c0705885de67f61acaf2a9`;
   corrupt-bundle provenance SHA-256
   `fa29ebf69618a076d38af06d6c2292a5ddf1c78f5b63b55494552f72be09cecc`;
   evidence SHA-256
   `956e3a15a203f6e2b7a85b58ccf16b1677250c4e0b4cb99c7656b8ab343df4c1`.
8. **Networkless sandbox.** Host and sandbox outputs were byte-identical. All 13 hostile-control
   checks passed: non-root UID/GID, zero effective capabilities, no-new-privileges, component and
   `/etc` write denial, writable scratch, direct and DNS egress denial, no sensitive environment
   names, exact 512 MiB memory limit, zero swap, 128 PID limit, and exact 2-CPU cgroup quota. All
   three copied artifacts matched before execution and after execution. No networks remained
   attached and the container did not exist after cleanup. Output SHA-256:
   `1296b6fe03eaf72c742b00c03f48deca1153bd8c6fb7353128dcac70f6413416`;
   evidence SHA-256:
   `57cda23f34a21b9f5c94af50785e07dee4a4853b163c70cfa5cf38c478ed9229`.

## Generic foundry defect exposed and fixed

The first sandbox replay was logically successful but changed the resource basename from
`wheel.whl` to `resource-1-1.whl`, so host and sandbox contract artifacts differed. The generic
generator now preserves the original resource basename inside a component/resource namespace, and
the sandbox preserves that generated basename in its own component namespace. The final replay is
byte-identical across host and sandbox. Runtime module SHA-256 at final replay:
`efbe389740145bb5593cbecddb0fc3f420e98d385399e68483f563d80de67eec`.

## Limits retained deliberately

- A technical wheel policy pass does not establish legal permission or absence of vulnerabilities.
- Dependency markers and extras are inventoried but not resolved into a platform-specific lock.
- The container image and dependency set still need separate SBOM, license, vulnerability, and
  reproducibility evidence before any production-release claim.
