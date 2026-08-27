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

## MLXP Deployment, 2026-08-28

This is a startup verification snapshot, not a claim that the full Lite run
or its final global merge has finished.

- Source code: `bd83c796ee654ea8bc584ca7361c5f8d3adde786`.
- New code: `012c6b2feae10879305e59759370a62adfc9cb8c`, pushed to GitHub and
  checked out cleanly at `/mnt/nvme/gpic-scaleout/repos/lexical-handoff-012c6b2`.
- Pod: `prod-rsv-snu14ksh-20260827-78e36c`, two H200 GPUs, 480 GiB cgroup limit.
- Interpreter: `/mnt/ddn/prod-runs/snu14ksh/gpic-linux-env/bin/python`.
- Linux acceptance: the same six test modules, **106 passed in 40.13s**.
- Same-input 100-caption old/new smoke: Stage 5 canonical row multisets match
  exactly (1,912 mentions; 1,192 edges); all ten Stage 6 TSVs are byte-identical.
  Model `en_core_web_trf`, batch 192, eight sentence/tag shards per GPU,
  disabled NER, two GPUs and `canonical_counts` retention were unchanged.
  The new smoke is `/mnt/nvme/gpic-scaleout/lexical-smoke100-handoff-012c6b2`;
  its verification is the new checkout's
  `reports/handoff_smoke_verification.json`.

The user approved stopping the legacy job and recomputing incomplete units.
The exact legacy supervisor/child process tree was verified and terminated.
All **16 completed units / 8,000,000 captions** retained their original receipt
hashes; every retained artifact passed SHA verification. Incomplete units 16
and 17 were not imported. They are recomputed in the new root alongside the
remaining units 18 and 19: **1,975,391 captions** remain to finish.

| Purpose | Location |
|---|---|
| Read-only source units | `/mnt/nvme/gpic-scaleout/lexical-lite10m-followup-bd83c79` |
| New output | `/mnt/nvme/gpic-scaleout/lexical-lite10m-memory-012c6b2` |
| Control/evidence directory | `/mnt/ddn/prod-runs/snu14ksh/gpic-scaleout/lite-official-v1/handoff-012c6b2-20260828` |

The control directory contains `legacy_stop.json`, `handoff.json`, `launch.json`,
`job.json`, `stdout.log`, `stderr.log` and `live_verification.json`.
The frozen handoff SHA-256 is
`507bea797024a03a195f7834177ef5ef228e0fb4b0ae3703afab0445b0956fe4`.
The intentional SIGTERM incident `30abfb6b350344598296e1ee85d18928` was resolved
only after the stop receipt, tests, smoke equality and source hash checks;
its history remains recorded. It was not a cooperative legacy pause.

The replacement was launched with `run_background_job.py`, with the actual
detached child wrapped by `incident_gate.py`. Guard PID 139465 and child PID
139466 were live at **2026-08-28 03:19 KST**. It does not depend on the desktop
staying open. No elapsed-time timeout is attached to healthy formal work.
The recorded launch command preserves the old semantic arguments and adds
the new output root and `--reuse-verified-units` plan. T5 was not restarted.

At that snapshot, `progress.json` reported 8,000,000 completed rows, with units
16 and 17 active on GPUs 0 and 1. Stage 3 shard progress reported actual
`gpu_enabled=true`, `gpu_mode=require` and increasing annotation totals.
The hardware-derived process-tree budget is divided across both unit workers
(179.73 GiB each), then across each unit's eight concurrent Stage 3 workers
(about 22.39 GiB each). Stage 4/5/6 and merge children use the same budget
propagation; the final production merge had not yet run at this snapshot.
Its adaptive SQLite-spill regression is documented in
`verification_count_merge_memory_20260828.md`.

Cgroup memory was 268.81 GiB, including 254.21 GiB of file cache and 13.86 GiB
of anonymous memory. GPU memory was 56,996 and 50,026 MiB of 143,771 MiB per
GPU. Cgroup `max`, `oom` and `oom_kill` counters were all zero; stderr was empty.
These are measurements, not a promise that all future allocations fit.

For later planned pauses, use the new revision's
`run_fixed_lexicon_scaleout.py pause --output-root NEW_OUTPUT`, wait for
`paused`, then resume with the recorded launch arguments plus `--resume`.
Only runtime GPU selection may change. Keep the handoff plan and both data
roots; do not edit the active code checkout or delete the reused source root.
