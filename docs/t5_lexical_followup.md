# Unattended T5 To Fixed-Lexicon Handoff

`scripts/run_t5_lexical_followup.py` connects a completed, locked T5 population
to the existing fixed-lexicon Stage 1-6 runner. It changes orchestration only;
no extraction rule, inventory row, canonicalizer, or count definition changes.

1. `prepare` creates a new immutable config with the exact T5 identity, input
   manifest, source commit, current inventory/sidecar/lexicon hashes, commands,
   and fresh output directories. It does not load a GPU model.
2. `run --config ...` may run in a user-authorized pod tmux session. The child
   itself is incident-gated. One repository incident/running lock owns the
   entire chain; lexical children inherit that gate token.
3. Waiting requires a live T5 tmux session and fresh T5 progress. Interrupted,
   failed, missing, or stale progress blocks the followup without killing T5.
   Five minutes is a heartbeat staleness check, not an inference runtime limit.
4. After both T5 COMPLETE and launcher success, stream every input/output on
   the pod and verify receipt coverage, run identity, file SHA/size, row counts,
   caption-ID order, zero adaptive splits, and the ordered grouping rollup.
   No caption text or IDs are printed or sent through the desktop.
5. Run the existing 100-caption lexical smoke, compare all Stage 5 canonical
   mentions/edges and Stage 6 TSVs against the locked baseline, and verify
   receipt-based resume. Only then run the complete identical input manifest.
   Stage 5 equality is the row multiset (row count and canonical content SHA),
   independent of shard/file counts or file/row ordering. Physical file counts
   remain in both sides of the audit; receipt file/size/SHA checks remain strict.
6. Keep the inventory frozen. Use SQLite counting and `canonical_counts`
   retention: verified Stage 5 records and count tables remain; regenerable
   intermediate files are pruned by the existing retention implementation.
   A worker's assigned external GPU selector must reach the Stage 3 child
   unchanged. Verify actual CUDA process placement, not only scheduler labels.

All child stdout/stderr goes to per-step log files. Phase history is retained in
`events.jsonl`. `status.json` is atomically
updated every 30 seconds with the supervisor PID, child PID/progress path,
phase, and config hash. There is no total elapsed-time kill. A failure blocks
the chain and creates an incident; it is not automatically retried or cleared.
The same config can resume after a reviewed incident using completed unit
receipts. A new pod must restore the runtime and the appropriate T5 session
locator if T5 itself has not completed yet. Never silently mutate a config.

Data remains on MLXP NVMe. Publishing large artifacts to DDN is a separate
capacity-checked operation; this handoff does not touch shared training data,
start Full/100M, update inventory, or create HTML.

## Planned Pause And GPU Changes On Restart

`--resume` always verifies completed artifact SHA256, even without the optional
hash-verification flag. Batch size and per-GPU Stage 3 shard grouping are pinned
alongside the input unit boundaries and inventory; GPU selectors remain runtime
settings. A missing or changed receipt is never reused as a completed unit.

Request a pause with `run_t5_lexical_followup.py pause --config <config.json>`.
This returns after recording the request, not after GPU work has stopped.
Wait for `status.json` to say `paused` and the supervisor to exit before
releasing the pod. Active lexical units finish Stage 1-6, verification, and
retention first; no partial unit is accepted. The current formal unit size is
10 input shards (normally 500,000 captions), so draining is not instantaneous.
During the final global merge the job finishes instead of killing the merger.

Restart with the same config and output paths:
`run_t5_lexical_followup.py run --config <config.json> --resume --gpus 0,1,2,3`.
`--gpus auto` discovers the new attempt's visible GPUs. The override changes
only child GPU selection; it is recorded in runtime status/events, not written
back into the immutable config. GPU count may change from 1 through 8 between
attempts. Batch size, input work units, code revision, and inventory stay fixed.
The existing T5 verification, locked 100-caption equality check, and receipt
resume checks still run before the formal child resumes completed units.

Standalone scale-out uses `run_fixed_lexicon_scaleout.py pause --output-root
<output>` and the original run arguments plus `--resume --gpus <new-list>`.
Both controllers use attempt-bound atomic request files. A planned pause is a
normal exit and does not clear or create an incident; real errors and forced
termination retain the incident policy. There is no automatic error retry.

These controls require the pause-capable code from the start of a run. Do not
hot-update a running checkout or rewrite an old run's identity to adopt them.

## Real GPU Restart Smoke

`scripts/verify_fixed_lexicon_gpu_restart.py` runs the real Stage 1-6 producer,
not mocked workers. Supply an existing small immutable caption manifest,
inventory bundle, preposition lexicon, a fresh output root, and two GPU indices.
It preserves all input row fields and order while creating four equal work
units. It runs 1 GPU, drains one unit, resumes on 2 GPUs, drains two more units,
then resumes on 1 GPU for the last unit. An uninterrupted 2-GPU run is the
baseline. Caption count defaults to 100 and the smoke rejects inputs over 1000.

Each attempt records actual descendant process GPU UUIDs using `ps` and
`nvidia-smi`. Pause is requested only after CUDA processes appear on every
requested GPU. Completed receipt and retained artifact hashes must stay
unchanged on resume. Final verification compares Stage 5 canonical row
multisets and every global Stage 6 TSV, plus population and immutable identity.

The smoke uses batch 192, two Stage 3 shards per GPU, two Stage 4-6 shards per
unit with one job, SQLite, and canonical-count retention in every attempt.
It tests restart placement and equality, not production throughput or physical
hot-plugging. Running concurrently with production can affect throughput;
check memory headroom first and use a separate clean checkout and output root.
The explicit 900-second per-attempt deadline is for this bounded diagnostic
only, never a production runtime limit. Failure stops its own process session,
records evidence, and remains incident-gated without retrying production.
