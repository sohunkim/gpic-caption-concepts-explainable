# Real H200 Restart Verification (2026-08-28)

## Result

Passed a real Stage 1-6 inference test on 100 captions with this lifecycle:

| Attempt | H200 GPUs | New captions | Completed units after exit | Exit state |
|---|---:|---:|---:|---|
| Initial run | 1 (GPU 0) | 25 | 1 | paused |
| Resume | 2 (GPU 0, GPU 1) | 50 | 3 | paused |
| Resume | 1 (GPU 0) | 25 | 4 | completed |
| Uninterrupted baseline | 2 (GPU 0, GPU 1) | 100 | 4 | completed |

The verifier exited 0 and emitted `status=ok, rows=100`. It required:

- Actual descendant CUDA processes on each selected physical GPU, not just
  scheduler labels. Both GPU UUIDs were observed during the two-GPU resume.
- Same immutable run identity and 100 input rows in both COMPLETE artifacts.
- Exactly 1, 3, then 4 completed unit receipts across the resumed attempts.
- Previously completed receipts and retained artifacts unchanged by SHA256.
  The two-GPU resume reused 24 files from the first completed unit.
- Identical Stage 5 canonical mention/edge row multisets and all global
  Stage 6 count TSVs versus the uninterrupted baseline.
- Valid canonical-count retention receipts, retained artifact SHA checks,
  and absence of intermediates recorded as pruned.

The two-GPU resume/drain attempt took 26.343 seconds, including process setup
and both 25-caption units. This is a correctness smoke, not a production
throughput measurement. No inference speedup is inferred from that duration.

## Conditions

- Pod: `prod-rsv-snu14ksh-20260827-78e36c`, namespace `p-production`.
- Test checkout: `/mnt/nvme/gpic-scaleout/repos/lexical-pause-a9cb52b`.
- Verified clean code SHA: `4a2157a1c80d580637e01aaa901dd0f86027c9b0`.
- Python: `/mnt/ddn/prod-runs/snu14ksh/gpic-linux-env/bin/python`.
- GPU 0: NVIDIA H200, `GPU-cd31056f-4824-1760-9d49-0bd49177dce1`.
- GPU 1: NVIDIA H200, `GPU-e8bb65db-1e31-4ea6-3724-c098f5d3fd1a`.
- NVIDIA driver `580.126.16`; 143,771 MiB reported memory per GPU.
- Model `en_core_web_trf`, batch 192, disabled component `ner`.
- Two Stage 3 shards per GPU; two Stage 4-6 shards per unit, one job;
  one merge job; SQLite counts and `canonical_counts` retention.
- The retained mixed summary confirms `stage3_sentence.gpu_enabled=true`,
  `gpu_mode=require`, and successful GPU worker summaries.
- Source smoke manifest:
  `/mnt/nvme/gpic-scaleout/lexical-smoke100-input-4b6eff1/manifest.json`.
  SHA256: `287f9b4637502e95bd6ffea36dd69eae0ce2841d9d30964c35745bb387ceb054`.
- All source row fields and their order were preserved. The same four fixed
  25-caption units were used throughout. GPU count alone changed on restart;
  batch size and per-unit shard boundaries did not.
- Frozen inventory and preposition lexicon were read from the production
  checkout's `resources/gpic_inventory/current/inventory_bundle.json` and
  `resources/lexicons/preposition_mwes.tsv`. They were not edited.

## Evidence

Remote evidence root:
`/mnt/nvme/gpic-scaleout/real-gpu-restart-smoke100-20260828-4a2157a`

- `conditions.json`: code, input and inventory fingerprints, GPU metadata.
- `verification.json`: Stage 5/6 equality, retention audit, all attempt results.
- `one_gpu_pause.evidence.json`, `two_gpu_resume_pause.evidence.json`,
  `one_gpu_resume_finish.evidence.json`, `two_gpu_uninterrupted.evidence.json`:
  exact argv, PIDs, GPU UUID observations, pause request and attempt identity.
- `resumed/receipts/`, `baseline/receipts/`: per-unit provenance and hashes.
- Both output roots contain retained Stage 5 artifacts and Stage 6 tables.

Local tests: 46 passed before the first attempt. After correcting the diagnostic
input resolver, all 8 harness tests passed locally (6.92s) and on MLXP (3.85s).
The initial harness used `caption_id` instead of raw Lite `id` and stopped before
GPU inference. It now reuses the production Stage 1 ID resolver, with tests for
all supported schemas, conflicting IDs, and duplicate/missing IDs. Incident
`89ca5e321d30488c8074adc2a3e0f205` was resolved with that evidence in the isolated
checkout. The empty failed attempt root remains separate from the successful run.

## Scope And Production Status

This tests changing GPU selection after a normal planned pause, not physically
hot-removing GPUs, abrupt pod loss, or 8-GPU hardware. The 100-caption input and
two Stage 3 shards per GPU are smaller than production's eight shards per GPU.
CPU fixtures previously covered more scheduler cases; they were not presented
as real H200 validation.

The running 10M checkout, supervisor, output, and inventory were not modified
or stopped. At 01:08:42 KST it was running with 3,000,000/9,975,391 captions
complete and units 6/7 active. Pod memory then was about 224.8 GiB of its
480 GiB cgroup limit. Concurrent smoke work could affect throughput briefly.

After the GPU verifier had exited successfully, a separate metadata-read
command failed locally with `Wsl/Service/CreateInstance/HCS_E_CONNECTION_TIMEOUT`.
A 20-second local WSL echo probe also timed out. These were not inference
failures. No WSL-wide restart, production retry, or production termination was
performed. At this checkpoint the full result JSON remains on MLXP and a
post-verification production status refresh is not yet confirmed.

## Follow-Up After WSL Recovery

After the user restored WSL, a bounded echo probe and MLXP metadata reads
succeeded. The completed GPU smoke was not rerun. Its two small evidence
files were retrieved locally and checked against remote SHA256 values:

| Local file under `reports/` | Bytes | SHA256 |
|---|---:|---|
| `real_gpu_restart_smoke100_20260828.json` | 14,032 | `15515e4fc121d9c3f69075a3ec052fbd9bdc3f1f3a8984e3b053b7734cc2d06d` |
| `real_gpu_restart_smoke100_20260828_conditions.json` | 6,506 | `6c35f083cfa71e15cf08da780f5646f0a921d222b06fd85dae1a41fa18c1ea1a` |

The retrieved result confirms `status=ok`, 100 captions, 1,912 canonical
mentions, 1,192 canonical edges, and equality of all 10 Stage 6 TSVs. The final
one-GPU resume reused 72 files from the three completed units. No caption
payload or large dataset artifact was transferred through the desktop.

Production progress at 01:18:46 KST: 4,000,000 / 9,975,391 captions complete
(40.1%), 8 / 20 units complete, units 8 and 9 active on GPUs 0 and 1. The same
supervisor PID 22196 and lexical child PID 30432 were still alive. Cgroup memory
was about 126.3 / 480 GiB, with `oom=0` and `oom_kill=0`. The prior WSL failure
did not stop or restart these processes. No production code, input, inventory,
or output was changed during recovery verification.
