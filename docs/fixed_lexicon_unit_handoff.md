# Verified Completed-Unit Handoff

## Why This Is Not Ordinary Resume

Completed 500K units are reusable. A code change must not relabel the old
producer as the new producer, however. Ordinary `--resume` deliberately keeps
the same immutable identity. Use an explicit new run and a verified handoff
when adopting an execution-only fix across revisions.

The old `bd83c796` live runner supports completed-unit receipts, but not
cooperative planned pause. New pause controls cannot be installed into that
already-running process. Stopping it can discard incomplete units; obtain
approval for that cost, inspect the exact process tree, and verify termination
before reusing its completed output. Do not use SIGSTOP or edit its checkout.

## Procedure

1. Commit/push the replacement code and verify a clean, exact-SHA MLXP checkout.
2. Run the same small locked caption smoke with that revision. Preserve the
   old input order, batch size, Stage 3 split boundaries and inventory paths.
3. Compare Stage 5 canonical row multisets and every global Stage 6 TSV byte.
   A smoke match is empirical evidence for that sample, not a universal proof
   of numerical equivalence on different hardware or every future caption.
4. Once the old worker is stopped or complete, prepare the handoff plan:

   ```sh
   python scripts/fixed_lexicon_handoff.py \
     --source-root OLD_OUTPUT --source-revision OLD_SHA \
     --baseline-smoke OLD_SMOKE --candidate-smoke NEW_SMOKE \
     --output HANDOFF_JSON
   ```

5. Launch `run_fixed_lexicon_scaleout.py` in a new output root with the same
   formal semantic arguments and `--reuse-verified-units HANDOFF_JSON`.
   Runtime GPU selection may change. Subsequent pauses/resumes of this new
   run use the normal planned-pause protocol and explicit `--resume` flag.

The plan freezes the original manifest, completed receipt hashes, source and
target revisions, smoke manifests, and input/grouping identity. Legacy batch
fields absent from the run manifest must be proven by each receipt-hashed unit
summary. Incomplete units cannot be imported. All retained artifact hashes are
checked during preparation, startup and before final merge. Verification emits
per-unit progress; these reads must not be mistaken for inference or a hang.

## Output Ownership

Old manifests, receipts, Stage 5 and unit Stage 6 files remain read-only in
their original root. Only new units and final merged counts live in the new
root. `summary.json` lists the Stage 5 roots and each unit's original code
revision. Consumers must use those explicit roots, not discover only the new
root's `units` directory. Keep both roots while the combined artifact exists.
No large caption data travels through the desktop.

Changed input/inventory/grouping, damaged source files, duplicate/local receipt
overlap, nested roots, or a failed/missing smoke proof block the handoff.
Current handoff supports `canonical_counts` and one source revision transition;
chained handoffs require a separate provenance implementation. This limitation
does not affect ordinary pause/resume or runtime GPU-count changes.

## Validation

Windows bounded regression on 2026-08-28: `test_fixed_lexicon_handoff.py` and
`test_run_fixed_lexicon_scaleout.py`, **47 passed in 73.15s**, 240s ceiling.
Fixtures exercise source immutability, legacy evidence, changed identities,
hash/coverage failures, duplicate prevention, mixed old/new final merge, source
lineage and unchanged ordinary-resume rejection. MLXP smoke and deployment
receipts are run-specific generated evidence, not implied by these fixtures.

Related merge-memory/followup/retention/pause regressions: **59 passed in
25.47s**, also with a 240s ceiling. Total local acceptance: 106 tests.
