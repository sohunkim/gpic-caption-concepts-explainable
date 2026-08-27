# AGENTS.md

This repository is a fresh explainable baseline for GPIC caption-to-concept extraction.

The priority is explainability, not maximum recall.

## Required Reading

Before creating or editing any code, lexicon, report, or documentation, read:

1. `AGENTS.md`
2. `docs/rules_v1.md`
3. `docs/answer_protocol.md`

If the requested change conflicts with `docs/rules_v1.md`, stop and explain the conflict before editing.

## Current Project State

This project has the explainable v1 pipeline implemented through Stage 6.

Future work must still follow the Rule Gate before adding or changing rules.

## Active Workspace Guard

The active local project root after the OneDrive/junction migration is:

`C:\Users\rlath\Documents\Codex\gpic-caption-concepts-explainable`

This Codex conversation may still have a different default workspace. Before
running repository commands from a long-lived or migrated conversation, first
establish the active repo root:

- use the absolute `workdir` above for shell commands
- do not rely on the conversation default cwd
- when using `apply_patch` from a migrated/long-lived conversation, patch files
  by absolute path under the active repo root above. `apply_patch` does not take
  a shell `workdir`, so relative patch paths can silently edit the old
  conversation workspace instead of the active repository.
- if the current cwd is not the active root above, switch immediately to the
  active root for repo inspection and command execution
- if a command says `src`, `docs`, or expected pipeline files are missing, do
  not infer repo state from that cwd; treat it as the wrong workspace and retry
  from the active root above
- run `scripts\assert_active_workspace.ps1` before long-running scripts,
  generated-artifact commands, benchmarks, or tests whose result will guide the
  next decision
- if the guard fails, stop and fix the working directory before continuing

Do not treat "I will remember the new path" as sufficient. The path must be
verified by command evidence when the result matters.

## Remote Pod Command Guard

In this desktop Codex environment, the working MLXP Kubernetes context is in
the `Ubuntu-24.04` WSL distribution, not in the Windows `kubectl` picked from
Docker Desktop. Before declaring MLXP inaccessible, check:

- `wsl -d Ubuntu-24.04 -- bash -lc "kubectl config current-context"`
- use `wsl -d Ubuntu-24.04 -- bash -lc "kubectl -n p-production exec <pod> -- <argv...>"`
  for single bounded remote commands from this desktop session

The Windows command `kubectl config current-context` may report no context and
`kubectl` may try `localhost:8080`; that is evidence that the wrong local
kubectl context was used, not evidence that the MLXP pod is unavailable.

MLXP pod names are reservation-specific and expire. Do not rely on a hardcoded
default pod. When using `scripts\run_mlxp_bash.py` or
`scripts\run_mlxp_probe_bash.py`, prefer `--pod-prefix <reservation-prefix>` or
set `MLXP_POD_PREFIX=<reservation-prefix>` when the reservation has exactly one
Running pod, for example `--pod-prefix prod-rsv-snu14ksh-`. The runner resolves
the single Running pod and preflights it before sending the remote script. If
multiple pods match, pass `--pod <current-pod>` explicitly or set
`MLXP_POD=<current-pod>`. If no pod/prefix is set, the runner must fail before
issuing `kubectl`.

Do not run MLXP/Kubernetes pod commands by embedding long command strings in
nested local shells such as:

- `wsl.exe bash -lc "kubectl ... -- bash -lc '...$(...)...; ...'"`
- PowerShell strings that contain shell substitutions, semicolons, redirection,
  heredocs, or quoting that should be interpreted only inside the pod

This failure mode can silently execute part of the command on the local WSL or
PowerShell side instead of inside the pod, which can create local artifacts or
clone the repository in the wrong place.

Preferred remote execution patterns:

- use direct argv commands:
  `kubectl -n <ns> exec <pod> -- git -C /root/work/repo status --short`
- for file transfer from this desktop session, use direct argv `kubectl cp`
  through `Ubuntu-24.04` WSL and do not combine the copy with a second remote
  command in the same local `bash -lc` string. Run verification/extraction as a
  separate `scripts\run_mlxp_bash.py --pod-prefix <prefix> <script.sh>` step
  when possible, or `--pod <pod>` when multiple Running pods share the prefix.
- when running `kubectl exec` from PowerShell, do not append shell pipes such as
  `| grep` to the command. PowerShell interprets the pipe locally after
  `kubectl` returns. Either print the bounded remote output directly, use
  `scripts\run_mlxp_bash.py <script.sh>` for remote shell filtering, or filter
  intentionally with PowerShell-native commands after capturing the output.
- from this Windows/Codex desktop workspace, use
  `scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds <N> -- scripts\run_mlxp_bash.py <script.sh>`
  for bounded multi-step MLXP diagnostics. For formal Stage 4/5/6 or another
  healthy progress-producing job that must not have a wall-clock kill, use
  `scripts\run_python.ps1 scripts\run_mlxp_bash.py <script.sh>` in the
  foreground. The local `<script.sh>` must be written with ASCII or UTF-8
  without BOM. Do not pipe ad hoc PowerShell here-strings directly into
  `wsl ... kubectl exec ... bash`.
- In the Codex desktop tool environment, do not launch a formal or bulk-copy
  `run_mlxp_bash.py` command through a `shell_command` call that retains the
  tool's short/default timeout. Launch it as a yielding exec cell with a
  practically non-expiring tool timeout, then resume that same cell with
  `wait`. The repository command must remain foreground and unbounded; the
  cell is the monitor. A partial DDN transfer must write to a resumable
  `.partial` directory and may be promoted only after a no-difference rsync
  dry-run and required artifact hash checks.
- Do not run `scripts\run_script_with_timeout.py`,
  `scripts\run_mlxp_bash.py`, `scripts\run_mlxp_probe_bash.py`, or
  `scripts\incident_gate.py run` in `multi_tool_use.parallel`. They share the
  incident/current-run guard, so parallel guarded executions can block each
  other and produce misleading failures. Run guarded commands serially.
