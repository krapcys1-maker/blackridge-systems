# Evolution control records

This directory is the durable handoff surface for the champion–challenger loop.

- `state.json` tells an agent what the current champion is and what must happen next.
- `HANDOFF.md` records human-readable decisions, evidence locations, commands, and limitations.
- `benchmark/public-spec.json` freezes critical gates and multidimensional scoring.
- `rounds/<round>/manifest.json` records immutable candidates and progress for one generation.

An agent must read `state.json`, `HANDOFF.md`, and the active round manifest before changing a
candidate. It must not rename the current `v1.1` champion to `v2`; only the fresh challenger branch
in round 001 receives that architecture name.
