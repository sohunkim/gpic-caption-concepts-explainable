"""Receipt-gated, unattended T5 -> fixed-lexicon handoff (no inference changes)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
from incident_gate import guarded_entrypoint
from copy_verified_files import discard_cached_pages
from planned_pause import PauseControl, STATE_FILE, request_pause

KIND = "gpic-t5-lexical-followup-v1"


class PlannedPause(Exception):
    """A cooperative stop, not a failed child or a forced termination."""


def execution_steps(config: dict[str, Any], *, gpus: str | None, resume: bool) -> list[dict]:
    steps = [{**step, "argv": list(step.get("argv", []))} for step in config["steps"]]
    if gpus is not None and gpus != "auto":
        values = [value.strip() for value in gpus.split(",")]
        if not all(values) or len(set(values)) != len(values):
            raise ValueError("GPU override requires unique non-empty selectors or auto")
    for step in steps:
        if step["name"] not in {"lexical_smoke", "verify_lexical_resume", "lexical_formal"}:
            continue
        argv = step["argv"]
        if gpus is not None:
            if argv.count("--gpus") != 1 or argv.index("--gpus") + 1 == len(argv):
                raise ValueError("expected exactly one GPU option in lexical command")
            argv[argv.index("--gpus") + 1] = gpus
        if resume and "--resume" not in argv:
            argv.append("--resume")
    return steps


def input_shards(path: Path) -> list[dict[str, Any]]:
    # Match the producer's InputShard contract, not optional population metadata.
    shards = []
    for row in read_json(path)["shards"]:
        source = Path(row["path"])
        if not source.is_absolute():
            source = (path.parent / source).resolve()
        shards.append({"shard_id": row["shard_id"], "path": str(source),
                       "rows": int(row["rows"]), "size_bytes": int(row["size_bytes"]),
                       "sha256": row["sha256"]})
    return shards


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def digest_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def checked(command: list[str], *, cwd: Path | None = None) -> str:
    return subprocess.run(command, cwd=cwd, check=True, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=30).stdout.strip()


def verify_pins(config: dict[str, Any]) -> None:
    for item in config["pinned_files"]:
        path = Path(item["path"])
        if path.stat().st_size != item["size_bytes"] or digest_file(path) != item["sha256"]:
            raise ValueError(f"pinned file changed: {path}")
    if checked(["git", "rev-parse", "HEAD"], cwd=ROOT) != config["lexical_commit"]:
        raise ValueError("lexical checkout revision changed")
    if checked(["git", "status", "--porcelain"], cwd=ROOT):
        raise ValueError("lexical checkout is dirty")


def t5_state(config: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    root = Path(config["t5_root"])
    if (root / "incident.json").exists():
        raise RuntimeError("T5 has an unresolved incident; followup blocked")
    progress = read_json(root / "progress.json")
    parent = read_json(Path(config["t5_status"]))
    if progress.get("state") in {"failed", "interrupted"} or parent.get("state") in {
        "failed", "interrupted"
    }:
        raise RuntimeError("T5 failed or was interrupted; followup blocked")
    complete = root / "COMPLETE.json"
    if complete.exists() and parent.get("state") == "complete":
        if progress.get("completed_shards") != progress.get("total_shards"):
            raise ValueError("T5 completion disagrees with progress")
        return True, progress
    timestamp = datetime.fromisoformat(progress["updated_at"])
    age = (datetime.now(timezone.utc) - timestamp).total_seconds()
    if age > config["heartbeat_stale_seconds"]:
        raise RuntimeError(f"T5 heartbeat stale ({age:.0f}s); T5 itself was not stopped")
    checked(["tmux", "has-session", "-t", config["t5_session"]])
    return False, progress


def scan_jsonl(path: Path) -> dict[str, Any]:
    """Bounded memory; no caption text or IDs are logged or transferred."""
    content = hashlib.sha256()
    ids = hashlib.sha256()
    rows = 0
    with path.open("rb") as handle:
        for line in handle:
            content.update(line)
            if not line.strip():
                continue
            row = json.loads(line)
            identifier = str(row.get("id") or "")
            if not identifier:
                raise ValueError(f"row without id: {path}, line {rows + 1}")
            ids.update(identifier.encode("utf-8") + b"\n")
            rows += 1
        discard_cached_pages(handle)
    return {"rows": rows, "sha256": content.hexdigest(),
            "id_sequence_sha256": ids.hexdigest(), "size_bytes": path.stat().st_size}


def verify_t5(config: dict[str, Any], report) -> dict[str, Any]:
    root = Path(config["t5_root"]).resolve()
    manifest = read_json(root / "run_manifest.json")
    complete = read_json(root / "COMPLETE.json")
    expected_identity = config["t5_identity_sha256"]
    if manifest.get("identity_sha256") != expected_identity:
        raise ValueError("unexpected T5 run identity")
    if any(complete.get(key) != value for key, value in manifest.items()):
        raise ValueError("T5 COMPLETE does not match its run manifest")
    shards = manifest["shards"]
    if input_shards(Path(config["input_manifest"])) != shards:
        raise ValueError("T5 and lexical do not have the same ordered input shards")
    if digest_file(Path(config["input_manifest"])) != manifest["input_manifest_sha256"]:
        raise ValueError("input manifest hash does not match T5")
    ids = [item["shard_id"] for item in shards]
    if len(set(ids)) != len(ids) or not ids:
        raise ValueError("empty or duplicate T5 shard list")
    expected_names = {f"{item}.json" for item in ids}
    actual_names = {path.name for path in (root / "receipts").glob("*.json")}
    if actual_names != expected_names:
        raise ValueError("T5 receipt set differs from its immutable input list")

    grouping_hash = hashlib.sha256()
    totals = dict.fromkeys(("outer_group_count", "sentence_group_count",
                           "outer_item_count", "sentence_item_count"), 0)
    rows = 0
    for number, shard in enumerate(shards, 1):
        name = shard["shard_id"]
        receipt = read_json(root / "receipts" / f"{name}.json")
        if (receipt.get("kind") != "gpic-factual-scene-graph-shard-receipt-v3"
                or receipt.get("shard_id") != name
                or receipt.get("run_identity_sha256") != expected_identity
                or receipt.get("input") != shard):
            raise ValueError(f"T5 receipt identity/input mismatch: {name}")
        for field in ("runtime_batching", "runtime_sentence_batching"):
            if receipt.get(field, {}).get("oom_splits") != 0:
                raise ValueError(f"T5 adaptive/OOM split in {name}")
        output = receipt["output"]
        output_path = Path(output["path"]).resolve()
        if output_path != root / "shards" / name / "canonical.jsonl":
            raise ValueError(f"unexpected T5 output path: {name}")
        report("verifying_t5", shard=name, verified_shards=number - 1,
               total_shards=len(shards), verified_rows=rows)
        observed_input = scan_jsonl(Path(shard["path"]))
        observed_output = scan_jsonl(output_path)
        for field in ("rows", "size_bytes", "sha256"):
            if observed_input[field] != shard[field] or observed_output[field] != output[field]:
                raise ValueError(f"T5 {field} mismatch: {name}")
        if (observed_output["rows"] != shard["rows"]
                or observed_input["id_sequence_sha256"] != receipt["input_id_sequence_sha256"]
                or observed_output["id_sequence_sha256"] != output["id_sequence_sha256"]
                or observed_output["id_sequence_sha256"] != observed_input["id_sequence_sha256"]):
            raise ValueError(f"T5 caption count/order mismatch: {name}")
        grouping = receipt["grouping_fingerprints"]
        if (grouping.get("schema") != manifest["grouping_fingerprint_schema"]
                or grouping.get("outer_item_count") != shard["rows"]):
            raise ValueError(f"T5 grouping evidence mismatch: {name}")
        for field in ("outer_group_plan_sha256", "sentence_group_plan_sha256",
                      "model_input_plan_sha256"):
            if len(bytes.fromhex(grouping[field])) != 32:
                raise ValueError(f"T5 invalid grouping hash: {name}")
        encoded = json.dumps({"shard_id": name, "grouping_fingerprints": grouping},
                             sort_keys=True, separators=(",", ":")).encode("utf-8")
        grouping_hash.update(len(encoded).to_bytes(8, "big"))
        grouping_hash.update(encoded)
        for field in totals:
            totals[field] += grouping[field]
        rows += shard["rows"]
    rollup = {"schema": manifest["grouping_fingerprint_schema"],
              "ordered_shard_grouping_sha256": grouping_hash.hexdigest(),
              "shard_count": len(shards), **totals}
    if complete.get("grouping_fingerprint_rollup") != rollup:
        raise ValueError("T5 completion grouping rollup mismatch")
    if rows != config["expected_rows"]:
        raise ValueError("T5 population row total mismatch")
    return {"status": "verified", "rows": rows, "shards": len(shards),
            "t5_identity_sha256": expected_identity, "grouping_fingerprint_rollup": rollup}


def child_environment(config: dict[str, Any]) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    library_root = config.get("cuda_library_root")
    if library_root:
        libraries = sorted(str(path) for path in Path(library_root).glob("*/lib"))
        if not libraries:
            raise ValueError("configured CUDA library directory is empty")
        env["LD_LIBRARY_PATH"] = os.pathsep.join(libraries + [env.get("LD_LIBRARY_PATH", "")])
    return env


def run_step(step: dict[str, Any], config: dict[str, Any], report,
             pause: PauseControl | None = None) -> None:
    log_path = Path(config["queue_root"]) / "logs" / f"{step['name']}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab", buffering=0) as log:
        child_control = None
        if step["name"] in {"lexical_smoke", "verify_lexical_resume", "lexical_formal"}:
            argv = step["argv"]
            child_control = Path(argv[argv.index("--output-root") + 1])
        process = subprocess.Popen(step["argv"], cwd=ROOT, env=child_environment(config),
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=os.name != "nt")
        try:
            while process.poll() is None:
                draining = pause is not None and pause.requested()
                if draining:
                    pause.finish("draining")
                    if child_control is not None and (child_control / STATE_FILE).exists():
                        child_state = read_json(child_control / STATE_FILE)
                        if (child_state.get("pid") == process.pid
                                and child_state["status"] in {"running", "draining", "paused"}):
                            request_pause(child_control, expected_attempt=child_state["attempt"])
                report("draining" if draining else step["name"],
                       step=step["name"], child_pid=process.pid, log=str(log_path),
                       child_progress=step.get("progress"))
                time.sleep(config["poll_seconds"])
            if process.returncode != 0:
                raise RuntimeError(f"{step['name']} exited {process.returncode}; see {log_path}")
            if child_control is not None and (child_control / STATE_FILE).exists():
                child_state = read_json(child_control / STATE_FILE)
                if child_state.get("pid") == process.pid and child_state["status"] == "paused":
                    raise PlannedPause()
        except BaseException:
            if process.poll() is None:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    if os.name != "nt":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait(timeout=15)
            raise


def run(config_path: Path, *, gpus: str | None = None, resume: bool = False) -> None:
    config = read_json(config_path)
    if config.get("kind") != KIND:
        raise ValueError("unsupported followup config")
    root = Path(config["queue_root"])
    steps = execution_steps(config, gpus=gpus, resume=resume)
    pause = PauseControl.start(root, digest_file(config_path), resume=resume)
    started = datetime.now(timezone.utc).isoformat()

    def report(state: str, **details) -> None:
        value = {"kind": KIND, "state": state, "pid": os.getpid(),
                 "started_at": started, "updated_at": datetime.now(timezone.utc).isoformat(),
                 "config_sha256": digest_file(config_path), "attempt": pause.attempt,
                 "gpu_override": gpus, "resume": resume, **details}
        write_json(root / "status.json", value)
        with (root / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, sort_keys=True) + "\n")
        print(json.dumps(value, sort_keys=True), flush=True)

    def check_pause() -> None:
        if pause.requested():
            raise PlannedPause()

    def verification_progress(state, **details):
        check_pause()
        report(state, **details)

    try:
        verify_pins(config)
        while True:
            check_pause()
            complete, progress = t5_state(config)
            if complete:
                break
            report("waiting_for_t5", completed_shards=progress["completed_shards"],
                   total_shards=progress["total_shards"], t5_progress=progress["updated_at"])
            time.sleep(config["poll_seconds"])
        result = verify_t5(config, verification_progress)
        write_json(root / "t5_verification.json", result)
        verify_pins(config)
        for step in steps:
            check_pause()
            run_step(step, config, report, pause=pause)
        result = read_json(Path(config["lexical_output"]) / "COMPLETE.json")
        if result.get("status") != "completed" or result.get("input_rows") != config["expected_rows"]:
            raise ValueError("lexical COMPLETE population/status mismatch")
        verify_pins(config)
        pause.finish("completed")
        report("completed", rows=result["input_rows"], lexical_output=config["lexical_output"])
        write_json(root / "COMPLETE.json", read_json(root / "status.json"))
    except PlannedPause:
        pause.finish("paused")
        report("paused")
    except BaseException as exc:
        pause.finish("failed")
        report("failed", error=f"{type(exc).__name__}: {exc}")
        raise


def prepare(args: argparse.Namespace) -> Path:
    queue_root = Path(args.queue_root).resolve()
    if queue_root.exists():
        raise ValueError("refusing to replace an existing followup config")
    t5_root = Path(args.t5_root).resolve()
    manifest = read_json(t5_root / "run_manifest.json")
    if manifest["identity_sha256"] != args.t5_identity:
        raise ValueError("T5 identity does not match explicit requested run")
    input_path = Path(args.input_manifest).resolve()
    if digest_file(input_path) != manifest["input_manifest_sha256"]:
        raise ValueError("lexical input manifest must be identical to T5 input")
    if input_shards(input_path) != manifest["shards"]:
        raise ValueError("input shard list differs from T5")
    bundle = ROOT / "resources/gpic_inventory/current/inventory_bundle.json"
    prep = ROOT / "resources/lexicons/preposition_mwes.tsv"
    pins = [input_path, t5_root / "run_manifest.json", Path(args.smoke_manifest).resolve(), prep]
    pins.extend(path for path in bundle.parent.rglob("*") if path.is_file())
    pins.extend((Path(args.smoke_baseline) / "stage6").glob("*.tsv"))
    pins.extend((Path(args.smoke_baseline) / "units").glob(
        "unit_*/stage456_sharded/shards/shard_*/stage5/canonical_*.jsonl"))
    smoke_out = Path(args.smoke_output).resolve()
    lexical_out = Path(args.lexical_output).resolve()
    if smoke_out.exists() or lexical_out.exists():
        raise ValueError("new followup requires fresh smoke and lexical output directories")
    # Resolving a venv's python symlink would silently select the system environment.
    common = [str(Path(args.python).absolute()), "-B", str(ROOT / "scripts/run_fixed_lexicon_scaleout.py"),
              "--inventory-bundle", str(bundle), "--preposition-mwe-lexicon", str(prep),
              "--gpus", args.gpus, "--batch-size", "192", "--stage6-count-backend", "sqlite",
              "--retention-policy", "canonical_counts", "--verify-completed-hashes"]
    smoke = [*common, "--input-manifest", str(Path(args.smoke_manifest).resolve()),
             "--output-root", str(smoke_out), "--input-shards-per-unit", "1"]
    steps = [
        {"name": "lexical_smoke", "argv": smoke, "progress": str(smoke_out / "progress.json")},
        {"name": "verify_lexical_smoke", "argv": [args.python, "-B",
            str(ROOT / "scripts/verify_fixed_lexicon_retention_smoke.py"),
            "--baseline", str(Path(args.smoke_baseline).resolve()), "--candidate", str(smoke_out),
            "--output", str(queue_root / "lexical_smoke_verification.json")]},
        {"name": "verify_lexical_resume", "argv": smoke},
        {"name": "lexical_formal", "argv": [*common, "--input-manifest", str(input_path),
            "--output-root", str(lexical_out), "--input-shards-per-unit", "10"],
            "progress": str(lexical_out / "progress.json")},
    ]
    config = {"kind": KIND, "queue_root": str(queue_root), "t5_root": str(t5_root),
              "t5_status": str(Path(args.t5_status).resolve()), "t5_session": args.t5_session,
              "t5_identity_sha256": args.t5_identity, "input_manifest": str(input_path),
              "expected_rows": sum(shard["rows"] for shard in manifest["shards"]),
              "lexical_output": str(lexical_out), "lexical_commit": checked(["git", "rev-parse", "HEAD"], cwd=ROOT),
              "poll_seconds": 30, "heartbeat_stale_seconds": 300,
              "cuda_library_root": args.cuda_library_root, "steps": steps,
              "pinned_files": [{"path": str(path), "size_bytes": path.stat().st_size,
                                "sha256": digest_file(path)} for path in sorted(set(pins))]}
    verify_pins(config)
    path = queue_root / "config.json"
    write_json(path, config)
    write_json(queue_root / "status.json", {"kind": KIND, "state": "prepared"})
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    setup = commands.add_parser("prepare")
    for name in ("queue-root", "t5-root", "t5-status", "t5-session", "t5-identity",
                 "input-manifest", "python", "smoke-manifest", "smoke-baseline",
                 "smoke-output", "lexical-output"):
        setup.add_argument(f"--{name}", required=True)
    setup.add_argument("--gpus", default="auto")
    setup.add_argument("--cuda-library-root")
    execute = commands.add_parser("run")
    execute.add_argument("--config", type=Path, required=True)
    execute.add_argument("--gpus", help="Restart-only GPU override; leaves the pinned config unchanged.")
    execute.add_argument("--resume", action="store_true")
    stop_parser = commands.add_parser("pause")
    stop_parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "prepare":
        print(prepare(args))
    elif args.mode == "pause":
        config = read_json(args.config)
        control_root = Path(config["queue_root"])
        state = read_json(control_root / STATE_FILE)
        if state["identity"] != digest_file(args.config):
            raise ValueError("pause config does not match the running followup")
        print(json.dumps(request_pause(control_root), sort_keys=True))
    else:
        def stop(signum, frame):
            raise KeyboardInterrupt(f"received signal {signum}")
        signal.signal(signal.SIGTERM, stop)
        raise SystemExit(guarded_entrypoint("t5_lexical_followup", lambda: run(
            args.config, gpus=args.gpus, resume=args.resume)))


if __name__ == "__main__":
    main()