- when invoking `scripts\run_mlxp_bash.py` or `scripts\run_mlxp_probe_bash.py`
  from this Codex desktop tool environment, request sandbox escalation. Without
  escalation, WSL instance creation can fail locally with
  `Wsl/Service/CreateInstance/E_ACCESSDENIED` before the MLXP command starts.
- for multi-step remote work, create a checked script file with LF line endings
  and no BOM, copy it to the pod, then execute that script explicitly
- if stdin script execution is used, first verify no BOM/CRLF issue with a
  bounded `pwd`, `whoami`, and `hostname` probe
- for remote patch transfer, do not create `git apply` patch files with
  PowerShell `Set-Content`. Use `git diff --output <patch>` or another
  byte-preserving writer, verify the first bytes are not `EF BB BF`, and run
  `git apply --check` inside the pod before applying.
- for partial remote code-sync zip files, do not use `Compress-Archive` with a
  bare list of individual files unless the archive entries have been inspected.
  It can flatten paths, causing extraction into the remote repository root.
  Prefer `scripts\build_code_sync_zip.py`, which uses Python `zipfile` with
  explicit repository-relative `arcname` entries and prints the archive
  contents before upload.
- when running Python tests or scripts directly inside the MLXP repo, set
  `PYTHONPATH=<remote_repo>/src${PYTHONPATH:+:$PYTHONPATH}` unless the command
  is executed through a wrapper that is already verified to add the source tree.
  Local `scripts\run_python.ps1` hides this requirement on Windows; the remote
  pod does not. A remote test failure with `ModuleNotFoundError:
  gpic_concepts_v1` is an execution-environment mistake, not a code failure.
- do not execute Windows PowerShell test wrappers such as `scripts/run_tests.ps1`
  directly inside Linux MLXP pods. For direct remote tests, use the verified
  MLXP Python interpreter with `PYTHONPATH` set, for example
  `PYTHONPATH=<remote_repo>/src /root/work/gpic-linux-env/bin/python -m pytest
  -p no:cacheprovider ...`.
- if an incident-clear verification command would need to pass another command
  containing its own `--`, prefer running the verification as a separate
  bounded probe and then clear with the captured evidence. Nested argparse
  remainder parsing is easy to misroute.
- do not pass `.ps1` files directly as `incident_gate.py clear
  --verify-command` executables. Python `subprocess` does not execute
  PowerShell scripts directly on Windows and raises `WinError 193`. Use the
  project Python executable directly for Python scripts, or call
  `powershell.exe -File` explicitly when PowerShell is truly required.
- clearing an incident mutates `.pipeline_state`. In migrated desktop sessions,
  the active repo may be outside the sandbox writable roots even when tests can
  run through approved wrappers. If `incident_gate.py clear` raises
  `PermissionError` while writing `.pipeline_state`, confirm the active repo
  path and rerun the clear command with explicit sandbox escalation; do not
  misdiagnose it as pipeline data corruption or a remote failure.

Before trusting a remote command result, verify the execution context with
command evidence from inside the pod:

- `pwd`
- `hostname`
- `whoami`
- expected repo branch and commit, using `git -C <remote_repo>`

Re-check the interpreter in each target pod, including storage shells. A shared
venv directory can contain a broken symlink to an image-local Python binary;
success on a GPU pod does not prove that its storage-shell sibling can execute
it. Read-only metadata probes should use the verified system interpreter and
check required imports before use. Formal launchers must validate their exact
configured interpreter and dependencies before creating output or loading a
model; never silently substitute a different environment for inference.

Do not assume an MLXP pod can fetch from GitHub. Before attempting a remote
`git fetch`, `git pull`, or `git merge` that depends on GitHub access, first
run a bounded auth-free probe with `GIT_TERMINAL_PROMPT=0`. If the probe would
prompt for credentials or fails because remote auth is unavailable, do not use
remote Git sync as part of the benchmark path. Either continue with the already
verified remote commit when the local runner supplies the needed wrapper
behavior, or use an explicit file/bundle transfer path. Do not let a benchmark
run depend on an interactive Git credential prompt inside the pod.

For GitHub SSH deploy-key validation, do not use `ssh -T git@github.com` exit
code as the official success criterion inside a guarded command. GitHub can
return exit code `1` even when authentication succeeds because it does not
provide shell access. Use `git ls-remote` against the intended SSH repository
URL as the guarded verification, or capture and interpret the `ssh -T` message
without letting its nonzero exit fail the official run.

For local-to-MLXP or MLXP-to-local code handoff, GitHub is the default path for
code, tests, documentation, and small versioned inventory files. Large data,
report DBs, generated outputs, and transfer packages still move by explicit
archive/copy with SHA-256 verification and must not be committed just to make a
handoff convenient.

Before a formal remote run from MLXP:

- commit the intended local code on a scoped branch and push it
- clone/fetch that branch on MLXP into a fresh or intentionally selected
  checkout, then checkout the exact commit SHA
- verify the remote checkout with:

  `python scripts/verify_git_handoff.py --repo <remote_repo> --expected-commit <40-char-sha> --require-clean`

- record the verified commit SHA in the run summary, benchmark note, transfer
  manifest, or user-facing report

Do not run a formal MLXP pipeline from a dirty local worktree, a dirty remote
checkout, or an unverified mutable clone. If GitHub access is unavailable, the
fallback is an explicit immutable code snapshot with a manifest and a written
reason for not using GitHub; do not silently mix the two handoff modes.

If any command unexpectedly reports a Windows path, the conversation default
workspace, or a local WSL path while the intended target was MLXP, stop
immediately. Do not continue the main pipeline until the wrong local artifact
has been identified and either removed with an absolute-path safety check or
explicitly quarantined.

## Password SSH/SCP Automation Guard

