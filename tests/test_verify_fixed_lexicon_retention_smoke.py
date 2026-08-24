from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


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


def _build_run(root: Path, *, retained: bool, changed: bool = False) -> None:
    stage5 = root / "units" / "unit_000000" / "stage456_sharded" / "shards" / "shard_0000" / "stage5"
    mentions = [{"id": 2}, {"id": 1}]
    if changed:
        mentions.append({"id": 3})
    _write_jsonl(stage5 / "canonical_mentions.jsonl", mentions)
    _write_jsonl(stage5 / "canonical_edges.jsonl", [{"edge": "a"}])
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
    for path in [
        stage5 / "canonical_mentions.jsonl",
        stage5 / "canonical_edges.jsonl",
        unit_stage6,
    ]:
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
