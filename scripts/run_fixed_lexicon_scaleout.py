from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from incident_gate import guarded_entrypoint
from gpic_concepts_v1.atomic_io import atomic_text_writer
from gpic_concepts_v1.inventory_bundle import load_inventory_bundle
from gpic_concepts_v1.stage3_annotate import (
    DEFAULT_STAGE3_BATCH_SIZE,
    DEFAULT_STAGE3_DISABLED_COMPONENTS,
    DEFAULT_STAGE3_MODEL,
)
from run_stage456_sharded import merge_stage6_count_dirs


RUN_KIND = "gpic-fixed-lexicon-scaleout-v1"
RECEIPT_KIND = "gpic-fixed-lexicon-unit-receipt-v2"
RETENTION_POLICIES = ("full", "canonical_counts")


@dataclass(frozen=True)
class InputShard:
    shard_id: str
    path: str
    rows: int
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class WorkUnit:
    unit_id: str
    shards: tuple[InputShard, ...]
    rows: int


@dataclass(frozen=True)
class WorkerSettings:
    output_root: str
    run_identity_sha256: str
    inventory_bundle: str
    object_inventory: str
    attribute_inventory: str
    action_inventory: str
    lexicon_dir: str
    preposition_mwe_lexicon: str
    model: str
    batch_size: int
    stage3_shards_per_gpu: int
    stage456_shards_per_worker: int
    stage456_jobs_per_worker: int
    stage456_merge_jobs: int
    stage6_count_backend: str
    progress_interval_records: int
    retention_policy: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_text_writer(path) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def load_input_manifest(path: Path) -> tuple[dict[str, Any], list[InputShard]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("kind") != "gpic-caption-shards-v1":
        raise ValueError(f"unsupported input manifest kind: {payload.get('kind')!r}")
    raw_shards = payload.get("shards")
    if not isinstance(raw_shards, list) or not raw_shards:
        raise ValueError("input manifest must contain a non-empty shards list")

    shards: list[InputShard] = []
    seen: set[str] = set()
    for raw in raw_shards:
        shard_id = str(raw.get("shard_id") or "")
        if not shard_id or shard_id in seen:
            raise ValueError(f"input shard IDs must be non-empty and unique: {shard_id!r}")
        seen.add(shard_id)
        source = Path(str(raw.get("path") or ""))
        if not source.is_absolute():
            source = (path.parent / source).resolve()
        shard = InputShard(
            shard_id=shard_id,
            path=str(source),
            rows=int(raw.get("rows") or 0),
            size_bytes=int(raw.get("size_bytes") or 0),
            sha256=str(raw.get("sha256") or ""),
        )
        if shard.rows <= 0 or shard.size_bytes <= 0 or len(shard.sha256) != 64:
            raise ValueError(f"input shard lacks rows, size, or SHA256: {shard_id}")
        shards.append(shard)
    return payload, shards


def build_work_units(shards: list[InputShard], shards_per_unit: int) -> list[WorkUnit]:
    if shards_per_unit < 1:
        raise ValueError("shards_per_unit must be greater than zero")
    units: list[WorkUnit] = []
    for offset in range(0, len(shards), shards_per_unit):
        unit_shards = tuple(shards[offset : offset + shards_per_unit])
        units.append(
            WorkUnit(
                unit_id=f"unit_{len(units):06d}",
                shards=unit_shards,
                rows=sum(shard.rows for shard in unit_shards),
            )
        )
    return units


def discover_gpu_ids(requested: str) -> list[str]:
    if requested != "auto":
        values = [value.strip() for value in requested.split(",") if value.strip()]
        if not values:
            raise ValueError("--gpus must be auto or a comma-separated list")
        return values
    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "").strip()
    if visible and visible != "-1":
        return [value.strip() for value in visible.split(",") if value.strip()]
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"],
        check=True,
        capture_output=True,
        text=True,
    )
    values = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not values:
        raise RuntimeError("no CUDA GPUs were detected")
    return values


def source_revision(root: Path) -> str:
    source_commit = root / "SOURCE_COMMIT.txt"
    if source_commit.exists():
        value = source_commit.read_text(encoding="utf-8").strip()
        if value:
            return value
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unversioned"


