from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SRC = ROOT / "src"
for path in (str(SCRIPTS), str(SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)

from run_fixed_lexicon_scaleout import (
    _artifact_records,
    _artifacts_are_valid,
    InputShard,
    RECEIPT_KIND,
    build_work_units,
    load_input_manifest,
    unit_receipt_is_valid,
)


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