For password-based SSH or SCP automation from this Windows/Codex desktop
workspace, do not use `pty.openpty()` with `subprocess.Popen` or a hand-built
PowerShell/WSL expect loop. OpenSSH password prompts require a controlling
terminal, and the non-controlling PTY path can make a correct password look
wrong or leave a hanging transfer.

Use `scripts/run_password_ssh_pty.py` under Linux/WSL for password-prompting
`ssh` and `scp` commands. The password must come from an environment variable;
do not hardcode it in the script or repository.

For multi-step Blackwell or other password-SSH remote work, do not embed a long
`bash -lc` script inside a PowerShell string. Write a local `.sh` script, upload
that script with `scp` through `scripts/run_password_ssh_pty.py`, and execute
the uploaded script with `ssh` through the same helper. This avoids local
PowerShell parsing remote shell operators such as `&&`, `|`, heredocs, or
variable substitutions.

Before any large upload or remote mutation, first run a bounded remote probe
through the same helper, for example an `echo ok` command. Do not infer that an
SSH password is wrong until this controlling-terminal path or a direct
interactive terminal has been tested.

If a password-based transfer fails:

1. identify whether the prompt was reached
2. verify the helper was the `pty.fork()` runner, not `pty.openpty()`
3. run a small `ssh ... echo ok` probe through the same path
4. only then retry the large transfer or ask the user to verify credentials

When a remote MLXP/Kubernetes background job is started from this conversation,
the local background-job guard is not sufficient. Record the remote status JSON
path and PID file in the conversation and in the user-facing update. Before
sending a final answer or otherwise ending the turn while that remote job may
still be active, check all of the following from inside the pod:

- remote status JSON, including `status`, `phase`, elapsed time, and updated
  timestamp
- remote parent PID and current child PID with `ps`
- expected output directory/file growth or a completed summary artifact

When checking a remote background PID, do not treat `ps -p <pid>` alone as
proof that work is still active. A `STAT` containing `Z` or a command shown as
`<defunct>` is a zombie: the worker has exited and is waiting to be reaped, so
it must be reported as not actively running. Use the progress/status artifact
and summary output to determine completion or failure.

If the remote job is still running, do not send a final answer that implies the
work is finished. Continue polling it, or explicitly state that monitoring is
being handed back to the user only if the user asked to stop active monitoring.
Do not rely on `scripts\list_active_background_jobs.py` for remote jobs; it
only sees local repository-managed jobs.

For MLXP/Kubernetes resource safety, do not treat `free -h` inside the pod as
the pod's usable memory limit. It may show node-level memory. Before launching
or approving a production-scale run, check the pod resource limit with
Kubernetes evidence such as:

- `kubectl -n <ns> get pod <pod> -o jsonpath="{.spec.containers[0].resources}"`
- or the active cgroup memory limit when available

Report memory safety against the pod/container `limits.memory`, not against
node-level available memory. If a previous phase has approached the limit, do
not rerun the same memory shape. Change the implementation to streaming,
chunked, or disk-backed processing first.

## Storage And I/O Diagnosis Guard

Do not claim an NVMe-vs-DDN/Lustre comparison unless command output proves the
tested files are on different filesystems. Before drawing a storage conclusion,
print and inspect all of:

- absolute path for each tested DB/file
- `df -hT <path>` for each tested DB/file
- `stat -f -c '%T %m' <path>` when available
- `readlink -f <path>` when symlinks or generated paths may be involved

If both test files are under the same mount, the comparison is invalid. Record
that as an invalid probe, not as evidence.

For SQLite/report performance, small empty-DB probes do not reproduce a
production-sized primary-key index. Label them only as connectivity or basic
write smoke tests. Do not use them to explain million-scale behavior unless the
probe reproduces the relevant shape: existing DB size, index cardinality,
primary-key/unique constraints, `INSERT OR IGNORE`, batch size, and target
filesystem.

When diagnosing sudden slowdown, separate observed facts from inferred causes:

- observed: process state, CPU, RSS, `wchan`, `/proc/<pid>/io` deltas, DB size,
  progress deltas, fact type around the slowdown
- inferred: SQLite index growth, dirty-page writeback, filesystem contention,
  cache spill, fact-type distribution, or lock contention

Do not state an inferred cause as the direct cause until the evidence
distinguishes it from the alternatives.

When polling a remote long-running job, avoid launching overlapping sleep-based
pollers from the same conversation. If a poll command uses `Start-Sleep`, wait
for that tool call to finish before starting another poll. If duplicate pollers
are suspected, identify them as pollers, not worker jobs, and confirm the main
worker PID separately.

## Long-Running Process Guard

Long-running pipeline jobs must not be launched with ad hoc PowerShell
`Start-Process`, `cmd.exe /c start`, `start /b`, visible helper windows, or
hand-built shell backgrounding. Those launch modes have caused stale launcher
processes and hidden non-started jobs.

Use only these supported paths:

- foreground bounded execution:
  `scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds N <script> ...`
- detached/background execution:
  `scripts\run_python.ps1 scripts\run_background_job.py start --pid-file <json> --stdout <log> --stderr <log> --cwd <repo> -- <command> ...`
- detached/background adoption for an already-running valid job:
  `scripts\run_python.ps1 scripts\run_background_job.py adopt --pid <pid> --pid-file <json> --cwd <repo> ...`
- detached/background status:
  `scripts\run_python.ps1 scripts\run_background_job.py status --pid-file <json> --progress-output <progress.json>`
- detached/background bounded watch:
  `scripts\run_python.ps1 scripts\run_background_job.py watch --pid-file <json> --progress-output <progress.json> --expect-output <path> --max-seconds N`

When a background job is started, immediately verify all of the following:

- the pid file exists and reports `running: true`
- the expected child process is visible, not just a launcher process
- stdout/stderr log files are being written or intentionally empty
- the expected output artifact is either absent because the job is still in
  progress, or present with a fresh timestamp after completion
