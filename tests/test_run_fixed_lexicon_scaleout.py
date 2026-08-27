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
