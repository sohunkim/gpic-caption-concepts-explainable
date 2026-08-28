# Lite To Full Continuation

The user approved an unattended continuation on 2026-08-28. Full means the
official 100M tier, not a Lite prefix. Existing Lite work must finish and pass
verification before Full inference starts. This does not authorize writes to
shared training data, inventory expansion, adaptive T5 batching, or bulk data
transfer through the desktop.

## Runtime Preflight Incident

A read-only metadata probe incorrectly assumed that a shared virtualenv's
interpreter was executable in a different container image. It was a broken
symlink there. This occurred before Full inference or output creation. The
corrected diagnostic checks interpreter existence and imports inside each
target container, without changing its environment. Storage copy operations
use a verified standard-library interpreter. Parquet work and inference use
the separately verified compute runtime. Formal continuation preflight must
reject missing interpreters/imports, never silently replace the inference
environment. Detailed deployment locators and incident evidence are kept in
local operational records, not in this portable usage document.

## Execution

`copy_verified_files.py` makes a resumable, checksum-verified mirror without
modifying the shared source. `export_registry_caption_shards.py` preserves all
registry columns and adds `id=key`. It requires strictly increasing keys,
dense global indices, increasing parent indices, and exact population coverage.
This avoids keeping a population-sized Python ID set. Its 1,024-row I/O buffer
does not change the locked 50,000-caption inference groups. Input shards have
checksums and an atomically updated build state; incomplete shards are not
published as valid input. `validate_population_continuation_inputs.py` uses the
pinned T5 producer's own population schema and verifies every input shard.

`run_population_continuation.py --config <runtime-config.json>` waits for the
predecessor process to exit successfully with the exact COMPLETE identity and
population. It then runs the existing receipt/hash verifier, prepares and
validates input, runs T5, verifies its output and locked semantic identity, and
runs fixed-lexicon extraction. The step order is mandatory. Runtime configs pin
repositories, dependencies, source metadata, inventories, and smoke evidence.
Concrete deployment locators belong in operational configs.

## Unreaped Predecessor Incident

The first unattended handoff stalled after Lite completed because Linux kept
the detached guard as a zombie under a non-reaping container PID 1.
`kill(pid, 0)` succeeded even though no computation remained. The original
tests mocked the process predicate and did not exercise this OS transition.

Background jobs, incident markers, and report-server process checks now share
the zombie-aware predicate in `incident_gate.pid_is_running`. Linux `Z`/`X`
states are stopped, not running; unreadable/unknown state fails closed. An
exited PID alone is never success: the continuation still requires the pinned
COMPLETE identity/population and the subsequent receipt/hash verifier. PID
reuse checks and live-process waiting remain intact. Linux regression tests
create an actual unreaped child, then verify completed handoff, failed handoff,
and stale-marker incident detection before reaping the test child.

Recovery must replace only the idle controller after recording its state.
Completed Lite output and the pinned producer settings are not regenerated or
changed. Deploy the tested fix as an exact clean Git commit and verify an
actual transition out of predecessor waiting, not merely a live controller PID.

Start it through `run_background_job.py` inside the compute pod, with an owned
control directory and log paths. This retains incident gating and survives a
desktop disconnect. A Linux flock rejects duplicate controllers. Healthy
computation has no elapsed-time deadline. Dependencies, failures, storage
headroom, and producer admission checks can block progress; no automatic retry
or incident clearing is performed. On an explicit restart, both producers
reuse completed receipts and `--gpus` may select a different GPU set without
changing batching or input groups. A failed/stopped predecessor is never
implicitly restarted by the controller.

Full capacity admission uses the measured previous-run storage estimate plus
headroom, not the filesystem's total capacity or an assumed personal quota.
During execution a separate floor reserves room for active units. An exhausted
floor stops the child and retains completed receipts. Shared NVMe capacity is
not reserved by this estimate and must continue to be monitored.

The input-only environment pins PyArrow separately from the inference
environments. Missing Parquet support must not trigger installation into a live
T5 or lexical environment.

## File Cache And Completed Input Reuse

The first Full input export finished, but T5 admission then rejected all
workers: the cgroup charged nearly all used memory to clean inactive file
pages, not live Python heaps. Treating every charged byte as unavailable made
the existing host reserve fail before inference. No OOM was observed. Do not
lower that reserve or silently change the locked producer to bypass admission.

One-pass registry/input hashing, completed input validation, and T5 input/output
verification now advise Linux to discard clean cached pages of each consumed
file with `posix_fadvise(..., POSIX_FADV_DONTNEED)`. Input writers flush and fsync
before issuing this advice. It changes neither bytes, ordering nor fingerprints;
it does not drop global caches or allocate memory. Generic hashes remain
unchanged unless explicitly opted in. Platforms without this API keep the
ordinary I/O behavior; supported API errors are not silently swallowed.

This is advisory, not a guarantee of reclaimed capacity. The unchanged producer
admission must still pass on the actual pod. It is not a fix for future live
heap or GPU memory exhaustion. Check cgroup anon/file/dirty/writeback and OOM
counters separately from the total when diagnosing another admission failure.

A completed input export is reused only after source/config/manifest/lock and
every shard checksum and boundary are revalidated. It is not reparsed or
regenerated. Partial exports retain the existing ordered resume path. The
producer's population-schema validation remains a separate mandatory step.
Regression tests cover bytes/SHA preservation, file-scoped advice, unsupported
platforms, API errors, completed-export tampering, and T5 ID/order/receipt gates.

The expanded Windows regression exposed an atomic-replace PermissionError in
pause forwarding, which rewrote the same request on every poll. `request_pause`
now reuses an existing request for the same identity/attempt. Restart/attempt
checks and child-failure propagation remain in force; a deterministic test
rejects redundant rewrites. The full Windows suite passed outside the sandbox.
That result does not prove the underlying Windows permission/locking cause is
fixed; keep this execution-environment distinction in deployment evidence.

## Verification

The focused local suite covers copy integrity, input order/marker preservation,
partial-shard resume, receipt tampering, predecessor failure/identity mismatch,
gate ordering, and rejection of changed T5 settings. It passed 23 tests on
2026-08-28. A deployment must additionally perform a small real-Parquet export
and schema validation with the pinned producer before arming the full job.