- long inventory builders that support progress artifacts are writing a fresh
  `*_progress.json` file with `status`, `phase`, processed counts, and
  `updated_at_utc`
- large Stage 3.5 inventory scans must be launched through
  `run_stage35_inventory_workflow.py` or with the relevant object/attribute/action
  inventory builder plus `--checkpoint-output <json> --resume-checkpoint`; this
  is one checkpoint JSON file per builder that is updated by atomic replace, not
  a pile of per-interval files. A progress JSON alone is not enough because it
  cannot resume after power loss, network loss, or a manually requested stop.

While a long job is running in the current Codex turn, do not merely say that a
progress file exists. Actively poll the available progress artifact or bounded
watch output and report concise user-facing updates at least every 30-60
seconds. Each update should include the current `status`, `phase`, key processed
counts such as `caption_total` or `inventory_rows_so_far`, and whether a manual
blocker/output artifact has appeared. If the progress artifact is missing,
stale, unreadable, or no longer changing while the process is still running,
inspect the process state before continuing.

Before sending a final answer or otherwise ending a turn after any background
job was started, adopted, or may still be active, run one serial pre-final
check:

`scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 60 -- scripts\run_pre_final_sanity.py --background-root outputs --fail-if-background-running`

When a GitHub-backed code handoff was just pushed, include the expected commit
in the same serial check:

`scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 60 -- scripts\run_pre_final_sanity.py --expected-commit <40-char-sha> --require-clean --background-root outputs --fail-if-background-running`

Do not split these into parallel `verify_git_handoff.py` and
`list_active_background_jobs.py` guarded commands. The incident gate is
repository-global; parallel guarded checks can interrupt each other and create
an incident while no pipeline work is actually wrong.

The legacy direct background-only check remains useful only as a narrow
diagnostic:

`scripts\run_python.ps1 scripts\run_script_with_timeout.py --timeout-seconds 60 -- scripts\list_active_background_jobs.py --root outputs --fail-if-running`

If the serial pre-final check reports an active job, do not send a final
answer. Continue polling the job, or explicitly stop/quarantine the job only
when it is stale or wrong. This check is the durable pre-final guard; do not
rely on memory that a job was "probably done."

If a background launcher is visible but no expected child process exists, stop
the launcher and treat the job as not started.

For million-scale Stage 3.5 runs, do not use wall-clock timeout as a progress
health check. A healthy progress-producing scan must not be killed only because
it crossed an arbitrary elapsed-time boundary. Use progress heartbeat monitoring
and checkpoint/resume; set child-script wall-clock timeout to `0` unless a
specific short diagnostic command needs a bounded timeout.

The current formal Stage 4/5/6 scripts are memory-guarded. They share the same
RSS safety contract:

- try to read the active Linux cgroup memory limit when no explicit
  `--memory-limit-gib` is provided
- compute `max_rss_gib = min(memory_limit_gib * rss_limit_fraction,
  memory_limit_gib - rss_reserve_gib)` unless `--max-rss-gib` explicitly
  overrides it
- write a single progress JSON by atomic replace when `--progress` or the mixed
  pipeline's stage-specific progress paths are provided
- raise `MemoryError` when a sampled RSS crosses the computed guard. Periodic
  checks cannot guarantee interception of a native allocation between samples.

Do not add memory safety to only one of Stage 4, Stage 5, or Stage 6. If a
memory/process failure pattern is discovered in one formal stage, inspect the
other formal stages for the same pattern and add or update a cross-stage test
when possible. `tests/test_formal_stage_memory_safety.py` is the contract test
that keeps the shared memory/progress surface from drifting across stages.

The implementation is partly disk-backed: Stage 4 still holds the raw graph
until writing, Stage 5 keeps canonical mentions as the edge lookup table, and
Stage 6 streams fact rows while using SQLite as the default count accumulator
backend. Stage 6's SQLite backend must flush adaptively from the active
cgroup/explicit RSS safety limit by default; fixed row-count cache limits are
diagnostic hard caps only, not the production policy. Stage 6's in-memory
backend is only for bounded diagnostics. If a formal production run trips the
RSS guard, do not raise the limit and rerun blindly. Change that stage to a
chunked, streaming, or disk-backed implementation first.

The budget belongs to a process tree, not each child independently. Use
`runtime_memory.child_memory_kwargs` at every parallel dispatch boundary.
Final count merging is also a memory-heavy stage: `merge_stage6_count_dirs`
and its ordinary/partitioned helpers must share `CountMergeStore`, including
the fixed-lexicon runner's final merge. A partition count based on CPU jobs is
not a memory bound. Require forced-spill byte equality for every count table
and nested budget propagation tests when changing these paths. Missing memory
measurements must not fall back to unbounded accumulation. Existing production
processes keep their pinned code; do not claim a new guard is active in them.

Do not wrap formal Stage 4/5/6 scripts in `run_script_with_timeout.py` for
production-scale work. That wrapper uses a hard `os._exit` kill and Stage 4/5/6
currently do not have checkpoint/resume. The wrapper refuses
`run_mixed_caption_pipeline.py`, `run_stage4_extract_raw.py`,
`run_stage5_canonicalize.py`, and `run_stage6_export_counts.py` by default; use
`--allow-stage456-timeout` only for a deliberately bounded smoke diagnostic.
`run_mixed_caption_pipeline.py --dry-run` is the safe exception: it performs no
pipeline stage execution and may be run through the timeout wrapper to inspect
the hardware-aware resource plan.

## Incident Response Guard

The response order below is enforced for official execution by
`scripts/incident_gate.py`, using repository-fixed state under
`.pipeline_state/`:

- `.pipeline_state/incident.json`: unresolved incident; official execution is blocked
- `.pipeline_state/running.json`: currently active or incompletely terminated run
- `.pipeline_state/incident_history.jsonl`: resolved incident audit history

Do not delete or edit these files manually. Inspect them with:

`scripts\run_python.ps1 scripts\incident_gate.py status`