def inventory_bundle_fingerprint(bundle_path: Path) -> dict[str, Any]:
    bundle = load_inventory_bundle(bundle_path)
    files = {
        "inventory_bundle": bundle_path.resolve(),
        "object_inventory": Path(bundle.object_inventory).resolve(),
        "attribute_inventory": Path(bundle.attribute_inventory).resolve(),
        "action_inventory": Path(bundle.action_inventory).resolve(),
    }
    action_canonical = getattr(bundle, "action_canonical_inventory", None)
    if action_canonical:
        files["action_canonical_inventory"] = Path(action_canonical).resolve()
    lexicon_dir = Path(bundle.lexicon_dir).resolve()
    for path in sorted(item for item in lexicon_dir.rglob("*") if item.is_file()):
        files[f"lexicon/{path.relative_to(lexicon_dir).as_posix()}"] = path

    rows = []
    for label, path in sorted(files.items()):
        rows.append(
            {
                "label": label,
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return {"files": rows, "sha256": _json_sha256(rows)}


def verify_input_shards(shards: Iterable[InputShard], *, verify_hashes: bool) -> None:
    for shard in shards:
        path = Path(shard.path)
        if not path.exists() or path.stat().st_size != shard.size_bytes:
            raise ValueError(f"input shard is missing or changed size: {path}")
        if verify_hashes and _sha256_file(path) != shard.sha256:
            raise ValueError(f"input shard SHA256 changed: {path}")


def _unit_artifact_paths(unit_dir: Path) -> list[Path]:
    required = [
        unit_dir / "mixed_pipeline_summary.jsonl",
        unit_dir / "pipeline_state.json",
        unit_dir / "stage6" / "summary.jsonl",
    ]
    root_stage5_files = sorted(
        path
        for path in (unit_dir / "stage5").glob("*")
        if path.is_file() and path.suffix in {".json", ".jsonl"}
    )
    sharded_stage5_files = sorted(
        path
        for path in (unit_dir / "stage456_sharded" / "shards").glob("shard_*/stage5/*")
        if path.is_file() and path.suffix in {".json", ".jsonl"}
    )
    stage6_files = sorted(
        path for path in (unit_dir / "stage6").glob("*") if path.is_file()
    )
    stage5_files = root_stage5_files + sharded_stage5_files
    paths = sorted(set(required + stage5_files + stage6_files))
    missing = [path for path in required if not path.exists()]
    if missing or not stage5_files or not any(path.suffix == ".tsv" for path in stage6_files):
        raise ValueError(
            "unit output is incomplete: "
            + json.dumps(
                {
                    "unit_dir": str(unit_dir),
                    "missing": [str(path) for path in missing],
                    "stage5_files": len(stage5_files),
                    "stage6_files": len(stage6_files),
                },
                sort_keys=True,
            )
        )
    return paths


def _artifact_records(paths: Iterable[Path], *, output_root: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(paths):
        records.append(
            {
                "path": path.relative_to(output_root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return records


def _artifacts_are_valid(
    artifacts: Any,
    *,
    output_root: Path,
    verify_hashes: bool,
) -> bool:
    if not isinstance(artifacts, list) or not artifacts:
        return False
    resolved_root = output_root.resolve()
    try:
        for artifact in artifacts:
            path = (output_root / artifact["path"]).resolve()
            if path != resolved_root and resolved_root not in path.parents:
                return False
            if not path.exists() or path.stat().st_size != int(artifact["size_bytes"]):
                return False
            if verify_hashes and _sha256_file(path) != artifact["sha256"]:
                return False
    except (KeyError, OSError, TypeError, ValueError):
        return False
    return True


def _path_size_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def apply_unit_retention(unit_dir: Path, *, policy: str) -> dict[str, Any]:
    if policy not in RETENTION_POLICIES:
        raise ValueError(f"unsupported retention policy: {policy!r}")
    if policy == "full":
        return {"policy": policy, "pruned_paths": [], "reclaimed_bytes": 0}

    candidate_paths = [
        unit_dir / "stage1",
        unit_dir / "stage3",
        unit_dir / "stage3_sharded",
        unit_dir / "stage4",
        unit_dir / "stage456_sharded" / "stage3_shards",
        unit_dir / "stage456_sharded" / "stage6",
        unit_dir / "stage456_sharded" / "stage6_merged",
    ]
    shard_root = unit_dir / "stage456_sharded" / "shards"
    for shard_dir in sorted(shard_root.glob("shard_*")):
        candidate_paths.extend((shard_dir / "stage4", shard_dir / "stage6"))

    resolved_unit = unit_dir.resolve()
    pruned_paths: list[str] = []
    reclaimed_bytes = 0
    for path in candidate_paths:
        if not path.exists():
            continue
        resolved_path = path.resolve()
        if resolved_path == resolved_unit or resolved_unit not in resolved_path.parents:
            raise ValueError(f"refusing to prune path outside unit directory: {resolved_path}")
        reclaimed_bytes += _path_size_bytes(path)
        pruned_paths.append(path.relative_to(unit_dir).as_posix())
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    return {
        "policy": policy,
        "pruned_paths": pruned_paths,
        "reclaimed_bytes": reclaimed_bytes,
    }


def build_unit_receipt(
    unit: WorkUnit,
    *,
    output_root: Path,
    run_identity_sha256: str,
    gpu_id: str,
    elapsed_seconds: float,
    retention_policy: str = "full",
) -> dict[str, Any]:
    unit_dir = output_root / "units" / unit.unit_id
    summary_rows = [
        json.loads(line)
        for line in (unit_dir / "mixed_pipeline_summary.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    if len(summary_rows) != 1 or summary_rows[0].get("status") != "completed":
        raise ValueError(f"unit mixed summary is not completed: {unit_dir}")
    actual_rows = int(summary_rows[0].get("stage1", {}).get("total", 0) or 0)
    if actual_rows != unit.rows:
        raise ValueError(
            f"unit caption total mismatch: unit={unit.unit_id} expected={unit.rows} "
            f"actual={actual_rows}"
        )

    artifacts = _artifact_records(_unit_artifact_paths(unit_dir), output_root=output_root)
    return {
        "kind": RECEIPT_KIND,
        "unit": {
            "unit_id": unit.unit_id,
            "rows": unit.rows,
            "shards": [asdict(shard) for shard in unit.shards],
        },
        "run_identity_sha256": run_identity_sha256,
        "worker_gpu": gpu_id,
        "elapsed_seconds": elapsed_seconds,
        "finished_at": _utc_now(),
        "artifacts": artifacts,
        "retention": {
            "policy": retention_policy,
            "pruned_paths": [],
            "reclaimed_bytes": 0,
        },
    }


def unit_receipt_is_valid(
    unit: WorkUnit,
    *,
    output_root: Path,
    run_identity_sha256: str,
    verify_hashes: bool,
    retention_policy: str = "full",
) -> bool:
    receipt_path = output_root / "receipts" / f"{unit.unit_id}.json"
    if not receipt_path.exists():
        return False
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("kind") != RECEIPT_KIND:
            return False
        if receipt.get("run_identity_sha256") != run_identity_sha256:
            return False
        if receipt.get("retention", {}).get("policy") != retention_policy:
            return False
        expected_unit = {
            "unit_id": unit.unit_id,
            "rows": unit.rows,
            "shards": [asdict(shard) for shard in unit.shards],
        }
        if receipt.get("unit") != expected_unit:
            return False
        return _artifacts_are_valid(
            receipt.get("artifacts"),
            output_root=output_root,
            verify_hashes=verify_hashes,
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _remove_incomplete_unit(unit_dir: Path, output_root: Path) -> None:
    resolved_unit = unit_dir.resolve()
    resolved_root = (output_root / "units").resolve()
    if resolved_unit.parent != resolved_root:
        raise ValueError(f"refusing to remove path outside unit root: {resolved_unit}")
    if resolved_unit.exists():
        shutil.rmtree(resolved_unit)


def _worker_main(
    gpu_id: str,
    task_queue: Any,
    event_queue: Any,
    settings: WorkerSettings,
) -> None:
    os.environ["CUDA_VISIBLE_DEVICES"] = gpu_id
    try:
        from run_mixed_caption_pipeline import run_mixed_caption_pipeline

        event_queue.put({"event": "ready", "gpu": gpu_id, "at": _utc_now()})
        while True:
            raw = task_queue.get()
            if raw is None:
                break
            unit = WorkUnit(
                unit_id=raw["unit_id"],
                shards=tuple(InputShard(**shard) for shard in raw["shards"]),
                rows=int(raw["rows"]),
            )
            output_root = Path(settings.output_root)
            unit_dir = output_root / "units" / unit.unit_id
            event_queue.put(
                {"event": "started", "gpu": gpu_id, "unit_id": unit.unit_id, "at": _utc_now()}
            )
            started = time.perf_counter()
            run_mixed_caption_pipeline(
                input_paths=[shard.path for shard in unit.shards],
                output_dir=unit_dir,
                object_inventory=Path(settings.object_inventory),
                attribute_inventory=Path(settings.attribute_inventory),
                action_inventory=Path(settings.action_inventory),
                preposition_mwe_lexicon=Path(settings.preposition_mwe_lexicon),
                lexicon_dir=Path(settings.lexicon_dir),
                model=settings.model,
                batch_size=settings.batch_size,
                gpu_mode="require",
                progress_output=unit_dir / "progress.json",
                progress_interval_records=settings.progress_interval_records,
                stage3_sentence_shards=settings.stage3_shards_per_gpu,
                stage3_tag_shards=settings.stage3_shards_per_gpu,
                stage3_jobs=settings.stage3_shards_per_gpu,
                stage3_gpu_devices=["0"],
                max_monolithic_stage456_captions=0,
                stage456_shards=settings.stage456_shards_per_worker,
                stage456_jobs=settings.stage456_jobs_per_worker,
                stage456_merge_jobs=settings.stage456_merge_jobs,
                stage6_count_backend=settings.stage6_count_backend,
                stage6_facts_output_mode="discard",
                runtime_resource_plan={
                    "scaleout_parent": RUN_KIND,
                    "worker_gpu": gpu_id,
                    "unit_id": unit.unit_id,
                },
            )
            receipt = build_unit_receipt(
                unit,
                output_root=output_root,
                run_identity_sha256=settings.run_identity_sha256,
                gpu_id=gpu_id,
                elapsed_seconds=time.perf_counter() - started,
                retention_policy=settings.retention_policy,
            )
            if not _artifacts_are_valid(
                receipt["artifacts"], output_root=output_root, verify_hashes=True
            ):
                raise ValueError(f"unit artifacts failed pre-retention verification: {unit_dir}")
            receipt["retention"] = apply_unit_retention(
                unit_dir, policy=settings.retention_policy
            )
            if not _artifacts_are_valid(
                receipt["artifacts"], output_root=output_root, verify_hashes=True
            ):
                raise ValueError(f"unit artifacts failed post-retention verification: {unit_dir}")
            _atomic_json(output_root / "receipts" / f"{unit.unit_id}.json", receipt)
            event_queue.put(
                {
                    "event": "completed",
                    "gpu": gpu_id,
                    "unit_id": unit.unit_id,
                    "rows": unit.rows,
                    "elapsed_seconds": receipt["elapsed_seconds"],
                    "at": _utc_now(),
                }
            )
        event_queue.put({"event": "worker_done", "gpu": gpu_id, "at": _utc_now()})
    except BaseException as exc:
        event_queue.put(
            {
                "event": "failed",
                "gpu": gpu_id,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
                "at": _utc_now(),
            }
        )
        raise


def _write_progress(
    output_root: Path,
    *,
    status: str,
    units: list[WorkUnit],
    completed: set[str],
    active: dict[str, str],
    gpu_ids: list[str],
    started_at: str,
    failure: dict[str, Any] | None = None,
) -> None:
    payload = {
        "artifact_type": "gpic_fixed_lexicon_scaleout_progress",
        "status": status,
        "started_at": started_at,
        "updated_at_utc": _utc_now(),
        "total_units": len(units),
        "completed_units": len(completed),
        "remaining_units": len(units) - len(completed),
        "completed_rows": sum(unit.rows for unit in units if unit.unit_id in completed),
        "total_rows": sum(unit.rows for unit in units),
        "completed_unit_ids": sorted(completed),
        "active": dict(sorted(active.items())),
        "runtime_gpu_ids": gpu_ids,
    }
    if failure is not None:
        payload["failure"] = failure
    _atomic_json(output_root / "progress.json", payload)


def _run_units(
    units: list[WorkUnit],
    *,
    output_root: Path,
    completed: set[str],
    gpu_ids: list[str],
    settings: WorkerSettings,
    heartbeat_seconds: float,
) -> None:
    pending = [unit for unit in units if unit.unit_id not in completed]
    if not pending:
        return
    for unit in pending:
        _remove_incomplete_unit(output_root / "units" / unit.unit_id, output_root)

    context = mp.get_context("spawn")
    task_queue = context.Queue()
    event_queue = context.Queue()
    for unit in pending:
        task_queue.put(
            {
                "unit_id": unit.unit_id,
                "rows": unit.rows,
                "shards": [asdict(shard) for shard in unit.shards],
            }
        )
    for _ in gpu_ids:
        task_queue.put(None)

    workers = [
        context.Process(
            target=_worker_main,
            args=(gpu_id, task_queue, event_queue, settings),
            name=f"fixedlex-gpu-{gpu_id}",
        )
        for gpu_id in gpu_ids
    ]
    for worker in workers:
        worker.start()

    active: dict[str, str] = {}
    failure: dict[str, Any] | None = None
    workers_done: set[str] = set()
    started_at = _utc_now()
    try:
        while len(workers_done) < len(workers):
            try:
                event = event_queue.get(timeout=heartbeat_seconds)
            except queue.Empty:
                dead = [
                    {"name": worker.name, "exitcode": worker.exitcode}
                    for worker in workers
                    if not worker.is_alive() and worker.exitcode not in {0, None}
                ]
                if dead:
                    failure = {"event": "worker_exit", "workers": dead, "at": _utc_now()}
                    break
                _write_progress(
                    output_root,
                    status="running",
                    units=units,
                    completed=completed,
                    active=active,
                    gpu_ids=gpu_ids,
                    started_at=started_at,
                )
                continue
            gpu = str(event.get("gpu") or "")
            if event["event"] == "started":
                active[gpu] = event["unit_id"]
            elif event["event"] == "completed":
                completed.add(event["unit_id"])
                active.pop(gpu, None)
            elif event["event"] == "failed":
                failure = event
                break
            elif event["event"] == "worker_done":
                workers_done.add(gpu)
            _write_progress(
                output_root,
                status="running",
                units=units,
                completed=completed,
                active=active,
                gpu_ids=gpu_ids,
                started_at=started_at,
            )
    finally:
        if failure is not None:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
        for worker in workers:
            worker.join()

    unexpected = [
        {"name": worker.name, "exitcode": worker.exitcode}
        for worker in workers
        if worker.exitcode not in {0, None}
    ]
    if failure is not None or unexpected:
        details = failure or {"event": "worker_exit", "workers": unexpected, "at": _utc_now()}
        _write_progress(
            output_root,
            status="failed",
            units=units,
            completed=completed,
            active=active,
            gpu_ids=gpu_ids,
            started_at=started_at,
            failure=details,
        )
        raise RuntimeError("fixed-lexicon scale-out worker failed: " + json.dumps(details))


def _run_identity(
    *,
    manifest_path: Path,
    input_manifest: dict[str, Any],
    units: list[WorkUnit],
    inventory_fingerprint: dict[str, Any],
    preposition_mwe_lexicon: Path,
    model: str,
    revision: str,
    retention_policy: str,
) -> dict[str, Any]:
    payload = {
        "kind": RUN_KIND,
        "input_manifest_kind": input_manifest["kind"],
        "input_manifest_sha256": _sha256_file(manifest_path),
        "inventory_bundle": inventory_fingerprint,
        "preposition_mwe_lexicon": {
            "path": str(preposition_mwe_lexicon),
            "size_bytes": preposition_mwe_lexicon.stat().st_size,
            "sha256": _sha256_file(preposition_mwe_lexicon),
        },
        "source_revision": revision,
        "retention_policy": retention_policy,
        "semantic_settings": {
            "model": model,
            "stage3_disabled_components": list(DEFAULT_STAGE3_DISABLED_COMPONENTS),
            "stage6_facts_output_mode": "discard",
        },
        "units": [
            {
                "unit_id": unit.unit_id,
                "rows": unit.rows,
                "shards": [asdict(shard) for shard in unit.shards],
            }
            for unit in units
        ],
    }
    payload["identity_sha256"] = _json_sha256(payload)
    return payload


def run(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = Path(args.input_manifest).resolve()
    output_root = Path(args.output_root).resolve()
    bundle_path = Path(args.inventory_bundle).resolve()
    preposition_mwe_lexicon = Path(args.preposition_mwe_lexicon).resolve()
    input_manifest, shards = load_input_manifest(manifest_path)
    units = build_work_units(shards, args.input_shards_per_unit)
    verify_input_shards(shards, verify_hashes=args.verify_input_hashes)
    inventory_fingerprint = inventory_bundle_fingerprint(bundle_path)
    bundle = load_inventory_bundle(bundle_path)
    revision = source_revision(ROOT)
    identity = _run_identity(
        manifest_path=manifest_path,
        input_manifest=input_manifest,
        units=units,
        inventory_fingerprint=inventory_fingerprint,
        preposition_mwe_lexicon=preposition_mwe_lexicon,
        model=args.model,
        revision=revision,
        retention_policy=args.retention_policy,
    )

    output_root.mkdir(parents=True, exist_ok=True)
    run_manifest_path = output_root / "run_manifest.json"
    if run_manifest_path.exists():
        existing = json.loads(run_manifest_path.read_text(encoding="utf-8"))
        if existing != identity:
            raise RuntimeError("output root belongs to a different immutable run identity")
    else:
        _atomic_json(run_manifest_path, identity)

    completed = {
        unit.unit_id
        for unit in units
        if unit_receipt_is_valid(
            unit,
            output_root=output_root,
            run_identity_sha256=identity["identity_sha256"],
            verify_hashes=args.verify_completed_hashes,
            retention_policy=args.retention_policy,
        )
    }
    complete_path = output_root / "COMPLETE.json"
    if len(completed) == len(units) and complete_path.exists():
        complete = json.loads(complete_path.read_text(encoding="utf-8"))
        if complete.get("identity_sha256") != identity["identity_sha256"]:
            raise RuntimeError("COMPLETE.json does not match the immutable run identity")
        if not _artifacts_are_valid(
            complete.get("artifacts"),
            output_root=output_root,
            verify_hashes=args.verify_completed_hashes,
        ):
            raise RuntimeError("COMPLETE.json references missing or changed final artifacts")
        return complete
    gpu_ids = discover_gpu_ids(args.gpus)
    settings = WorkerSettings(
        output_root=str(output_root),
        run_identity_sha256=identity["identity_sha256"],
        inventory_bundle=str(bundle_path),
        object_inventory=str(Path(bundle.object_inventory).resolve()),
        attribute_inventory=str(Path(bundle.attribute_inventory).resolve()),
        action_inventory=str(Path(bundle.action_inventory).resolve()),
        lexicon_dir=str(Path(bundle.lexicon_dir).resolve()),
        preposition_mwe_lexicon=str(preposition_mwe_lexicon),
        model=args.model,
        batch_size=args.batch_size,
        stage3_shards_per_gpu=args.stage3_shards_per_gpu,
        stage456_shards_per_worker=args.stage456_shards_per_worker,
        stage456_jobs_per_worker=args.stage456_jobs_per_worker,
        stage456_merge_jobs=args.stage456_merge_jobs,
        stage6_count_backend=args.stage6_count_backend,
        progress_interval_records=args.progress_interval_records,
        retention_policy=args.retention_policy,
    )
    _write_progress(
        output_root,
        status="starting" if len(completed) < len(units) else "merging",
        units=units,
        completed=completed,
        active={},
        gpu_ids=gpu_ids,
        started_at=_utc_now(),
    )
    _run_units(
        units,
        output_root=output_root,
        completed=completed,
        gpu_ids=gpu_ids,
        settings=settings,
        heartbeat_seconds=args.heartbeat_seconds,
    )
    if len(completed) != len(units):
        raise RuntimeError("not all fixed-lexicon units completed")

    merged_stage6 = output_root / "stage6"
    if merged_stage6.exists() and not (output_root / "COMPLETE.json").exists():
        shutil.rmtree(merged_stage6)
    stage6_summary = merge_stage6_count_dirs(
        [output_root / "units" / unit.unit_id / "stage6" for unit in units],
        merged_stage6,
        merge_jobs=args.global_merge_jobs,
    )
    final_artifacts = _artifact_records(
        (path for path in merged_stage6.iterdir() if path.is_file()),
        output_root=output_root,
    )
    summary = {
        "artifact_type": RUN_KIND,
        "status": "completed",
        "completed_at": _utc_now(),
        "identity_sha256": identity["identity_sha256"],
        "input_rows": sum(unit.rows for unit in units),
        "unit_count": len(units),
        "stage5_roots": [
            str(
                output_root
                / "units"
                / unit.unit_id
                / "stage456_sharded"
                / "shards"
            )
            for unit in units
        ],
        "stage6": stage6_summary,
        "artifacts": final_artifacts,
        "runtime_gpu_ids": gpu_ids,
        "retention_policy": args.retention_policy,
    }
    _atomic_json(output_root / "summary.json", summary)
    _atomic_json(output_root / "COMPLETE.json", summary)
    _write_progress(
        output_root,
        status="completed",
        units=units,
        completed=completed,
        active={},
        gpu_ids=gpu_ids,
        started_at=summary["completed_at"],
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run resumable fixed-inventory GPIC Stage 1-6 over immutable shards."
    )
    parser.add_argument("--input-manifest", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--inventory-bundle", required=True)
    parser.add_argument(
        "--preposition-mwe-lexicon",
        default=str(ROOT / "resources" / "lexicons" / "preposition_mwes.tsv"),
    )
    parser.add_argument("--gpus", default="auto")
    parser.add_argument("--input-shards-per-unit", type=int, default=10)
    parser.add_argument("--model", default=DEFAULT_STAGE3_MODEL)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_STAGE3_BATCH_SIZE)
    parser.add_argument("--stage3-shards-per-gpu", type=int, default=8)
    parser.add_argument("--stage456-shards-per-worker", type=int, default=7)
    parser.add_argument("--stage456-jobs-per-worker", type=int, default=7)
    parser.add_argument("--stage456-merge-jobs", type=int, default=2)
    parser.add_argument("--global-merge-jobs", type=int, default=8)
    parser.add_argument("--stage6-count-backend", choices=("sqlite", "memory"), default="sqlite")
    parser.add_argument("--progress-interval-records", type=int, default=5000)
    parser.add_argument("--heartbeat-seconds", type=float, default=30.0)
    parser.add_argument(
        "--verify-input-hashes", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument("--verify-completed-hashes", action="store_true")
    parser.add_argument(
        "--retention-policy",
        choices=RETENTION_POLICIES,
        default="full",
        help=(
            "full keeps every unit intermediate; canonical_counts keeps verified "
            "Stage 5 canonical artifacts and unit Stage 6 count tables only"
        ),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    positive = {
        "input_shards_per_unit": args.input_shards_per_unit,
        "batch_size": args.batch_size,
        "stage3_shards_per_gpu": args.stage3_shards_per_gpu,
        "stage456_shards_per_worker": args.stage456_shards_per_worker,
        "stage456_jobs_per_worker": args.stage456_jobs_per_worker,
        "stage456_merge_jobs": args.stage456_merge_jobs,
        "global_merge_jobs": args.global_merge_jobs,
        "progress_interval_records": args.progress_interval_records,
    }
    invalid = {name: value for name, value in positive.items() if value < 1}
    if invalid:
        raise ValueError("scale-out integer options must be positive: " + json.dumps(invalid))
    if args.heartbeat_seconds <= 0:
        raise ValueError("heartbeat_seconds must be greater than zero")
    print(json.dumps(run(args), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(guarded_entrypoint("fixed_lexicon_scaleout", main))
