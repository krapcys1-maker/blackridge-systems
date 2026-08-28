# Semantic Scholar 0.12.0 hermetic upstream suite

This benchmark runs the upstream `unittest` suite from commit
`3189ecb80bd985b6cd9b4a56fb410b05515f0f15` with Python 3.12.

The environment is resolved by `pip-compile` in `upstream-tests.lock` and must
be installed with all of the following pip flags:

```text
--no-index --no-cache-dir --require-hashes --only-binary=:all:
```

`hermetic-upstream-tests.patch` changes only the test harness. It fixes the
invalid list-valued VCR record mode, switches VCR to replay-only mode, and
replaces the two live-network timeout tests with deterministic
`httpx.AsyncClient.request` mocks. Production library sources are not patched.

The verified run used a read-only source mount, `--network none`, all Linux
capabilities dropped, `no-new-privileges`, one CPU, 512 MiB memory, and 128
PIDs. The authoritative result is recorded in
`evidence/manual/2026-08-27/semanticscholar-upstream-hermetic-suite.json`.