Clear an incident only with `scripts\incident_gate.py clear` and non-empty
root-cause, guard-added, and verification-evidence fields. Use
`--verify-command` when an executable regression check exists; a failing
verification command must leave the incident open.

Official Stage 1-6, Stage 3.5, mixed pipeline, inventory publish, timeout,
MLXP, and password SSH/SCP entrypoints must use `guarded_entrypoint`. The
background launcher must wrap the detached child with `incident_gate.py run`;
do not guard only the launcher process. Diagnostic and test commands must stay
available while an incident is open so that the incident can be investigated
and cleared with evidence.

When the user points out a repeated mistake, a violated project rule, a stuck
process, a missing timeout, a wrong workspace, or an untracked background job,
do not resume the main pipeline first.

Follow this order:

1. Inspect the current external state with command evidence.
2. Stop or quarantine only the stale/incorrect process or artifact if one exists.
3. Identify the concrete failure path, not just the symptom.
4. Add or update a durable guard in code, wrapper scripts, or AGENTS.md so the
   same failure path is harder to repeat.
5. Verify the guard with a bounded command.
6. Only then resume the main pipeline step.

Also update `docs/incident_response_log_v1.md` with:

- what failed
- why it happened
- what durable guard was added or updated
- how that guard was verified
- what command/output proves the main pipeline was safe to resume

Do not answer with only an apology, plan, or explanation when a durable guard is
possible. If no durable guard is possible, say exactly why and record the manual
check that must happen before resuming.

## Scope Boundary

Only the pipeline in `docs/rules_v1.md` is allowed:

1. caption shape judgment
2. spaCy preprocessing
3. spaCy linguistic annotation
3.5. GPIC observed object inventory preparation
4. raw concept extraction
5. canonicalization
6. count export

Stage 3.5 is an explicit offline preparation step between Stage 3 and Stage 4,
not a hidden extraction or repair stage.

Do not add stages, hidden repairs, fallback chains, or extra lexicon families unless the user explicitly approves an update to `docs/rules_v1.md`.

## Rule Gate

Every extraction or transformation rule must be written in `docs/rules_v1.md` before implementation.

Each rule must state:

- rule id
- stage
- input
- output
- tool
- tool type
- rule type
- count impact
- known limitation

If a rule cannot be explained in this format, do not implement it.

### Rule Impact Review Gate

Do not treat rule impact review as conversation memory.

Before adding or changing any rule or lexicon that can affect extraction,
canonicalization, or counts, record a review in `docs/rule_change_review_log_v1.md`.

Each review must state:

- proposed rule or lexicon change
- rule generality classification:
  - general rule
  - source-specific evidence rule
  - explicit user-approved manual decision
  - one-off patch / rescue mapping
- target stage and rule id
- existing rules affected
- expected count-table impact
- false positive risk
- false negative risk
- reversibility, including source columns, rule ids, or metadata
- verification plan
- decision status

Do not implement the change until the review is written and the user approves
the decision.

Before editing code, classify the proposed change. If it is a one-off patch,
dataset-label rescue mapping, or semantic alias made to save a single case, do
not implement it as an automatic rule. Leave the case unresolved, ambiguous, or
rejected unless the user explicitly approves the exact manual decision.

### Lexicon Candidate Gate

Do not add semantic aliases, label-specific rescue mappings, or one-off manual
lookups while building source-label candidates.

Allowed automatic lookup recovery is limited to documented lexical/formal
normalization, such as:

- case normalization
- whitespace normalization
- hyphen, underscore, and space separator variants
- joined separator variants
- WordNet/OEWN Morphy results

Forbidden unless the user explicitly approves the exact label decision:

- semantic aliasing, such as `potted plant -> pot plant`
- head fallback, such as `sports ball -> ball`
- dataset-label rescue mappings for a single failed label
- manual query replacement hidden inside candidate-generation code

If a source label cannot be resolved by the approved automatic lookup rules,
leave it `unresolved`. Synset selection is for choosing one representative noun
sense for the source object label, not for verifying the actual image object
sense. If a label appears not to be a standalone object noun, leave that as
source-label evidence or an unresolved/ambiguous candidate unless the user
explicitly approves a manual decision.

Manual decisions may select or reject a synset only after explicit user
approval. They must not silently change the lookup query used to justify the
row.

Once the user provides an explicit manual decision TSV or exact row decision,
that decision is authoritative for the pipeline even if a later semantic audit
would have preferred another synset. Treat semantic audit findings as advisory
notes only. Do not reopen, override, or block a user-approved manual decision
unless the user explicitly asks to revise that row.

## Allowed In V1

Allowed rule families are only:

- caption shape judgment: sentence vs tag-list
- spaCy tokenization
- raw quote span merge
- plain hyphen word merge
- spaCy tagger
- spaCy dependency parser
- spaCy attribute ruler
- spaCy lemmatizer
- spaCy noun chunks
- GPIC observed object span inventory built from Stage 3 GPIC records
- noun chunk selected span to object through the GPIC observed inventory
- noun chunk modifier to attribute or quantity
- VERB token to action
- `nsubj` child to agent
- `obj` or `dobj` child to patient
- ADP/preposition plus direct `pobj` to relation
- reviewed preposition MWE lexicon span plus final direct `pobj` to relation
- preposition MWE source/target candidate preservation from direct
  object-mapped dependency evidence, including missing-endpoint ambiguous
  occurrence audit rows
- object synonym canonicalization
- attribute synonym canonicalization
- attribute type taxonomy only as an offline audit artifact, not active Stage 5/6 output
- quantity raw-preserving canonicalization
- action synonym canonicalization
- action type mapping
- object parent concept mapping
- relation raw-preserving policy for single ADP relations
- relation MWE label preservation and relation component count export from
  documented Stage 4 preposition MWE metadata
- flat count export

