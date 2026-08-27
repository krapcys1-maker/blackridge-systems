# Manual findings — 2026-08-28

This record separates observed behavior from automated regression status. Raw reports are retained
outside the repository because the extracted model is 1.75 GB and the reports contain the complete
SciFact corpus-relative traces.

## Frozen inputs

- SciFact commit: `68b98a56d93e0f9da0d2aab4e6c3294699a0f72e`
- SciFact archive SHA-256: `11c621288d41ac144d29b13b0f8503b3820b7d6e8b1f6ff24dff335c196d76be`
- MultiVerS commit: `a6ce033f0e17ae38c1f102eae1ee4ca213fbbe2e`
- official SciFact checkpoint SHA-256: `630739ec906bc5ad959a59bcee479329f97aeee4eb373230c79595b076c46690`
- extracted safetensors SHA-256: `42ee31687442c7c32c354ee18cafd5320e09b4656ad44f0b697cc58fcd2c8446`
- Longformer revision: `b190dd42e462d2dee634d1162c839710079f97ab`
- raw tokenizer SHA-256: `028621155ac209730bb147a4eef4dcc243320149480d9c677fb1394e2d7f24fa`

The legacy PyTorch checkpoint is hash-checked before trusted pickle loading and immediately reduced
to inference-only safetensors. The runtime does not load the legacy pickle.

## Model and pipeline observations

The twelve mechanically selected calibration cases produced 12/12 statuses and 12/12 exact
document-label pairs. Every evidence case was manually compared with the frozen annotations; its
rationale indexes were exact. The retained report SHA-256 is
`5176dde28f08debb964df25acb4747230b2235103934a5578bfcc6496a3ec133`.

On the remaining 288 development claims, 256 statuses (88.9%) and 241 exact document-label sets
(83.7%) matched. The retained report SHA-256 is
`e02d5a0fe410cbcb5de47f6f6daf4e4c8386d8e39df8141f74b4766396654c16`.
Across all 300 claims, the exact official evaluator reported:

- abstract label F1 `0.8385417` (precision `0.92`, recall `0.770335`);
- abstract rationalized F1 `0.8385417`;
- sentence label and sentence-selection F1 `0.8391813` (precision `0.902516`, recall `0.784153`).

The official-metric report SHA-256 is
`93e9f9befe9e8fda63279762896aaf949fe118f5c52888a81f1f12ab4f9e34c5`.
An earlier lightweight ONNX NLI candidate reached only 159/288 holdout statuses and official
abstract label F1 `0.404494`; it was rejected and is not a selectable production component.

Manual error decomposition found 29 evidence-bearing false negatives and three false-positive NEI
claims. Every one of the 29 false negatives was a top-three retrieval miss. When a gold document
was present in the top three, the MultiVerS label produced no status error in this development set.
This is diagnostic evidence, not a promise on other corpora.

The current BM25 retriever measured recall@1 `0.694444`, recall@3 `0.827778`, recall@5 `0.85`,
recall@10 `0.927778`, and MRR@10 `0.77125` over 180 evidence-bearing development claims. Its report
SHA-256 is `006ac4e2543dad437a5adc63291e6329c54f43e00a8870bff339f189a4114607`.
Adding titles to the indexed text was tested and rejected because recall@3 fell to `0.811111`.

## Generated-system observations

Host runs were inspected for one support (`dev-3`), one contradiction (`dev-42`), and one
insufficient-evidence claim (`dev-1`). The evidence documents and rationale indexes were manually
compared with the frozen records: `dev-3` returned document `14717500`, sentences 2/5/7; `dev-42`
returned document `18174210`, sentences 1/9/10; `dev-1` returned no evidence documents.

The generated bundle also returned the exact `dev-42` result while both original component files,
the original corpus, model, config, and tokenizer were temporarily unavailable. Raw evidence
SHA-256: `037665b10c7fddc7cd73e6e51f5a32ff98c35d9174924d84dba35241073e226e`.

A request containing an undeclared field skipped both components and ran no process. Evidence
SHA-256: `af0958004e26150b7f4f7626e7e4af518d4b53277fe6defa266dbd379aebf332`.
Changing one bit in the bundled model changed its SHA-256 and stopped execution at the provenance
gate. Evidence SHA-256: `388cf1eacc72c5b935519529f791e542c46a7ef6ffbd62dda3999f4f7a935b73`.

The final sandbox dependency image is locked as
`sha256:f27a5402336f4cd0c8aa6d5170093da46e0d681556ca5892d7527da9c6ecdb7d`.
A caller-supplied alternative was rejected before container creation. A full sandbox run selected
the locked image without an external override and returned the exact `dev-42` contradiction. All
thirteen hostile-control checks passed, copied artifacts matched before and after execution, and
the container was confirmed absent after cleanup. Evidence SHA-256:
`f9d624969ed4d19a44cd2f6bfc8a67aa5371729c22d4fdf73cecdec00e660f00`.

## What this does not prove

The foundry is not release-ready. Retrieval still misses relevant abstracts, no second independent
domain workload has yet exercised the same universal composition path, and the component image
still needs a release SBOM, license bundle, vulnerability review, and reproducible dependency-lock
workflow. No live scholarly-search integration was accepted as evidence. External API spend for
this workload was `$0.00` of the authorized `$10.00` ceiling.
