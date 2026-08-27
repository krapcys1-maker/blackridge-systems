# Scientific Claim Auditor workload

This workload is a real integration pressure test for the generic Blackridge foundry. It is not a
specialized feature in the control plane and it is not evidence that the current repository already
performs scientific fact checking.

The upstream archive is intentionally not committed. Obtain the exact bytes declared in
`upstream.json`, then prepare a working copy with:

```powershell
python tools/prepare_scifact_workload.py `
  --archive D:\path\to\data.tar.gz `
  --manifest benchmarks/scientific-claim-auditor-v1/upstream.json `
  --output D:\external\scifact-workload
```

The preparer refuses a different archive or member hash, reads only the three declared archive
members, and emits the mechanically selected calibration cases, the disjoint training claims, and a
complete corpus snapshot.
Generated files remain external evidence and are not wheel contents.

The selected inference component, its CPU-only sandbox recipe, rejected alternatives, measured
quality, manual case inspection, and remaining blockers are recorded in `manual-findings.md`.

Manual review order:

1. verify archive, member, and license hashes;
2. inspect every selected claim, evidence label, document identity, sentence index, and quote;
3. run the simplest baseline before changing composition behavior;
4. retain positive, contradictory, insufficient, malformed, duplicate, distractor, timeout, and
   tamper controls;
5. inspect the generated system artifact and sandbox boundary independently of regression tests.