External source-label inventories such as COCO, LVIS, Objects365, OpenImages,
Visual Genome, V3Det, or ImageNet are not active runtime inputs for the GPIC
caption pipeline unless `docs/rules_v1.md` is explicitly updated. Treat those
files as historical probes or offline evidence only.

## Forbidden In V1

Do not implement these in v1:

- pronoun resolution
- generic anaphora resolution
- `one`, `another`, `others`, `both` instance splitting
- passive voice normalization
- inherited agent repair
- skipped reference role recovery
- self-edge repair
- semantic PP source disambiguation
- with-absolute recovery
- scene context fallback rules
- undocumented relation MWE repair or semantic relation source/target recovery
- phrasal action collapse
- broad hidden hardcoded word lists inside Python code
- GPIC-error-specific patch rules
- any new linguistic interpretation during count export

## Implementation Rules

When implementation begins:

- keep functions small and single-purpose
- keep rule ids visible in output rows
- store lexicons as TSV files under `resources/lexicons`
- do not hide policy inside code comments
- do not copy logic from the previous prototype
- do not add a dependency without documenting why it is needed
- do not change generated reports by hand

## Communication Rules

When explaining this repository:

- use the six v1 stage numbers from `docs/rules_v1.md`
- distinguish implemented behavior from proposed behavior
- say "not implemented" when a feature is absent
- say "excluded by v1 design" when a feature is intentionally omitted
- do not describe this as high-recall

Use this description:

> an explainable caption-to-concept baseline with documented limitations

## Repeated Benchmark Rules

Do not treat repeated benchmark requirements as conversation memory.

If the user says a benchmark condition must be checked "from now on" or "for
future experiments", encode it in one of these durable places before relying on
it:

- the benchmark script summary output
- this `AGENTS.md`
- `docs/answer_protocol.md`
- a benchmark-specific document under `docs/`

For GPU benchmarks, record hardware/runtime metadata in the benchmark summary
when `nvidia-smi` is available:

- GPU name
- driver version
- CUDA version if reported by `nvidia-smi`
- observed power limit in watts
- observed power draw in watts
- GPU pstate

Do not say "I will check it manually next time" as a substitute for durable
benchmark instrumentation or documentation.

For portable fixed-lexicon Stage 1-6 speed runs on MLXP or any unknown hardware,
prefer `scripts/run_mixed_caption_pipeline.py --auto-resources` over hardcoded
CPU/GPU counts. The mixed runner must record `runtime_resource_plan` in its
summary/progress output; benchmark writeups should cite that field when
explaining CPU/GPU/shard choices.

For benchmark answers, do not fix reasoning mistakes by adding more checklist
rules. Separate reporting from analysis. If a causal explanation is given, first
use the analysis unit built for the benchmark; in this project that means the
stage-level timing fields. If that analysis has not been done, report the
measurement and say the cause is not established.

## Durable Process Judgment Rules

Do not treat newly discovered process rules as conversation-only conclusions.

If a recurring workflow problem is identified during the thread, decide whether
it needs durable documentation before moving on.

Examples include:

- what kinds of experiments must be logged
- what benchmark metadata must be recorded
- what checks must run before reporting results
- what optimization attempts were tried and rejected
- what practices caused confusion and should not be repeated

If the process judgment should affect future work, encode it in one of these
places before relying on it:

- this `AGENTS.md`
- `docs/answer_protocol.md`
- a topic-specific document under `docs/`
- the relevant script output or summary file

Do not merely say "we should do this from now on" when the point is meant to
survive context loss.

### Decision-Impacting Test Records

Do not treat all test runs the same.

Routine unit test passes may be reported in the final answer or commit message
without adding a separate durable log entry.

However, record the test command, result, and interpretation in a durable place
when the test affects a future decision. This includes:

- a test failure and the root cause or fix
- a regression test used to accept a code change
- a benchmark-adjacent test used before or after a speed comparison
- a test used to accept or reject an optimization
- a test result used to decide the next work direction

Use the relevant existing document when possible, such as a benchmark document,
an optimization log, or a topic-specific file under `docs/`.

Do not say "the tests passed, so this is enough" if that test result is being
used as evidence for a durable technical decision but is not recorded anywhere
except the chat.

### Test Runner Safety

Default to `unittest` in this repository.

Use the bounded wrappers:

- `scripts\run_tests.ps1`
- `scripts\diagnose_test_runtime.ps1`
- `scripts\run_unittest_with_timeout.py`
- `scripts\run_pytest_with_timeout.py`

`scripts\run_tests.ps1` runs `unittest` by default. Use pytest only as a
diagnostic exception by passing `--pytest`.

The unittest wrapper runs in-process and uses a hard `os._exit` timeout. This
keeps the default runner simpler than pytest, but it does not by itself bypass
Codex sandbox file-write restrictions.

The pytest wrappers run pytest with a child-process timeout and disable pytest's
cache provider. This avoids hangs from pytest cache finalization in
junction/OneDrive or sandboxed paths.

The wrappers default test temp files to
`C:\Users\Public\Documents\ESTsoft\CreatorTemp\gpic-explainable-link-tests`
when available. If a sandboxed test run fails with `PermissionError`
while writing inside a temp directory, do not infer that the pipeline code is
broken. First verify with the same bounded command outside the sandbox and, when
useful, with a narrow `run_python.ps1 -c` write probe.

Do not run an all-in-one diagnostic group. Run one bounded group at a time.

For syntax-only validation of a small edited file set, prefer
`run_python.ps1 -c` with `ast.parse()` over `python -m compileall`.
`compileall` writes `__pycache__` files and can fail with unrelated
`PermissionError` or file-lock issues in this workspace. Use `compileall` only
when bytecode generation itself is the thing being tested.

Do not use `scripts\run_python.ps1 -m unittest` or
`scripts\run_python.ps1 -m pytest` as a validation runner. `run_python.ps1`
only locates the project Python and executes the arguments; it is not the
repository's test timeout boundary. Use `scripts\run_tests.ps1` or the bounded
Python test wrappers instead.

