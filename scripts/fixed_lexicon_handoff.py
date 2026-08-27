"""Explicit, smoke-verified reuse of completed units across code revisions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "src", ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from gpic_concepts_v1.atomic_io import atomic_text_writer

KIND = "gpic-fixed-lexicon-verified-unit-handoff-v1"


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _manifest(root: Path) -> dict:
    from run_fixed_lexicon_scaleout import RUN_KIND, _json_sha256
    value = _read(root / "run_manifest.json")
    payload = {key: item for key, item in value.items() if key != "identity_sha256"}
    if value.get("kind") != RUN_KIND or value.get("identity_sha256") != _json_sha256(payload):
        raise ValueError(f"invalid source run identity: {root}")
    if value.get("verified_unit_handoff"):
        raise ValueError("chained cross-version handoffs require a separate provenance review")
    return value


def _unit(raw: dict):
    from run_fixed_lexicon_scaleout import InputShard, WorkUnit
    return WorkUnit(raw["unit_id"], tuple(InputShard(**row) for row in raw["shards"]), raw["rows"])


def _unit_semantics(root: Path, unit, expected: dict) -> None:
    path = root / "units" / unit.unit_id / "mixed_pipeline_summary.jsonl"
    summaries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(summaries) != 1 or summaries[0].get("status") != "completed":
        raise ValueError(f"incomplete unit summary: {unit.unit_id}")
    summary = summaries[0]
    stage3 = summary.get("stage3_sharded") or {}
    if int(summary.get("stage1", {}).get("total", 0)) != unit.rows:
        raise ValueError(f"unit caption count mismatch: {unit.unit_id}")
    observed = {
        "model": stage3.get("model"),
        "batch_size": stage3.get("batch_size"),
        "stage3_disabled_components": stage3.get("disabled_components"),
    }
    for key, value in observed.items():
        if value != expected[key]:
            raise ValueError(f"unit semantic mismatch: {unit.unit_id}: {key}")
    for shape in ("sentence_split", "tag_split"):
        split = stage3.get(shape, {})
        if (split.get("shard_count") != expected["stage3_shards_per_gpu"]
                or split.get("split_policy") != "contiguous_preserve_input_order"):
            raise ValueError(f"unit grouping mismatch: {unit.unit_id}: {shape}")


def _verify_unit(root: Path, unit, manifest: dict, semantics: dict) -> None:
    from run_fixed_lexicon_scaleout import unit_receipt_is_valid, _unit_artifact_paths
    if not unit_receipt_is_valid(
        unit, output_root=root, run_identity_sha256=manifest["identity_sha256"],
        verify_hashes=True, retention_policy=manifest["retention_policy"],
    ):
        raise ValueError(f"source receipt/artifact verification failed: {unit.unit_id}")
    receipt = _read(root / "receipts" / f"{unit.unit_id}.json")
    expected = {path.relative_to(root).as_posix()
                for path in _unit_artifact_paths(root / "units" / unit.unit_id)}
    if {row["path"] for row in receipt["artifacts"]} != expected:
        raise ValueError(f"source receipt artifact coverage mismatch: {unit.unit_id}")
    _unit_semantics(root, unit, semantics)


def _target_identity(source: dict, revision: str, semantics: dict) -> dict:
    from run_fixed_lexicon_scaleout import _json_sha256
    for key, value in source["semantic_settings"].items():
        if semantics.get(key) != value:
            raise ValueError(f"formal source semantic mismatch: {key}")
    target = {**source, "source_revision": revision, "semantic_settings": semantics}
    target.pop("identity_sha256")
    target["identity_sha256"] = _json_sha256(target)
    return target


def prepare(source_root: Path, source_revision: str, baseline: Path, candidate: Path,
            target_revision: str, output: Path) -> dict:
    from run_fixed_lexicon_scaleout import _sha256_file
    from verify_fixed_lexicon_retention_smoke import verify

    source_root, baseline, candidate = (path.resolve() for path in (source_root, baseline, candidate))
    source = _manifest(source_root)
    if source["retention_policy"] != "canonical_counts":
        raise ValueError("verified handoff currently requires canonical_counts retention")
    old_smoke, new_smoke = _manifest(baseline), _manifest(candidate)
    if source["source_revision"] != source_revision or old_smoke["source_revision"] != source_revision:
        raise ValueError("source and baseline smoke code revisions must match the approved source revision")
    if new_smoke["source_revision"] != target_revision:
        raise ValueError("candidate smoke was not produced by the target code revision")
    if (old_smoke["input_manifest_sha256"] != new_smoke["input_manifest_sha256"]
            or old_smoke["units"] != new_smoke["units"]):
        raise ValueError("smoke input/grouping mismatch")
    semantics = new_smoke["semantic_settings"]
    for key in ("model", "batch_size", "stage3_shards_per_gpu", "stage3_disabled_components", "stage6_facts_output_mode"):
        if key not in semantics:
            raise ValueError(f"target smoke lacks semantic identity: {key}")
    for name, other in (("baseline", old_smoke), ("candidate", new_smoke)):
        for field in ("inventory_bundle", "preposition_mwe_lexicon", "retention_policy"):
            if other[field] != source[field]:
                raise ValueError(f"{name} smoke differs from the formal source: {field}")
        for key, value in other["semantic_settings"].items():
            if semantics.get(key) != value:
                raise ValueError(f"{name} smoke semantic mismatch: {key}")
        root = baseline if name == "baseline" else candidate
        complete = _read(root / "COMPLETE.json")
        if complete.get("identity_sha256") != other["identity_sha256"] or complete.get("status") != "completed":
            raise ValueError(f"{name} smoke completion identity mismatch")
        for raw in other["units"]:
            _verify_unit(root, _unit(raw), other, semantics)
    target = _target_identity(source, target_revision, semantics)
    smoke = verify(baseline, candidate, retention_policy=source["retention_policy"])

    entries = []
    known = {raw["unit_id"] for raw in source["units"]}
    for receipt_path in sorted((source_root / "receipts").glob("unit_*.json")):
        if receipt_path.stem not in known:
            raise ValueError(f"unexpected source receipt: {receipt_path.name}")
    for raw in source["units"]:
        unit = _unit(raw)
        receipt_path = source_root / "receipts" / f"{unit.unit_id}.json"
        if not receipt_path.exists():
            continue
        _verify_unit(source_root, unit, source, semantics)
        entries.append({"unit_id": unit.unit_id, "rows": unit.rows,
                        "receipt_sha256": _sha256_file(receipt_path)})
        print(json.dumps({"phase": "verified_source_unit", "unit_id": unit.unit_id,
                          "verified_units": len(entries)}, sort_keys=True), flush=True)
    if not entries:
        raise ValueError("no verified completed units to hand off")

    plan = {
        "kind": KIND, "source_root": str(source_root),
        "source_manifest_sha256": _sha256_file(source_root / "run_manifest.json"),
        "source_identity_sha256": source["identity_sha256"],
        "source_revision": source_revision, "target_identity": target,
        "smoke_verification": smoke,
        "smoke_manifests": {name: {"path": str(root / "run_manifest.json"),
                                  "sha256": _sha256_file(root / "run_manifest.json")}
                            for name, root in (("baseline", baseline), ("candidate", candidate))},
        "units": entries,
        "reused_rows": sum(entry["rows"] for entry in entries),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if _read(output) != plan:
            raise ValueError("handoff plan already exists with different content; use a new path")
    else:
        with atomic_text_writer(output) as handle:
            json.dump(plan, handle, sort_keys=True, indent=2)
            handle.write("\n")
    return plan


def resolve(plan_path: Path, target_identity: dict, target_root: Path) -> tuple[dict, dict]:
    from run_fixed_lexicon_scaleout import _sha256_file
    plan = _read(plan_path)
    if plan.get("kind") != KIND or plan.get("target_identity") != target_identity:
        raise ValueError("handoff target identity mismatch; inputs, code or grouping changed")
    source_root = Path(plan["source_root"]).resolve()
    target_root = target_root.resolve()
    if source_root == target_root or source_root in target_root.parents or target_root in source_root.parents:
        raise ValueError("handoff requires separate, non-nested output roots")
    if _sha256_file(source_root / "run_manifest.json") != plan["source_manifest_sha256"]:
        raise ValueError("handoff source manifest changed")
    source = _manifest(source_root)
    if (source["identity_sha256"] != plan["source_identity_sha256"]
            or source["source_revision"] != plan["source_revision"]):
        raise ValueError("handoff source provenance mismatch")
    if _target_identity(source, target_identity["source_revision"], target_identity["semantic_settings"]) != target_identity:
        raise ValueError("handoff must preserve source inputs, inventory and grouping")
    if plan.get("smoke_verification", {}).get("status") != "ok":
        raise ValueError("handoff requires a successful smoke comparison")
    for name in ("baseline", "candidate"):
        record = plan["smoke_manifests"][name]
        path = Path(record["path"])
        if _sha256_file(path) != record["sha256"]:
            raise ValueError(f"handoff smoke manifest changed: {name}")
        smoke_manifest = _manifest(path.parent)
        revision = source["source_revision"] if name == "baseline" else target_identity["source_revision"]
        if smoke_manifest["source_revision"] != revision:
            raise ValueError(f"handoff smoke revision mismatch: {name}")
    by_id = {raw["unit_id"]: _unit(raw) for raw in target_identity["units"]}
    resolved = {}
    for entry in plan["units"]:
        unit_id = entry["unit_id"]
        if unit_id in resolved or unit_id not in by_id or entry["rows"] != by_id[unit_id].rows:
            raise ValueError("duplicate, unknown or changed handoff unit")
        receipt_path = source_root / "receipts" / f"{unit_id}.json"
        if _sha256_file(receipt_path) != entry["receipt_sha256"]:
            raise ValueError(f"handoff source receipt changed: {unit_id}")
        if (target_root / "receipts" / f"{unit_id}.json").exists():
            raise ValueError(f"local and imported receipts overlap: {unit_id}")
        _verify_unit(source_root, by_id[unit_id], source, target_identity["semantic_settings"])
        resolved[unit_id] = source_root / "units" / unit_id
        print(json.dumps({"phase": "verified_reused_unit", "unit_id": unit_id,
                          "verified_units": len(resolved)}, sort_keys=True), flush=True)
    if not resolved or plan["reused_rows"] != sum(by_id[key].rows for key in resolved):
        raise ValueError("handoff row coverage mismatch")
    return resolved, {"path": str(plan_path.resolve()), "sha256": _sha256_file(plan_path),
                      "source_root": str(source_root), "source_revision": source["source_revision"],
                      "source_identity_sha256": source["identity_sha256"],
                      "reused_units": len(resolved), "reused_rows": plan["reused_rows"]}


def main() -> None:
    from run_fixed_lexicon_scaleout import source_revision
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--baseline-smoke", type=Path, required=True)
    parser.add_argument("--candidate-smoke", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = prepare(args.source_root, args.source_revision, args.baseline_smoke,
                   args.candidate_smoke, source_revision(ROOT), args.output)
    print(json.dumps({"status": "ready", "reused_rows": plan["reused_rows"],
                      "reused_units": len(plan["units"]), "output": str(args.output)}, sort_keys=True))


if __name__ == "__main__":
    from incident_gate import guarded_entrypoint
    raise SystemExit(guarded_entrypoint("fixed_lexicon_handoff_prepare", main))
