# Scientific researcher metadata-handshake review — 2026-08-27

## Verdict

The strict metadata handshake removed the verified component source and public task from the
Blackridge builder prompt without changing the installed runtime artifact. In benchmark v8,
all three Blackridge runs passed 22/22 critical checks, reported the measured 319 reused lines,
generated zero source lines, and installed the exact registered SHA-256.

This optimization reduces model work; it does not expand the component's L3 semantic scope.

## Frozen payload measurements

| Prompt | Characters | Source present | Public task present |
|---|---:|---:|---:|
| v6 source-bearing hybrid | 21,057 | yes | yes |
| v7 metadata plus public task | 9,906 | no | yes |
| v8 strict metadata handshake | 4,107 | no | no |

The v8 prompt retains the component ID, physical line count, source SHA-256, exact public
contract SHA-256 values, two named reviews, and independently verified primary and supplemental
probe bindings. The source bytes are installed only after the response by the deterministic
orchestrator and independently measured from the resulting workspace.

The orchestrator now rejects a Blackridge response that does not explicitly confirm the
preselected component. A regression test also proves that any supplied `candidate.py` cannot
replace the exact registered bytes.

## Negative optimization result: v7

Removing source bytes alone was insufficient. The v7 prompt still contained the public task and
implementation instructions. In all three attempts the model ignored the reuse instruction,
generated a new `candidate.py`, selected no component, and claimed zero reused lines. The
orchestrator safely discarded that code and installed the registered artifact, so runtime quality
still reached 22/22, but the builder protocol and efficiency goal failed.

V7 Blackridge used 16,322 total API tokens and averaged 15.986 seconds of builder wall time.
This retained negative result directly motivated the strict handshake and the new fail-closed
selection check; it is not reported as successful optimization evidence.

V7 artifact root:
`D:\Skladacz aplikacji\blackridge-experiments\scientific-researcher-v1-replication-20260827-v7-metadata-only`

- Repository commit: `412128dbf0f7d3ce3628432a130da278fd9c8c61`.
- Manifest SHA-256: `296864216ed9eb103390069bbe94d0d2a36e096df7bd17d8538f00b8f86478f0`.

## Successful benchmark v8 (3 × 2)

All paired comparison controls and immutable-input checks matched. Every process exited without
timeout or output overflow. Clean-install probes passed and no benchmark container remained.

| Arm | Task success | Critical checks | Mean builder wall | Mean generated lines | Mean reused lines | Total API tokens |
|---|---:|---:|---:|---:|---:|---:|
| from-scratch | 0/3 | 48/66 | 15.378 s | 208.3 | 0 | 12,563 |
| Blackridge handshake | 3/3 | 66/66 | 2.356 s | 0 | 319 | 4,827 |

Compared with v6 Blackridge, the handshake reduced total tokens from 18,076 to 4,827 (73.3%)
and mean builder wall time from 5.127 seconds to 2.356 seconds (54.1%). Compared within v8, it
used 61.6% fewer tokens and 84.7% less builder wall time than from-scratch.

The model returned the exact 309-token metadata response in all three Blackridge attempts. Each
bundle contained only `requirements.lock` and `BUILD.json`, selected
`grounded-researcher-v1`, and claimed 319 reused lines. Independent measurement matched that
claim. The installed source was byte-identical in every attempt:
`c8b34ceaed8980bcb70a4a63c7afe713e9f4cdecec655f1a314d448086dfe56d`.

The functional stdout SHA-256 was
`fac57767aa6adfe30fdd8434d4a60e1478a5ba6d4fe7643d8f6c5c6b336bcb2e` in all three attempts;
the clean-abstention stdout SHA-256 was
`c0f2c1f80dd091e57e7a921c1dee545d844e1b6d4815a7436cf082733b0bbc4e` in all three attempts.
Manual review confirmed all ten source identities, exact citation substrings, required concepts,
and empty negative claims and sources.

V8 artifact root:
`D:\Skladacz aplikacji\blackridge-experiments\scientific-researcher-v1-replication-20260827-v8-metadata-handshake`

- Repository commit: `19749ea99a806e0e68004decb499b953fb34ad7e`.
- Manifest SHA-256: `6c3af289050a26d2bbf00342a04ba69b8c153aac9128c307a20f2f9c6a2cf1f3`.
- Attempt 1 comparison SHA-256:
  `3e24cea4257b5aff2da9d68ef78bfa575ec9af6c19cd2310ee418fb1fcd8b5cd`.
- Attempt 2 comparison SHA-256:
  `0f606225fa95c2b167b7461da4db65c4350767af4687c4dc6db8eaa4b7331e33`.
- Attempt 3 comparison SHA-256:
  `5487c2bce63e1f315114e4827171e93ea3a1a6f265eb08b72d904e3040bc25a6`.

## Remaining limit and next experiment

V8 is still remediation and replication against a known evaluator, not a blinded holdout. The
next falsifiable step is a sealed suite owned outside the component-repair loop. It should use
unseen domains and vocabulary, contradictory and temporally qualified sources, Unicode and
boundary-sized requests, adversarial community structures, and resource pressure. The suite
must be versioned before either arm runs and must not be reused for repair after unsealing.