`scripts\run_python.ps1` enforces this rule: it rejects direct interactive
Python, `-m unittest`, `-m pytest`, and direct `.py` script execution except for
the bounded wrapper scripts. Do not bypass that guard.

After any interrupted, hung, or user-aborted test command, do not immediately
rerun a test. First inspect for stale project `python.exe` or PowerShell
processes, stop only the stale processes that belong to the interrupted command,
and record the root cause if it affects future work.

### Long-Lived Report Server Safety

Never run `report_server.py`,
`scripts/launch_packaged_interactive_report.py`, or another
`serve_forever()` entry point as a foreground repository command. A report
server is a long-lived service, so process completion is not a valid readiness
condition.

Use only `scripts/run_report_server_operation.py` for report-server control:

- `start` launches through `manage_report_server.py`, waits only for bounded
  health readiness, then returns while the detached server continues
- `status` performs one bounded health/process check and returns
- `stop` performs one bounded shutdown and returns

Every `start` call must supply both a positive outer
`--operation-timeout-seconds` and a shorter positive inner
`--readiness-timeout-seconds`. Generated report launchers must use this
controller. Do not invoke a generated `.bat` through an unbounded
`cmd.exe /c call` command; either run the controller directly or give the shell
command a timeout longer than the controller's outer deadline.

For test temp paths in this repository, avoid `Path.resolve()` when deriving a
repo-local temp directory. This repo can be opened through a junction/link, and
`resolve()` may silently switch from the logical Codex path to the OneDrive
target path. Prefer an explicit temp root or a non-resolving logical repo path.
If a temp helper falls back to a shared Public temp path after a write failure,
do not keep rerunning the test; inspect the temp path decision first.

### Filesystem Write And Escalation Policy

Do not treat filesystem permissions as conversation memory.

Use `apply_patch` for manual edits to repository files, including code,
documentation, small TSV lexicons, and small configuration files.

Use Python or command-line scripts for generated artifacts only when the file is
better produced mechanically, such as large TSV/JSON/HTML/Markdown outputs,
downloaded resources, or benchmark reports.

For generated artifacts:

1. Before running a generated-artifact command, check whether the command writes
   inside the current sandbox writable roots.
   - If the target repository or output directory is outside the writable roots,
     do not run a doomed sandbox attempt first.
   - Run the same narrow bounded command with `require_escalated` from the
     beginning.
   - State that this is required because the active repo/output path is outside
     the sandbox writable roots, not because the script or pipeline logic is
     broken.
2. Run the script normally first only when it is expected to write inside the
   current writable workspace.
3. Generated TSV/CSV writers should write to a same-directory temporary file and
   then replace the final path atomically. Do not rewrite final TSV/CSV paths
   directly with `open("w")` when the output is a generated artifact.
4. Long-running generated-artifact Python scripts must be executed through
   `scripts\run_script_with_timeout.py`, not by calling the target script
   directly through `scripts\run_python.ps1`. The runner executes the target
   script in-process and uses a hard `os._exit` timeout so a timed-out script
   does not leave a Python child process behind.
5. If it fails with a sandbox, junction, OneDrive, network, cache, or
   `PermissionError` problem, rerun the same narrow command with
   `require_escalated`.
6. Do not describe `require_escalated` as a fix for the permission problem. It is
   an approved outside-sandbox execution for that command.
7. Keep generated output paths explicit. Avoid relying on `Path.resolve()` when a
   repository path may be a junction to OneDrive.
8. Keep temp and cache paths explicit when a script writes temporary files.
9. Record durable decisions about generated artifacts, permission failures, or
   escalation use in the relevant log or document when they affect future work.

Do not use shell write tricks such as `echo > file`, `Set-Content`, or Python
one-off writes for manual repository edits when `apply_patch` can express the
change.

### Generated Report Summary Sync

Do not treat Markdown report summaries as self-validating.

### Remote Report Code Snapshots

Do not build a formal remote interactive report from a mutable or previously
cloned repository checkout. Inventory/data hashes do not prove that report
schema and caption-index code are current.

For formal MLXP report builds:

- package the local `scripts/` and `src/` trees as an immutable code snapshot
  with a SHA-256 manifest
- use a portable tar archive for Windows-to-Linux snapshots and reject archive
  entry names containing backslashes before transfer
- copy and verify that snapshot on the pod before creating the base report
- run base-report, caption-index, validation, and packaging commands from that
  same extracted snapshot
- pin and check the hashes of
  `build_interactive_count_report.py`,
  `build_report_caption_index_from_facts.py`,
  `build_report_caption_index_from_stage5.py`, and
  `validate_interactive_report_db.py`
- require the report DB to contain every `VIEW_KEY_COLUMNS` column before
  dropping or rebuilding an existing caption index
- use `--check-all-caption-counts` for the final formal report validation

If the code snapshot or DB key schema differs, stop before caption indexing.
Do not repair the mismatch by silently falling back to a mutable clone.

This is not a requirement to scan every old report on every task. Apply it when
a report is being edited, cited as current evidence, or naturally discovered to
be stale during the current work.

When a generated TSV/CSV/JSON source artifact changes, any Markdown document
that is being updated or cited as its current summary must be checked against
the source artifact before reporting or editing.

For source-label candidate reports:

- use the current TSV columns as the source of truth, such as
  `selection_status`, `selected_oewn_synset`, `manual_decision`,
  `synset_selection_tag`, and `mwe_candidate_status`
- do not infer current `selected`, `ambiguous`, `rejected`, or `unresolved`
  counts from an older Markdown summary
- if a report was generated from an older inventory snapshot, mark it as
  historical and state the snapshot scope
- do not call a snapshot report "current" after new source datasets have been
  added unless it has been regenerated from the new source artifact
- historical decision logs may keep their original numbers, but a current probe
  report that is touched or cited must either match the latest source artifact
  or clearly state that it is a historical snapshot

