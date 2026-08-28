# Completed Fixed-Lexicon Releases

The execution run may reuse completed units from an older run after a verified
code handoff. That is not a self-contained delivery directory: its COMPLETE
record can point at more than one source root. Never delete those roots just
because a newer run is complete.

`scripts/publish_fixed_lexicon_release.py` creates an independent durable
release after execution finishes. It performs no inference or count merge.

```
release/
  manifest.json                 # explicit relative paths, coverage and SHA256
  COMPLETE.json                 # sealed manifest, rows, unit count, byte count
  stage5/unit_000000/shard_0000/stage5/
    canonical_mentions.jsonl
    canonical_edges.jsonl
  counts/                       # final global Stage 6 files, unchanged
  provenance/                   # original receipts, manifests, unit counts
  publish_progress.json         # latest copy/verification progress
```

The manifest's `stage5_shards` is the explicit mention/edge file list;
`stage5_roots` lists unit roots in the usual `shard_*/stage5` layout. Resolve
both relative to the release directory, not the working directory. Historical
absolute paths remain in provenance and input identity, never in the release's
Stage 5 read paths. Moving the release must not require an old run directory.

## Publication

Use a clean, verified Git checkout. Determine the mounted personal PVC's
actual quota (not the shared filesystem total), measured personal usage, and
a reserve appropriate to concurrent writes. Pass all values explicitly:

```sh
python scripts/publish_fixed_lexicon_release.py \
  --source /path/to/completed/run \
  --destination /personal/releases/lexical-lite10m-v1 \
  --capacity-root /personal \
  --capacity-bytes "$PERSONAL_QUOTA_BYTES" \
  --used-bytes "$MEASURED_PERSONAL_USAGE_BYTES" \
  --reserve-bytes "$RESERVE_BYTES"
```

The publisher validates run identities, handoff receipt hashes, disjoint input
shards, retained-file coverage and every Stage 5 mention/edge pair. It streams
each source against its recorded SHA256, fsyncs the copy, rereads the copy to
verify its SHA256, and only then completes that file. A final source-metadata
check precedes directory promotion. There is no healthy-work wall-clock kill.

An OS-held lock excludes concurrent publishers to the same destination.
Interrupted work stays in `<destination>.partial`; the same source plan can
resume by verifying already copied files. A changed plan blocks resume rather
than mixing runs. Progress is overwritten and flushed every five seconds.
Re-measure usage before resuming. Quota admission uses that measurement, not a
live quota API; the reserve must accommodate other writers. Filesystem free
space is also checked before each copy.

Independent verification needs only the release:

```sh
python scripts/publish_fixed_lexicon_release.py \
  --destination /personal/releases/lexical-lite10m-v1 --verify-only
```

Publication never modifies live controller configs, deletes a source, or
changes producer identities. Cleanup is separate: inspect live commands and
pinned configs/manifests, retain required audit/reference inputs, and record
the exact resolved paths, evidence and bytes removed. Never recurse over
another researcher's directories or delete generic NVMe cache/repo trees.

## Verification

Local regression on 2026-08-28: 46 tests passed (publisher, verified-unit
handoff, atomic I/O), 29.11 seconds. Fixtures verify exact bytes, both producer
revisions and readability after both original roots are made unavailable.
Real release completion and cleanup evidence are recorded separately; passing
fixtures is not evidence that a large transfer has finished.
