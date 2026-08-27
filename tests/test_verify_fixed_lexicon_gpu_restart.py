from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_fixed_lexicon_gpu_restart import assert_reused, descendants, prepare_input


def source(tmp_path, rows):
    content = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
    path = tmp_path / "source.jsonl"
    path.write_bytes(content)
    manifest = tmp_path / "source.json"
    manifest.write_text(json.dumps({"kind": "gpic-caption-shards-v1", "shards": [{
        "shard_id": "s", "path": str(path), "rows": len(rows),
        "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}]}))
    return manifest


@pytest.mark.parametrize("id_field", ["caption_id", "key", "id"])
def test_preparation_preserves_order_and_all_row_markers(tmp_path, id_field):
    rows = [{id_field: str(i), "caption": "A blue car.", "global_index": i,
             "parent_global_index": i * 2} for i in range(8)]
    manifest = prepare_input(source(tmp_path, rows), tmp_path / "prepared", rows=8)
    shards = json.loads(manifest.read_text())["shards"]
    actual = [json.loads(line) for shard in shards for line in Path(shard["path"]).read_text().splitlines()]
    assert actual == rows
    assert [s["rows"] for s in shards] == [2, 2, 2, 2]


def test_preparation_uses_pipeline_conflicting_id_gate(tmp_path):
    rows = [{"id": str(i), "caption_id": "conflicting"} for i in range(4)]
    with pytest.raises(ValueError, match="conflicting caption identifiers"):
        prepare_input(source(tmp_path, rows), tmp_path / "prepared", rows=4)
    assert not (tmp_path / "prepared").exists()


@pytest.mark.parametrize("ids", [["a", "a", "b", "c"], ["a", "", "b", "c"]])
def test_preparation_rejects_duplicate_or_missing_ids(tmp_path, ids):
    with pytest.raises(ValueError):
        prepare_input(source(tmp_path, [{"caption_id": i} for i in ids]), tmp_path / "prepared", rows=4)
    assert not (tmp_path / "prepared").exists()


def test_probe_excludes_unrelated_production_gpu_processes():
    assert descendants(10, {10: 1, 11: 10, 12: 11, 20: 1, 21: 20}) == {10, 11, 12}


def test_reuse_rejects_rewritten_completed_output(tmp_path):
    path = tmp_path / "receipt.json"
    path.write_bytes(b"original")
    snapshot = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()}
    assert_reused(tmp_path, snapshot)
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="rewrote"):
        assert_reused(tmp_path, snapshot)