When updating only the explanatory Markdown around a generated artifact, use
`apply_patch`. Do not edit large generated TSV/CSV artifacts by hand.

### Formal Pipeline And Inventory Reuse

Do not treat preview/debug outputs as normal pipeline outputs.

Stage 5/6 outputs are formal caption-to-concept results only when all required
inventory gates for that run have passed. If a command uses a preview or debug
escape hatch, such as `--allow-unresolved-attribute-preview`, the output path,
summary, report, and user-facing answer must call it preview/debug output. Do
not continue from that output as if it were a formal pipeline artifact.

Do not rebuild a GPIC observed inventory in isolation when a resolved prior
inventory for the same concept family exists. The stable prior is
`resources/gpic_inventory/current/inventory_bundle.json`, not an ad hoc
`outputs/...` snapshot.

For production Stage 3.5 inventory expansion:

- always load inventory inputs from the current bundle
- run `scripts/run_stage35_inventory_workflow.py` with `--use-current-inventory`
- do not manually combine `--object-inventory`, `--attribute-prior-inventory`,
  `--action-prior-inventory`, or `--base-lexicon-dir`
- if a completed component must be promoted before the full workflow is
  complete, use `scripts/publish_current_inventory_component.py` so
  `resources/gpic_inventory/current` is updated component-wise
- never call an `outputs/...` TSV "current" unless it has been published into
  `resources/gpic_inventory/current`
- after generation, report how many rows were reused versus newly queued when
  the script exposes those counts

This applies across caption shapes. A tag-list inventory is not a separate
semantic namespace from sentence inventory; the same `span_key`/surface policy
must reuse the same resolved evidence unless the rule document explicitly says
otherwise.

Do not infer that a previous object-inventory workflow automatically carried
over to attributes or actions. Check the actual command arguments before
running the build.

Keep inventory gates phase-specific. If the current phase is synset/manual
resolution, report only unresolved manual/synset rows as blockers. Do not mix
canonical-surface blockers into that report. Run and report the canonical gate
only after the synset/manual gate is clear.

### Encoding Hygiene

Do not trust a mojibake-looking PowerShell display as evidence that a file is
actually corrupted.

For Korean or mixed Korean/English Markdown and TSV files:

1. Prefer Python `Path.read_text(encoding="utf-8", errors="strict")` or another
   explicit UTF-8 reader when verifying file content.
2. Do not use `Get-Content` without `-Encoding UTF8` for Korean text.
3. Do not copy patch context from mojibake terminal output. If patch context is
   needed, obtain it from an explicit UTF-8 read or ASCII-safe `repr()`.
4. If text looks corrupted in command output, verify the file bytes/content with
   UTF-8 strict reading before claiming the file itself is corrupted.
5. Scripts that write Markdown, JSON, CSV, or TSV must specify
   `encoding="utf-8"` and, for CSV/TSV, `newline=""`.
6. PowerShell output is allowed for quick inspection, but it is not sufficient
   evidence for Korean file integrity unless UTF-8 decoding has been verified.

## Karpathy Guidelines

These behavioral guidelines reduce common LLM coding mistakes. They bias toward
caution over speed; for trivial tasks, use judgment.

### 1. Think Before Coding

Do not assume. Do not hide confusion. Surface tradeoffs.

Before implementing:

- state assumptions explicitly
- if uncertain, ask
- if multiple interpretations exist, present them rather than picking silently
- if a simpler approach exists, say so
- push back when warranted
- if something is unclear, stop, name what is confusing, and ask

### 2. Simplicity First

Write the minimum code that solves the problem. Do nothing speculative.

- no features beyond what was asked
- no abstractions for single-use code
- no flexibility or configurability that was not requested
- no error handling for impossible scenarios
- if 200 lines could be 50, rewrite it
- ask whether a senior engineer would call the solution overcomplicated; if yes,
  simplify it

### 3. Surgical Changes

Touch only what is necessary. Clean up only the mess created by the current
change.

When editing existing code:

- do not improve adjacent code, comments, or formatting
- do not refactor things that are not broken
- match existing style, even if a different style would be preferable
- if unrelated dead code is noticed, mention it rather than deleting it

When the current change creates orphans:

- remove imports, variables, or functions made unused by the current change
- do not remove pre-existing dead code unless asked

Every changed line must trace directly to the user's request.

### 4. Goal-Driven Execution

Define success criteria and loop until verified.

Transform tasks into verifiable goals:

- "Add validation" means write tests for invalid inputs, then make them pass.
- "Fix the bug" means write a test that reproduces it, then make it pass.
- "Refactor X" means ensure tests pass before and after.

For multi-step tasks, state a brief plan:

1. Step -> verify: check.
2. Step -> verify: check.
3. Step -> verify: check.

Strong success criteria allow independent progress. Weak criteria, such as
"make it work", require clarification.

## Evidence-Gated Answer Protocol

Follow `docs/answer_protocol.md` for any non-trivial explanation, especially
claims about current code behavior, runtime environment, GPU/CPU status,
benchmark speed, rule behavior, or generated outputs.

Do not present an inference as a verified fact.

When making a claim about the current repository or environment, separate:

- verified observation
- code/file evidence
- inference
- unknown or unchecked state

For code behavior:

- inspect the relevant file before explaining the implemented behavior
- cite the file path or function name in the answer
- say "not checked in this turn" if the file was not inspected

For runtime and benchmark claims:

- state the exact command or output file used as evidence
- state model, input size, batch size, stage range, intermediate-file policy,
  GPU/CPU status, and environment scope
- never infer hardware capability from one library's runtime status
- separate physical GPU availability, PyTorch CUDA availability, spaCy GPU/CuPy
  availability, and the actual device used in the latest run

Forbidden unless independently verified in the current turn:

- "this machine cannot use GPU"
- "only CPU is available"
- "the code currently does X" without reading the relevant code
- "the benchmark speed is X" without the command, input size, and stage range
