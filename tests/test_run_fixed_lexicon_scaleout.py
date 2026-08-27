from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import os
from queue import Queue
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SRC = ROOT / "src"
for path in (str(SCRIPTS), str(SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)

from run_fixed_lexicon_scaleout import (
    _artifact_records,
    _artifacts_are_valid,
    apply_unit_retention,
    InputShard,
    RECEIPT_KIND,
    build_work_units,
    load_input_manifest,
    unit_receipt_is_valid,
)
import run_fixed_lexicon_scaleout as scaleout
import run_mixed_caption_pipeline as mixed
import run_stage3_sharded as stage3
from planned_pause import PauseControl, STATE_FILE, request_pause

sys.path.insert(0, str(ROOT / "tests" / "fixtures"))
from lexical_scaleout_worker import worker as fixture_worker


def _shard(index: int, path: Path) -> InputShard:
    content = f'{{"id":"{index}","caption":"caption {index}"}}\n'.encode()
    path.write_bytes(content)
    return InputShard(
        shard_id=f"shard_{index:06d}",
        path=str(path),
        rows=1,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def test_load_manifest_resolves_relative_paths_and_builds_contiguous_units(tmp_path: Path) -> None:
    shards = [_shard(index, tmp_path / f"input_{index}.jsonl") for index in range(5)]
    manifest = tmp_path / "manifest.json"
    payload = {
        "kind": "gpic-caption-shards-v1",
        "shards": [
            {**shard.__dict__, "path": Path(shard.path).name}
            for shard in shards
        ],
    }
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    _, loaded = load_input_manifest(manifest)
    units = build_work_units(loaded, 2)

    assert [unit.unit_id for unit in units] == ["unit_000000", "unit_000001", "unit_000002"]
    assert [[shard.shard_id for shard in unit.shards] for unit in units] == [
        ["shard_000000", "shard_000001"],
        ["shard_000002", "shard_000003"],
        ["shard_000004"],
    ]
    assert all(Path(shard.path).is_absolute() for shard in loaded)


def test_receipt_requires_matching_identity_unit_and_artifact_hash(tmp_path: Path) -> None:
    shard = _shard(0, tmp_path / "input.jsonl")
    unit = build_work_units([shard], 1)[0]
    artifact = tmp_path / "units" / unit.unit_id / "stage6" / "objects.tsv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("count_key\tcount\nobject\t1\n", encoding="utf-8")
    receipt_path = tmp_path / "receipts" / f"{unit.unit_id}.json"
    receipt_path.parent.mkdir()
    receipt = {
        "kind": RECEIPT_KIND,
        "run_identity_sha256": "a" * 64,
        "retention": {
            "policy": "full",
            "pruned_paths": [],
            "reclaimed_bytes": 0,
        },
        "unit": {
            "unit_id": unit.unit_id,
            "rows": unit.rows,
            "shards": [shard.__dict__],
        },
        "artifacts": [
            {
                "path": artifact.relative_to(tmp_path).as_posix(),
                "size_bytes": artifact.stat().st_size,
                "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            }
        ],
    }
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    assert unit_receipt_is_valid(
        unit,
        output_root=tmp_path,
        run_identity_sha256="a" * 64,
        verify_hashes=True,
    )
    artifact.write_text("changed\n", encoding="utf-8")
    assert not unit_receipt_is_valid(
        unit,
        output_root=tmp_path,
        run_identity_sha256="a" * 64,
        verify_hashes=True,
    )


def test_receipt_requires_matching_retention_policy(tmp_path: Path) -> None:
    shard = _shard(0, tmp_path / "input.jsonl")
    unit = build_work_units([shard], 1)[0]
    artifact = tmp_path / "units" / unit.unit_id / "stage6" / "objects.tsv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("count_key\tcount\nobject\t1\n", encoding="utf-8")
    receipt_path = tmp_path / "receipts" / f"{unit.unit_id}.json"
    receipt_path.parent.mkdir()
    receipt_path.write_text(
        json.dumps(
            {
                "kind": RECEIPT_KIND,
                "run_identity_sha256": "c" * 64,
                "retention": {
                    "policy": "canonical_counts",
                    "pruned_paths": [],
                    "reclaimed_bytes": 0,
                },
                "unit": {
                    "unit_id": unit.unit_id,
                    "rows": unit.rows,
                    "shards": [shard.__dict__],
                },
                "artifacts": _artifact_records([artifact], output_root=tmp_path),
            }
        ),
        encoding="utf-8",
    )

    assert unit_receipt_is_valid(
        unit,
        output_root=tmp_path,
        run_identity_sha256="c" * 64,
        verify_hashes=True,
        retention_policy="canonical_counts",
    )
    assert not unit_receipt_is_valid(
        unit,
        output_root=tmp_path,
        run_identity_sha256="c" * 64,
        verify_hashes=True,
        retention_policy="full",
    )


def test_canonical_counts_retention_preserves_stage5_and_unit_stage6(tmp_path: Path) -> None:
    unit_dir = tmp_path / "units" / "unit_000000"
    retained = [
        unit_dir / "mixed_pipeline_summary.jsonl",
        unit_dir / "pipeline_state.json",
        unit_dir / "stage5" / "summary.jsonl",
        unit_dir / "stage456_sharded" / "shards" / "shard_0000" / "stage5" / "canonical_mentions.jsonl",
        unit_dir / "stage6" / "objects.tsv",
    ]
    pruned = [
        unit_dir / "stage1" / "captions.jsonl",
        unit_dir / "stage3_sharded" / "records.jsonl",
        unit_dir / "stage456_sharded" / "stage3_shards" / "shard.jsonl",
        unit_dir / "stage456_sharded" / "shards" / "shard_0000" / "stage4" / "facts.jsonl",
        unit_dir / "stage456_sharded" / "shards" / "shard_0000" / "stage6" / "objects.tsv",
        unit_dir / "stage456_sharded" / "stage6_merged" / "objects.tsv",
    ]
    for path in retained + pruned:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(path.name, encoding="utf-8")

    result = apply_unit_retention(unit_dir, policy="canonical_counts")

    assert result["policy"] == "canonical_counts"
    assert result["reclaimed_bytes"] == sum(len(path.name.encode()) for path in pruned)
    assert all(path.exists() for path in retained)
    assert all(not path.exists() for path in pruned)
    assert "stage1" in result["pruned_paths"]
    assert "stage456_sharded/shards/shard_0000/stage4" in result["pruned_paths"]


def test_unit_without_receipt_is_never_complete(tmp_path: Path) -> None:
    shard = _shard(0, tmp_path / "input.jsonl")
    unit = build_work_units([shard], 1)[0]
    (tmp_path / "units" / unit.unit_id).mkdir(parents=True)

    assert not unit_receipt_is_valid(
        unit,
        output_root=tmp_path,
        run_identity_sha256="b" * 64,
        verify_hashes=False,
    )


def test_final_artifact_manifest_rejects_missing_changed_and_escaped_files(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "stage6" / "objects.tsv"
    artifact.parent.mkdir()
    artifact.write_text("count_key\tcount\nobject\t1\n", encoding="utf-8")
    records = _artifact_records([artifact], output_root=tmp_path)

    assert _artifacts_are_valid(records, output_root=tmp_path, verify_hashes=True)
    artifact.write_text("changed\n", encoding="utf-8")
    assert not _artifacts_are_valid(records, output_root=tmp_path, verify_hashes=True)

    records[0]["path"] = "../outside.tsv"
    assert not _artifacts_are_valid(records, output_root=tmp_path, verify_hashes=False)


@pytest.mark.parametrize("gpu_id", ["0", "1", "3", "GPU-fixture-device"])
def test_scaleout_worker_preserves_gpu_selector_through_stage3_subprocess(tmp_path, monkeypatch, gpu_id):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1,2,3")
    seen = []
    summary = tmp_path / "worker_summary.jsonl"
    summary.write_text('{"total": 1}\n', encoding="utf-8")

    def run_stage3_subprocess(command, **kwargs):
        seen.append(kwargs["env"]["CUDA_VISIBLE_DEVICES"])
        return SimpleNamespace(returncode=0)

    def run_mixed(**kwargs):
        assert os.environ["CUDA_VISIBLE_DEVICES"] == gpu_id
        shard = stage3.Stage3Shard(
            caption_shape="sentence", shard_index=0, input_path=tmp_path / "input.jsonl",
            output_path=tmp_path / "records.jsonl", summary_path=summary,
            progress_path=tmp_path / "progress.json", stdout_path=tmp_path / "stdout.log",
            stderr_path=tmp_path / "stderr.log", row_count=1,
            gpu_device=kwargs["stage3_gpu_devices"][0])
        stage3.run_one_stage3_shard(shard, model="fixture", batch_size=192,
                                   gpu_mode="require", progress_interval_records=10,
                                   disabled_components=())

    monkeypatch.setattr(mixed, "run_mixed_caption_pipeline", run_mixed)
    monkeypatch.setattr(stage3.subprocess, "run", run_stage3_subprocess)
    monkeypatch.setattr(scaleout, "build_unit_receipt", lambda *a, **kw:
                        {"artifacts": [], "elapsed_seconds": 0})
    monkeypatch.setattr(scaleout, "_artifacts_are_valid", lambda *a, **kw: True)
    monkeypatch.setattr(scaleout, "apply_unit_retention", lambda *a, **kw: {})
    settings = SimpleNamespace(
        output_root=str(tmp_path), object_inventory="object.tsv", attribute_inventory="attribute.tsv",
        action_inventory="action.tsv", preposition_mwe_lexicon="prep.tsv", lexicon_dir="lexicons",
        model="fixture", batch_size=192, progress_interval_records=10, stage3_shards_per_gpu=8,
        stage456_shards_per_worker=7, stage456_jobs_per_worker=7, stage456_merge_jobs=8,
        stage6_count_backend="sqlite", run_identity_sha256="a" * 64, retention_policy="canonical_counts")
    tasks, events = Queue(), Queue()
    tasks.put({"unit_id": "unit_000000", "rows": 1, "shards": []})
    tasks.put(None)
    scaleout._worker_main(gpu_id, tasks, events, settings)
    assert seen == [gpu_id]


@pytest.fixture
def resumable_run(tmp_path, monkeypatch):
    shards = [_shard(i, tmp_path / f"input_{i}.jsonl") for i in range(11)]
    manifest = tmp_path / "inputs.json"
    manifest.write_text(json.dumps({"kind": "gpic-caption-shards-v1",
                                   "shards": [shard.__dict__ for shard in shards]}))
    prep = tmp_path / "prep.tsv"
    prep.write_text("fixture\n")
    bundle = SimpleNamespace(object_inventory=tmp_path / "object.tsv",
                             attribute_inventory=tmp_path / "attribute.tsv",
                             action_inventory=tmp_path / "action.tsv", lexicon_dir=tmp_path)
    monkeypatch.setattr(scaleout, "inventory_bundle_fingerprint", lambda _: {"sha256": "fixed"})
    monkeypatch.setattr(scaleout, "load_inventory_bundle", lambda _: bundle)
    monkeypatch.setattr(scaleout, "source_revision", lambda _: "fixture-revision")
    monkeypatch.setattr(scaleout, "_worker_main", fixture_worker)

    def merge(roots, output, **kwargs):
        output.mkdir()
        rows = sorted(line for root in roots
                      for line in (root / "objects.tsv").read_text().splitlines()[1:])
        (output / "objects.tsv").write_text("id\tcount\n" + "\n".join(rows) + "\n")
        return {"rows": len(rows)}

    monkeypatch.setattr(scaleout, "merge_stage6_count_dirs", merge)
    args = scaleout.build_parser().parse_args([
        "--input-manifest", str(manifest), "--output-root", str(tmp_path / "run"),
        "--inventory-bundle", str(tmp_path / "bundle.json"),
        "--preposition-mwe-lexicon", str(prep), "--gpus", "0",
        "--input-shards-per-unit", "1", "--heartbeat-seconds", "0.05",
        "--retention-policy", "canonical_counts",
    ])
    output = Path(args.output_root)
    output.mkdir()
    return args, output


def test_pause_resume_1_to_2_to_8_workers_keeps_receipts_and_result(resumable_run):
    args, output = resumable_run
    control = output / "fixture_control.json"
    control.write_text(json.dumps({"pause": ["unit_000000"]}))
    first = scaleout.run(args)
    assert first["status"] == "paused" and first["completed_units"] == 1
    assert not (output / "COMPLETE.json").exists()
    assert not (output / "stage6").exists()
    receipt = output / "receipts/unit_000000.json"
    initial_receipt = receipt.read_bytes()
    identity = (output / "run_manifest.json").read_bytes()
    with pytest.raises(ValueError, match="explicit --resume"):
        scaleout.run(args)

    args.resume = True
    args.gpus = "0,1"
    control.write_text(json.dumps({"pause": ["unit_000001", "unit_000002"]}))
    second = scaleout.run(args)
    assert second["status"] == "paused"
    assert 2 <= second["completed_units"] <= 3
    assert receipt.read_bytes() == initial_receipt
    assert (output / "run_manifest.json").read_bytes() == identity

    args.gpus = "0,1,2,3,4,5,6,7"
    control.write_text("{}")
    result = scaleout.run(args)
    assert result["status"] == "completed" and result["input_rows"] == 11
    assert receipt.read_bytes() == initial_receipt
    assert (output / "run_manifest.json").read_bytes() == identity
    assert all(len(path.read_text().splitlines()) == 1 for path in output.glob("*.calls"))
    assert len(list(output.glob("*.calls"))) == 11
    assert len(list((output / "receipts").glob("*.json"))) == 11
    merged = (output / "stage6/objects.tsv").read_bytes()

    args.output_root = str(output.parent / "uninterrupted")
    args.gpus = "0"
    args.resume = False
    scaleout.run(args)
    assert (Path(args.output_root) / "stage6/objects.tsv").read_bytes() == merged


def test_resume_checks_hashes_even_without_hash_flag(resumable_run):
    args, output = resumable_run
    control = output / "fixture_control.json"
    control.write_text(json.dumps({"pause": ["unit_000000"]}))
    scaleout.run(args)
    artifact = output / "units/unit_000000/stage6/objects.tsv"
    original = artifact.read_bytes()
    artifact.write_bytes(original.replace(b"0\t1", b"0\t9"))
    assert artifact.stat().st_size == len(original)
    args.resume = True
    assert not args.verify_completed_hashes
    result = scaleout.run(args)
    assert result["status"] == "paused"
    assert artifact.read_bytes() == original
    assert len((output / "unit_000000.calls").read_text().splitlines()) == 2


def test_failure_during_drain_is_not_a_successful_pause(resumable_run):
    args, output = resumable_run
    (output / "fixture_control.json").write_text(json.dumps({
        "pause": ["unit_000000"], "fail": ["unit_000000"],
    }))
    with pytest.raises(RuntimeError, match="worker failed"):
        scaleout.run(args)
    assert json.loads((output / STATE_FILE).read_text())["status"] == "failed"
    assert not (output / "receipts/unit_000000.json").exists()
    assert not (output / "COMPLETE.json").exists()


@pytest.mark.parametrize("field,value", [("batch_size", 64), ("stage3_shards_per_gpu", 4),
                                         ("input_shards_per_unit", 2)])
def test_resume_rejects_changed_batch_or_grouping(resumable_run, field, value):
    args, output = resumable_run
    # Stop before dispatch without importing a GPU runtime.
    def save_and_pause(*a, **kw):
        request_pause(output)
        return True
    from unittest.mock import patch
    with patch.object(scaleout, "_run_units", save_and_pause):
        scaleout.run(args)
    args.resume = True
    setattr(args, field, value)
    with pytest.raises(RuntimeError, match="different immutable run identity"):
        scaleout.run(args)


def test_pause_before_dispatch_does_not_remove_partial_unit(tmp_path, monkeypatch):
    control = PauseControl.start(tmp_path, "fixture")
    request_pause(tmp_path)
    def unexpected(*a, **kw):
        pytest.fail("must not delete or dispatch after an existing pause request")
    monkeypatch.setattr(scaleout, "_remove_incomplete_unit", unexpected)
    monkeypatch.setattr(scaleout.mp, "get_context", unexpected)
    assert scaleout._run_units([], output_root=tmp_path, completed=set(), gpu_ids=["0"],
                               settings=None, heartbeat_seconds=0.05, pause=control)


def test_pause_during_final_merge_finishes_normally(resumable_run, monkeypatch):
    args, output = resumable_run
    merge = scaleout.merge_stage6_count_dirs
    def pause_then_merge(*a, **kw):
        request_pause(output)
        return merge(*a, **kw)
    monkeypatch.setattr(scaleout, "merge_stage6_count_dirs", pause_then_merge)
    result = scaleout.run(args)
    assert result["status"] == "completed"
    assert json.loads((output / STATE_FILE).read_text())["status"] == "completed"
    assert (output / "COMPLETE.json").is_file()


def test_worker_exit_without_done_is_failure_not_infinite_wait(resumable_run):
    args, output = resumable_run
    (output / "fixture_control.json").write_text('{"exit_early": true}')
    with pytest.raises(RuntimeError, match="worker failed"):
        scaleout.run(args)
    assert json.loads((output / STATE_FILE).read_text())["status"] == "failed"


@pytest.mark.parametrize("gpus", ["", "0,0", "0,", ",1"])
def test_gpu_selector_rejects_duplicates_and_empty_tokens(gpus):
    with pytest.raises(ValueError, match="unique selectors"):
        scaleout.discover_gpu_ids(gpus)


def test_partial_worker_start_failure_cleans_up_without_queue_flush_wait(tmp_path, monkeypatch):
    workers, queues = [], []

    class TestQueue(Queue):
        cancelled = False
        def __init__(self):
            super().__init__()
            queues.append(self)
        def cancel_join_thread(self):
            self.cancelled = True
        def close(self):
            pass

    class Process:
        exitcode = None
        alive = False
        def __init__(self, **kwargs):
            self.name = kwargs["name"]
            workers.append(self)
        def start(self):
            if self is workers[1]:
                raise RuntimeError("fixture process start failure")
            self.alive = True
        def is_alive(self):
            return self.alive
        def terminate(self):
            self.alive = False
            self.exitcode = -15
        def join(self, timeout):
            assert timeout > 0 and not self.alive

    monkeypatch.setattr(scaleout.mp, "get_context", lambda _: SimpleNamespace(
        Queue=TestQueue, Process=Process))
    unit = build_work_units([_shard(0, tmp_path / "input.jsonl")], 1)[0]
    with pytest.raises(RuntimeError, match="fixture process start failure"):
        scaleout._run_units([unit], output_root=tmp_path, completed=set(), gpu_ids=["0", "1"],
                            settings=None, heartbeat_seconds=0.05)
    assert workers[0].exitcode == -15
    assert queues[0].cancelled
