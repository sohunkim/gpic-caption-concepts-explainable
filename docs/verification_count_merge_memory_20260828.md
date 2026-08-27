# Bounded Count Merge And Shared RAM Budget

## Scope

User approved fixing the final merge memory regression on 2026-08-28. This is
an R28 execution/storage change, not a linguistic rule or inventory change.
Stage 1-6 caption grouping, model, batch size, GPU placement and count semantics
must remain unchanged. Expected count-table delta is zero.

## Root Cause

The July Stage 6 streaming/SQLite fix remained in place. The later parallel
shard merger accumulated all distinct keys in `rows_by_key`, then allocated a
second sorting index. Its hash partition count followed job count, not RAM.
The scale-out final merge reused this path. Individual Stage 4/5/6 workers also
derived their RSS limits from the whole pod independently.

The live 10M run was healthy when inspected: 6,000,000 of 9,975,391 captions,
12/20 completed units, active units 12 and 13, cgroup `memory.max=480 GiB`,
`memory.current=179269230592 bytes`, and `oom=oom_kill=0`. This is not a measured
final-merge peak or proof that all future inputs fit.

## Correction

- One RSS budget is divided through the process tree after subtracting each
  dispatcher's measured RSS. No dataset-specific GiB or row-count constants.
- Cgroup limits take priority; non-container hosts can use physical RAM and
  Windows working-set measurement. Unknown budget/RSS fails parallel planning.
- Shared accumulator spills at 80% of its assigned RSS guard, leaving headroom
  for serialization/sort. SQLite temporary sort is FILE, mmap is disabled.
- Every table follows the same path, including hash partitions and final merge.
- Original pipe strings are preserved across spills; repeated literal pipes
  do not change normalization. Example IDs retain the same sorted top five.
- Stage 6's existing SQLite cache also checks repeated keys, because evidence
  can grow without introducing a new key, and flushes before the hard guard.
- Atomic TSV output and failure cleanup preserve previously validated files.
  Per-table progress records the assigned budget, spill count and backend.

## Verification Plan

Force repeated spills for every COUNT_TABLE_SPEC and compare exact output
bytes with the memory path. Include overlapping keys across flushes, value
conflicts, literal pipes, tab-escaped keys, stable sort, top-five examples,
parallel/hash paths, unknown measurements and parent budget exhaustion.
Run Stage 3/4/5/6, mixed runner and pause/restart regression suites, then repeat
bounded tests on Linux from an exact Git commit in an isolated checkout.

## Local Verification

- Initial bounded regression: 136 passed in 98.34 seconds (240-second ceiling).
- Final local regression: 158 passed, one existing Torch deprecation warning,
  in 107.84 seconds (240-second ceiling). Modules: count_merge_memory,
  stage456_sharded, formal_stage_memory_safety, stage6_export_counts,
  stage3_sharded, stage3_annotate, mixed_caption_pipeline,
  run_fixed_lexicon_scaleout, run_t5_lexical_followup,
  verify_fixed_lexicon_retention_smoke, verify_fixed_lexicon_gpu_restart,
  planned_pause. `git diff --check` also passed.
- Actual RSS probe: 100,000 distinct keys each seen twice, with a diagnostic
  budget equal to process starting RSS plus 32 MiB. This small explicit budget
  is a test condition, not a production cap or dataset-dependent rule.
- Windows result: 200,000 input rows, 100,000 output keys, 13 SQLite spills,
  21,383 maximum cached keys, 2.734 seconds. Starting RSS 28,660 KiB, ending
  RSS 47,268 KiB; the sampled hard guard was not exceeded. Windows peak RSS
  was not captured by this probe, so these are not a peak-memory claim.
- Sorted result SHA256:
  `1cb287d69cf8d0aa281202e624cb513bd498ef481acec86bcc4934ce7ca29a99`.
- Diagnostic report: `reports/count_merge_memory_local_20260828.json`.
- A later read-only live check found 7,000,000/9,975,391 completed rows,
  `memory.current=196433092608`, `memory.max=515396075520`, and all OOM counters
  zero. That run still uses the old pinned code, not this correction.

## Deployment Boundary

The running 10M checkout is pinned at
`bd83c796ee654ea8bc584ca7361c5f8d3adde786`. New code must not be hot-written into
it, nor may its source identity be relabeled. Completion receipts and healthy
in-flight units are preserved. Deployment to that attempt requires a separately
verified handoff; merely pushing these changes does not update a live process.

## Limits

This bounds aggregate-key growth by spill and allocates process budgets; it is
not a promise of zero OOM. Periodic RSS checks cannot intercept every native
allocation. A single enormous caption/metadata row and GPU VRAM allocations
remain separate risks. No GPU batch size or sentence grouping is auto-changed.
