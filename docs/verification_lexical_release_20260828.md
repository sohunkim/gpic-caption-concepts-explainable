# Lexical Lite10M Release Verification

Verified on 2026-08-28. Internal locations, process identifiers and storage
telemetry are kept in the private operational audit, not this Git summary.

## Result

- Independent release with 9,975,391 input captions, 20 disjoint work units
  and 140 explicit Stage 5 mention/edge pairs.
- All 905 retained artifacts matched both source receipts and reread copied
  bytes. Source metadata remained unchanged before atomic promotion.
- An additional independent verification read only the release and confirmed
  all artifact hashes and coverage. No parsing or count recomputation occurred.
- Caption IDs, mention IDs, source/target IDs, raw text, canonical values and
  rule provenance remain in Stage 5. Original producer identities remain per
  unit, including the units imported by verified handoff.
- Use the manifest's explicit `stage5_shards`, resolved against the release
  root. Final tables are in `counts/`; execution evidence is in `provenance/`.

## Retirement

The explicitly user-retired, misnamed Lite-prefix 1M experiment was removed
only after checking active metadata, commands and open files. Original JSON
metadata and all file hashes were retained. A second audit checked unchanged
target bytes and references immediately before removal. The official Nano,
current execution roots, references and dependencies were preserved.

## Tests

- Local publisher/handoff/atomic I/O suite: 46 passed in 29.11s.
- Linux publisher/atomic I/O suite: 24 passed in 0.35s.
- Final cleanup guard + publisher suite: local 27 passed in 5.33s;
  Linux 27 passed in 0.38s.
- A pre-deletion fixture caught raw JSON text comparison missing escaped
  paths. The implementation now traverses parsed strings and normalizes
  separators, covered by native, POSIX and nested-reference regressions.
  No real removal was attempted before this correction.

The running extraction was not reconfigured. Publication is not a substitute
for runtime capacity/eviction safeguards or backups of other output roots.
