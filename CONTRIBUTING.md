# Contributing

Blackridge is reuse-first. Before adding a new implementation, search for an established,
license-compatible upstream component and record the decision in `upstream-catalog.yaml`.

A contribution should include:

1. a capability or integration contract;
2. provenance for any upstream code or artifact;
3. deterministic tests;
4. a security and license note;
5. evidence that the change improves an end-to-end outcome.

Run the local quality gate with:

```bash
ruff check .
pytest
```

Never execute code from an untrusted candidate repository on the host. Discovery and source
inspection are read-only; execution belongs in a disposable sandbox with bounded credentials,
network, CPU, memory, and time.

