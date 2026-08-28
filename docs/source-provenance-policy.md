# Source provenance and copy policy

Blackridge prefers package, CLI, API, SDK, and OCI boundaries. Reusing an idea or a public API is
not recorded as copying source. Copying or adapting implementation text is a separate action and
must be declared before it enters a release branch.

## Required record for copied or adapted code

Add a record to `provenance/derived-code.yaml` with all of the following:

- the upstream repository and immutable 40-character commit;
- exact upstream paths and SHA-256 hashes of the reviewed snapshot;
- exact destination paths and SHA-256 hashes;
- SPDX license identifier plus the retained license text and its SHA-256;
- an explicit compatibility decision and named reviewer;
- an attribution location and concrete description of modifications;
- a named manual `pass` review whose file hash still matches.

`blackridge check-provenance` fails closed when any declared record is incomplete, changed, outside
the repository, unapproved, or covered by a license requiring separate legal review. It also blocks
an attribution marker such as `Derived from:` when no record names that destination.

The registry cannot detect a copied file whose author deliberately removes every marker. Therefore
it is paired with `blackridge probe-source-provenance`, which examines Git first-add history and
searches for exact normalized six-line fragments across frozen upstream commits. The retained
probe explicitly states its limits: it will not detect renamed, reordered, translated, or heavily
edited copies, and a zero-match result is not proof of originality.

The 2026-08-26 baseline compares every then-tracked `src/blackridge/*.py` file with the exact
commits behind python-json-patch 1.33, jsonschema 4.26.0, SWE-ReX 1.4.0, and the npm `gitHead` for
Octocode 18.3.0. Any match is a review target, never an automatic infringement verdict.
