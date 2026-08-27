from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from verify_fixed_lexicon_retention_smoke import RECEIPT_KIND, verify


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_run(root: Path, *, retained: bool, changed: bool = False, partitions: int = 1) -> None:
    shards = root / "units" / "unit_000000" / "stage456_sharded" / "shards"
    mentions = [{"id": 2}, {"id": 1}]
    if changed:
        mentions.append({"id": 3})
    stage5_files = []
    for index in range(partitions):
        stage5 = shards / f"shard_{index:04d}" / "stage5"
        _write_jsonl(stage5 / "canonical_mentions.jsonl", mentions[index::partitions])
        _write_jsonl(stage5 / "canonical_edges.jsonl", [{"edge": "a"}][index::partitions])
        stage5_files.extend(stage5.glob("*.jsonl"))
    stage6 = root / "stage6" / "objects.tsv"
    stage6.parent.mkdir(parents=True)
    stage6.write_text("object\tcount\nthing\t1\n", encoding="utf-8")
    if not retained:
        return

    unit_stage6 = root / "units" / "unit_000000" / "stage6" / "objects.tsv"
    unit_stage6.parent.mkdir(parents=True)
    unit_stage6.write_text(stage6.read_text(encoding="utf-8"), encoding="utf-8")
    complete = root / "COMPLETE.json"
    complete.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    artifacts = []
    for path in [*stage5_files, unit_stage6]:
        artifacts.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": _sha(path),
            }
        )
    receipt = {
        "kind": RECEIPT_KIND,
        "unit": {"unit_id": "unit_000000"},
        "retention": {
            "policy": "canonical_counts",
            "pruned_paths": ["stage1"],
            "reclaimed_bytes": 123,
        },
        "artifacts": artifacts,
    }
    receipt_path = root / "receipts" / "unit_000000.json"
    receipt_path.parent.mkdir()
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")


def test_retention_smoke_verifier_accepts_equivalent_outputs(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    _build_run(baseline, retained=False)
    _build_run(candidate, retained=True)

    result = verify(baseline, candidate, retention_policy="canonical_counts")

    assert result["status"] == "ok"
    assert result["retention"]["reclaimed_bytes"] == 123
    assert result["stage5"]["canonical_mentions.jsonl"]["rows"] == 2


def test_retention_smoke_verifier_rejects_stage5_change(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    _build_run(baseline, retained=False)
    _build_run(candidate, retained=True, changed=True)

    try:
        verify(baseline, candidate, retention_policy="canonical_counts")
    except ValueError as exc:
        assert "Stage 5 canonical artifacts differ" in str(exc)
    else:
        raise AssertionError("expected Stage 5 mismatch")


@pytest.mark.parametrize("partitions", [2, 4, 14])
def test_equal_content_can_have_different_file_counts(tmp_path, partitions):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    _build_run(baseline, retained=False)
    _build_run(candidate, retained=True, partitions=partitions)
    result = verify(baseline, candidate, retention_policy="canonical_counts")
    assert result["status"] == "ok"
    assert result["stage5"]["canonical_mentions.jsonl"]["files"] == partitions
    assert result["stage5_baseline"]["canonical_mentions.jsonl"]["files"] == 1


@pytest.mark.parametrize("rows", [[{"id": 1}, {"id": 1}], [{"id": 1}, {"id": 3}], [{"id": 1}]])
def test_repartitioning_does_not_hide_duplicates_missing_or_changed_rows(tmp_path, rows):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    _build_run(baseline, retained=False)
    _build_run(candidate, retained=True, partitions=2)
    paths = sorted(candidate.glob("units/*/stage456_sharded/shards/*/stage5/canonical_mentions.jsonl"))
    for index, path in enumerate(paths):
        _write_jsonl(path, rows[index::2])
    with pytest.raises(ValueError, match="Stage 5 canonical artifacts differ"):
        verify(baseline, candidate, retention_policy="canonical_counts")


def test_stage6_difference_still_blocks(tmp_path):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    _build_run(baseline, retained=False)
    _build_run(candidate, retained=True, partitions=2)
    (candidate / "stage6/objects.tsv").write_text("object\tcount\nthing\t2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Stage 6 TSV artifacts differ"):
        verify(baseline, candidate, retention_policy="canonical_counts")


def test_repartitioned_receipt_corruption_still_blocks(tmp_path):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    _build_run(baseline, retained=False)
    _build_run(candidate, retained=True, partitions=2)
    receipt_path = candidate / "receipts/unit_000000.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["artifacts"][0]["sha256"] = "0" * 64
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="retained artifact failed SHA validation"):
        verify(baseline, candidate, retention_policy="canonical_counts")
