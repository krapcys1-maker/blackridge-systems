# Sealed holdout boundary

A blinded holdout must be authored outside the component-repair loop and stored outside the
Blackridge repository. Blackridge verifies the complete byte inventory before any evaluator or
experimental arm is allowed to run; verification does not read the semantic meaning of cases or
claim that the suite is independent.

The external owner creates `holdout-manifest.json` at the suite root:

```json
{
  "schema_version": "1",
  "suite_id": "scientific-researcher-independent-holdout",
  "version": "1.0.0",
  "sealed_at": "2026-08-27T18:00:00Z",
  "owner": "Independent evaluator name",
  "system_revision": "40-lowercase-hex-commit",
  "files": [
    {
      "path": "definition.json",
      "role": "definition",
      "sha256": "64-lowercase-hex-digest",
      "size_bytes": 123
    },
    {
      "path": "evaluator.py",
      "role": "evaluator",
      "sha256": "64-lowercase-hex-digest",
      "size_bytes": 456
    },
    {
      "path": "cases/opaque-case.bin",
      "role": "case",
      "sha256": "64-lowercase-hex-digest",
      "size_bytes": 789
    }
  ]
}
```

Every suite requires at least one `definition`, `evaluator`, and `case` role. Paths must be
normalized relative POSIX paths. The inventory is exact: missing and additional files, byte or
size changes, symlinks, a different system revision, and a different manifest hash all fail
closed.

The owner communicates the manifest SHA-256 through a separate channel. Verify it with:

```console
blackridge verify-holdout /external/sealed-suite \
  --manifest-sha256 <sha256-from-owner> \
  --system-revision <exact-blackridge-commit> \
  --output .blackridge/evidence/sealed-holdout-verification.json
```

The resulting probe records inventory counts and hashes, not case contents. Keep its output
outside the sealed suite so the suite remains an exact closed inventory.

Verification is only the entry gate. The independent owner must also preregister the scoring
policy, negative controls, resource limits, unsealing time, permitted number of runs, and the rule
that exposed cases are never reused for repair. A named manual review remains required before any
L4 claim.
