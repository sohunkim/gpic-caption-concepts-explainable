"""Small real-CUDA 1 -> 2 -> 1 restart smoke, isolated from production runs."""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from run_fixed_lexicon_scaleout import (
    ROOT, InputShard, _atomic_json, _sha256_file, inventory_bundle_fingerprint,
    load_input_manifest, source_revision, verify_input_shards,
)
from incident_gate import guarded_entrypoint
from planned_pause import request_pause
from run_t5_lexical_followup import child_environment
from verify_fixed_lexicon_retention_smoke import verify


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def prepare_input(source: Path, destination: Path, *, rows: int) -> Path:
    _, shards = load_input_manifest(source)
    if rows < 4 or rows > 1000 or rows % 4 or sum(s.rows for s in shards) != rows:
        raise ValueError("smoke input must have exactly --rows (4..1000, divisible by 4)")
    verify_input_shards(shards, verify_hashes=True)
    lines: list[bytes] = []
    ids: list[str] = []
    for shard in shards:
        content = Path(shard.path).read_bytes().splitlines(keepends=True)
        if len(content) != shard.rows:
            raise ValueError("input row count differs from manifest")
        for line in content:
            row = json.loads(line)
            caption_id = str(row.get("caption_id") or "")
            if not caption_id:
                raise ValueError("smoke row has no caption_id")
            ids.append(caption_id)
            lines.append(line.rstrip(b"\r\n") + b"\n")
    if len(set(ids)) != rows:
        raise ValueError("smoke caption IDs are not unique")
    destination.mkdir(parents=True, exist_ok=False)
    prepared = []
    for index in range(4):
        path = destination / f"shard_{index:06d}.jsonl"
        path.write_bytes(b"".join(lines[index * (rows // 4):(index + 1) * (rows // 4)]))
        prepared.append(asdict(InputShard(
            f"shard_{index:06d}", str(path.resolve()), rows // 4,
            path.stat().st_size, _sha256_file(path))))
    manifest = destination / "manifest.json"
    _atomic_json(manifest, {
        "kind": "gpic-caption-shards-v1", "rows": rows, "shards": prepared,
        "source_manifest": str(source), "source_sha256": _sha256_file(source),
        "id_sequence_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest(),
    })
    return manifest


def descendants(root: int, parents: dict[int, int]) -> set[int]:
    result = {root}
    while True:
        extra = {pid for pid, parent in parents.items() if parent in result} - result
        if not extra:
            return result
        result.update(extra)


def command_output(argv: list[str]) -> str:
    return subprocess.run(argv, check=True, capture_output=True, text=True,
                          timeout=10).stdout


def gpu_snapshot() -> list[dict]:
    fields = ["index", "uuid", "name", "driver_version", "memory.total",
              "memory.used", "utilization.gpu", "power.limit", "power.draw", "pstate"]
    output = command_output(["nvidia-smi", "--query-gpu=" + ",".join(fields),
                             "--format=csv,noheader,nounits"])
    return [dict(zip(fields, [v.strip() for v in row])) for row in csv.reader(output.splitlines())]


def own_gpu_processes(pid: int) -> list[dict]:
    output = command_output(["ps", "-eo", "pid=,ppid="])
    parents = {int(parts[0]): int(parts[1]) for line in output.splitlines()
               if len(parts := line.split()) == 2}
    own = descendants(pid, parents)
    gpu_output = command_output([
        "nvidia-smi", "--query-compute-apps=pid,gpu_uuid,used_gpu_memory",
        "--format=csv,noheader,nounits"])
    return [{"pid": int(row[0]), "gpu_uuid": row[1].strip(),
             "used_gpu_memory_mib": row[2].strip()}
            for row in csv.reader(gpu_output.splitlines()) if int(row[0]) in own]


def receipt_snapshot(root: Path) -> dict[str, str]:
    paths = list((root / "receipts").glob("unit_*.json"))
    for path in list(paths):
        for artifact in read_json(path)["artifacts"]:
            target = (root / artifact["path"]).resolve()
            if root.resolve() not in target.parents:
                raise ValueError("receipt artifact escapes test run")
            if _sha256_file(target) != artifact["sha256"]:
                raise ValueError("retained artifact SHA mismatch")
            paths.append(target)
    return {str(path.relative_to(root)): _sha256_file(path) for path in paths}


def assert_reused(root: Path, snapshot: dict[str, str]) -> None:
    if any(not (root / name).is_file() or _sha256_file(root / name) != sha
           for name, sha in snapshot.items()):
        raise ValueError("resume rewrote a completed receipt or retained artifact")


def stop_test_group(process: subprocess.Popen) -> None:
    # Only the session created for this smoke attempt, never production PIDs.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=10)


def run_attempt(args, manifest: Path, root: Path, *, name: str, gpus: list[str],
                uuid_by_gpu: dict[str, str], resume: bool, pause: bool,
                expected_receipts: int) -> dict:
    argv = [sys.executable, "-B", str(ROOT / "scripts/run_fixed_lexicon_scaleout.py"),
            "--input-manifest", str(manifest), "--output-root", str(root),
            "--inventory-bundle", str(args.inventory_bundle),
            "--preposition-mwe-lexicon", str(args.preposition_mwe_lexicon),
            "--gpus", ",".join(gpus), "--input-shards-per-unit", "1",
            "--model", "en_core_web_trf", "--batch-size", "192",
            "--stage3-shards-per-gpu", "2", "--stage456-shards-per-worker", "2",
            "--stage456-jobs-per-worker", "1", "--stage456-merge-jobs", "1",
            "--global-merge-jobs", "1", "--stage6-count-backend", "sqlite",
            "--retention-policy", "canonical_counts", "--verify-completed-hashes",
            "--heartbeat-seconds", "5", "--progress-interval-records", "10"]
    if resume:
        argv.append("--resume")
    log_path = args.output_root / f"{name}.log"
    before = receipt_snapshot(root)
    evidence = {"name": name, "argv": argv, "gpu_ids": gpus,
                "gpu_processes": {}, "pause_request": None, "reused_files": len(before)}
    expected_uuids = {uuid_by_gpu[g] for g in gpus}
    start = time.monotonic()
    last_report = 0.0
    with log_path.open("wb", buffering=0) as log:
        process = subprocess.Popen(argv, cwd=ROOT, env=child_environment(vars(args)),
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True)
        evidence["pid"] = process.pid
        try:
            while process.poll() is None:
                if time.monotonic() - start > args.attempt_timeout_seconds:
                    raise TimeoutError("bounded GPU smoke deadline exceeded; production unaffected")
                observations = own_gpu_processes(process.pid)
                current_uuids = {row["gpu_uuid"] for row in observations}
                if current_uuids - expected_uuids:
                    raise ValueError("test process used an unrequested GPU")
                for row in observations:
                    evidence["gpu_processes"][str(row["pid"])] = row
                if pause and evidence["pause_request"] is None and current_uuids == expected_uuids:
                    evidence["pause_request"] = request_pause(root)
                    print(json.dumps({"attempt": name, "event": "pause_requested",
                                      "observed_gpu_uuids": sorted(current_uuids)}), flush=True)
                if time.monotonic() - last_report >= 15:
                    progress = read_json(root / "progress.json") if (root / "progress.json").exists() else {}
                    status = {"attempt": name, "elapsed_seconds": round(time.monotonic() - start, 1),
                              "pipeline": progress, "gpu": gpu_snapshot()}
                    _atomic_json(args.output_root / "progress.json", status)
                    _atomic_json(args.output_root / f"{name}.evidence.json", evidence)
                    print(json.dumps({"attempt": name, "seconds": status["elapsed_seconds"],
                                      "status": progress.get("status"),
                                      "receipts": len(list((root / "receipts").glob("unit_*.json")))}), flush=True)
                    last_report = time.monotonic()
                time.sleep(1)
            if process.returncode != 0:
                raise RuntimeError(f"GPU smoke child exited {process.returncode}; see {log_path}")
            if {v["gpu_uuid"] for v in evidence["gpu_processes"].values()} != expected_uuids:
                raise ValueError("missing real CUDA process evidence for a requested GPU")
            state = read_json(root / "execution_control.json")
            if state["status"] != ("paused" if pause else "completed"):
                raise ValueError(f"unexpected attempt status: {state}")
            if (root / "COMPLETE.json").exists() == pause:
                raise ValueError("COMPLETE marker disagrees with planned pause")
            receipts = list((root / "receipts").glob("unit_*.json"))
            if len(receipts) != expected_receipts:
                raise ValueError(f"expected {expected_receipts} drained units, got {len(receipts)}")
            assert_reused(root, before)
            receipt_snapshot(root)
            evidence.update(status=state["status"], attempt=state["attempt"],
                            elapsed_seconds=time.monotonic() - start, receipts=len(receipts))
        except BaseException as exc:
            stop_test_group(process)
            evidence.update(status="failed", error=str(exc))
            raise
        finally:
            _atomic_json(args.output_root / f"{name}.evidence.json", evidence)
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-manifest", type=Path, required=True)
    parser.add_argument("--inventory-bundle", type=Path, required=True)
    parser.add_argument("--preposition-mwe-lexicon", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--gpus", default="0,1")
    parser.add_argument("--rows", type=int, default=100)
    parser.add_argument("--attempt-timeout-seconds", type=int, default=900)
    parser.add_argument("--cuda-library-root")
    args = parser.parse_args()
    if os.name != "posix":
        raise ValueError("real GPU restart smoke requires Linux")
    gpus = args.gpus.split(",")
    if len(gpus) != 2 or len(set(gpus)) != 2 or args.attempt_timeout_seconds < 1:
        raise ValueError("provide exactly two distinct GPU indices and a positive smoke timeout")
    hardware = gpu_snapshot()
    uuid_by_gpu = {row["index"]: row["uuid"] for row in hardware}
    if not set(gpus) <= set(uuid_by_gpu):
        raise ValueError("requested GPUs are not visible")
    args.output_root = args.output_root.resolve()
    args.output_root.mkdir(parents=True, exist_ok=False)
    manifest = prepare_input(args.input_manifest, args.output_root / "input", rows=args.rows)
    conditions = {"kind": "gpic-real-gpu-restart-smoke-v1", "code_commit": source_revision(ROOT),
                  "input": read_json(manifest), "hardware": hardware,
                  "inventory": inventory_bundle_fingerprint(args.inventory_bundle),
                  "scope": "100-caption default smoke; not a throughput benchmark or live GPU resize"}
    _atomic_json(args.output_root / "conditions.json", conditions)
    baseline, candidate = args.output_root / "baseline", args.output_root / "resumed"
    attempts = []
    for name, root, selected, resume, pause, expected in [
        ("one_gpu_pause", candidate, gpus[:1], False, True, 1),
        ("two_gpu_resume_pause", candidate, gpus, True, True, 3),
        ("one_gpu_resume_finish", candidate, gpus[:1], True, False, 4),
        ("two_gpu_uninterrupted", baseline, gpus, False, False, 4),
    ]:
        attempts.append(run_attempt(args, manifest, root, name=name, gpus=selected,
                                    uuid_by_gpu=uuid_by_gpu, resume=resume, pause=pause,
                                    expected_receipts=expected))
    baseline_complete, candidate_complete = read_json(baseline / "COMPLETE.json"), read_json(candidate / "COMPLETE.json")
    if (baseline_complete["identity_sha256"] != candidate_complete["identity_sha256"]
            or baseline_complete["input_rows"] != args.rows or candidate_complete["input_rows"] != args.rows):
        raise ValueError("run identity or population differs")
    result = verify(baseline, candidate, retention_policy="canonical_counts")
    result.update(attempts=attempts, hardware_after=gpu_snapshot(),
                  code_commit=conditions["code_commit"], rows=args.rows,
                  identity_sha256=candidate_complete["identity_sha256"])
    _atomic_json(args.output_root / "verification.json", result)
    print(json.dumps({"status": "ok", "rows": args.rows,
                      "verification": str(args.output_root / "verification.json")}), flush=True)


if __name__ == "__main__":
    raise SystemExit(guarded_entrypoint("fixed_lexicon_real_gpu_restart_smoke", main))
