from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


RECEIPT_KIND = "gpic-fixed-lexicon-unit-receipt-v2"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_jsonl_digest(paths: Iterable[Path]) -> dict[str, Any]:
    rows: list[str] = []
    source_files = 0
    for path in sorted(paths):
        source_files += 1
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(
                    json.dumps(
                        json.loads(line),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
    rows.sort()
    digest = hashlib.sha256()
    for row in rows:
        digest.update(row.encode("utf-8"))
        digest.update(b"\n")
    return {"files": source_files, "rows": len(rows), "sha256": digest.hexdigest()}


def _stage5_digest(root: Path, filename: str) -> dict[str, Any]:
    paths = list(
        (root / "units").glob(
            f"unit_*/stage456_sharded/shards/shard_*/stage5/{filename}"
        )
    )
    if not paths:
        raise ValueError(f"no Stage 5 {filename} files found under {root}")
    return _canonical_jsonl_digest(paths)


def _stage6_tsv_records(root: Path) -> dict[str, dict[str, Any]]:
    stage6 = root / "stage6"
    records = {
        path.name: {"size_bytes": path.stat().st_size, "sha256": _sha256_file(path)}
        for path in sorted(stage6.glob("*.tsv"))
    }
    if not records:
        raise ValueError(f"no global Stage 6 TSV files found under {root}")
    return records


def _artifact_is_valid(artifact: dict[str, Any], *, output_root: Path) -> bool:
    resolved_root = output_root.resolve()
    try:
        path = (output_root / artifact["path"]).resolve()
        if path != resolved_root and resolved_root not in path.parents:
            return False
        return (
            path.is_file()
            and path.stat().st_size == int(artifact["size_bytes"])
            and _sha256_file(path) == artifact["sha256"]
        )
    except (KeyError, OSError, TypeError, ValueError):
        return False


def _verify_retention(candidate: Path, expected_policy: str) -> dict[str, Any]:
    receipts = sorted((candidate / "receipts").glob("unit_*.json"))
    if not receipts:
        raise ValueError("candidate has no unit receipts")
    reclaimed_bytes = 0
    pruned_count = 0
    for receipt_path in receipts:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("kind") != RECEIPT_KIND:
            raise ValueError(f"unexpected receipt kind: {receipt_path}")
        retention = receipt.get("retention", {})
        if retention.get("policy") != expected_policy:
            raise ValueError(f"retention policy mismatch: {receipt_path}")
        pruned_paths = retention.get("pruned_paths")
        if not isinstance(pruned_paths, list) or not pruned_paths:
            raise ValueError(f"receipt has no recorded pruned paths: {receipt_path}")
        unit_id = receipt.get("unit", {}).get("unit_id")
        unit_dir = candidate / "units" / str(unit_id)
        for relative in pruned_paths:
            path = (unit_dir / str(relative)).resolve()
            if unit_dir.resolve() not in path.parents or path.exists():
                raise ValueError(f"recorded pruned path is unsafe or still exists: {path}")
        artifacts = receipt.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise ValueError(f"receipt has no retained artifacts: {receipt_path}")
        if not all(_artifact_is_valid(row, output_root=candidate) for row in artifacts):
            raise ValueError(f"receipt retained artifact failed SHA validation: {receipt_path}")
        reclaimed_bytes += int(retention.get("reclaimed_bytes") or 0)
        pruned_count += len(pruned_paths)

    forbidden = [
        "units/unit_*/stage1",
        "units/unit_*/stage3",
        "units/unit_*/stage3_sharded",
        "units/unit_*/stage4",
        "units/unit_*/stage456_sharded/stage3_shards",
        "units/unit_*/stage456_sharded/stage6",
        "units/unit_*/stage456_sharded/stage6_merged",
        "units/unit_*/stage456_sharded/shards/shard_*/stage4",
        "units/unit_*/stage456_sharded/shards/shard_*/stage6",
    ]
    leftovers = [str(path) for pattern in forbidden for path in candidate.glob(pattern)]
    if leftovers:
        raise ValueError("prunable intermediates remain: " + json.dumps(leftovers))
    return {
        "receipts": len(receipts),
        "pruned_paths": pruned_count,
        "reclaimed_bytes": reclaimed_bytes,
    }


def verify(baseline: Path, candidate: Path, *, retention_policy: str) -> dict[str, Any]:
    complete_path = candidate / "COMPLETE.json"
    if not complete_path.exists():
        raise ValueError(f"candidate is not complete: {complete_path}")
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    if complete.get("status") != "completed":
        raise ValueError("candidate COMPLETE.json is not completed")

    baseline_stage5 = {
        name: _stage5_digest(baseline, name)
        for name in ("canonical_mentions.jsonl", "canonical_edges.jsonl")
    }
    candidate_stage5 = {
        name: _stage5_digest(candidate, name)
        for name in ("canonical_mentions.jsonl", "canonical_edges.jsonl")
    }
    # File counts describe worker partitioning, not the extracted row multiset.
    if any(
        (candidate_stage5[name]["rows"], candidate_stage5[name]["sha256"])
        != (baseline_stage5[name]["rows"], baseline_stage5[name]["sha256"])
        for name in baseline_stage5
    ):
        raise ValueError(
            "Stage 5 canonical artifacts differ: "
            + json.dumps(
                {"baseline": baseline_stage5, "candidate": candidate_stage5},
                sort_keys=True,
            )
        )

    baseline_stage6 = _stage6_tsv_records(baseline)
    candidate_stage6 = _stage6_tsv_records(candidate)
    if candidate_stage6 != baseline_stage6:
        raise ValueError("global Stage 6 TSV artifacts differ from baseline")

    return {
        "kind": "gpic-fixed-lexicon-retention-smoke-verification-v1",
        "status": "ok",
        "baseline": str(baseline.resolve()),
        "candidate": str(candidate.resolve()),
        "retention": _verify_retention(candidate, retention_policy),
        "stage5": candidate_stage5,
        "stage5_baseline": baseline_stage5,
        "stage6": {"files": len(candidate_stage6), "artifacts": candidate_stage6},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify a retained fixed-lexicon smoke against a full baseline."
    )
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--retention-policy", default="canonical_counts")
    parser.add_argument("--output")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = verify(
        Path(args.baseline).resolve(),
        Path(args.candidate).resolve(),
        retention_policy=args.retention_policy,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
