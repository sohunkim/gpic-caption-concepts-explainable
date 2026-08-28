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

## Verification

The focused local suite covers copy integrity, input order/marker preservation,
partial-shard resume, receipt tampering, predecessor failure/identity mismatch,
gate ordering, and rejection of changed T5 settings. It passed 23 tests on
2026-08-28. A deployment must additionally perform a small real-Parquet export
and schema validation with the pinned producer before arming the full job.
