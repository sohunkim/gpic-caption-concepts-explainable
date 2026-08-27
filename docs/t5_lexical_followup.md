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
