from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "scripts", ROOT / "src"):
    sys.path.insert(0, str(path))

import fixed_lexicon_handoff as handoff
import run_fixed_lexicon_scaleout as runner


SETTINGS = {"model": "fixture", "batch_size": 192, "stage3_shards_per_gpu": 8,
            "stage3_disabled_components": ["ner"], "stage6_facts_output_mode": "discard"}


def write(path, value):
    runner._atomic_json(path, value)


def identity(value):
    value = deepcopy(value)
    value.pop("identity_sha256", None)
    value["identity_sha256"] = runner._json_sha256(value)
    return value


def finish_unit(root, unit, manifest):
    folder = root / "units" / unit.unit_id
    split = {"shard_count": 8, "split_policy": "contiguous_preserve_input_order"}
    write(folder / "mixed_pipeline_summary.jsonl", {
        "status": "completed", "stage1": {"total": unit.rows},
        "stage3_sharded": {"model": "fixture", "batch_size": 192,
                           "disabled_components": ["ner"], "sentence_split": split, "tag_split": split},
    })
    # Summary is JSONL, not pretty-printed JSON.
    path = folder / "mixed_pipeline_summary.jsonl"
    path.write_text(json.dumps(handoff._read(path)) + "\n", encoding="utf-8")
    write(folder / "pipeline_state.json", {})
    write(folder / "stage6/summary.jsonl", {})
    (folder / "stage6/objects.tsv").write_text("key\tcount\n" + unit.unit_id + "\t1\n", encoding="utf-8")
    for name in ("canonical_mentions.jsonl", "canonical_edges.jsonl"):
        write(folder / "stage456_sharded/shards/shard_0000/stage5" / name, {"id": unit.unit_id})
        path = folder / "stage456_sharded/shards/shard_0000/stage5" / name
        path.write_text(json.dumps(handoff._read(path)) + "\n", encoding="utf-8")
    receipt = runner.build_unit_receipt(unit, output_root=root,
        run_identity_sha256=manifest["identity_sha256"], gpu_id="0", elapsed_seconds=0,
        retention_policy="canonical_counts")
    receipt["retention"]["pruned_paths"] = ["stage1"]
    write(root / "receipts" / f"{unit.unit_id}.json", receipt)


@pytest.fixture
def case(tmp_path):
    shards = [runner.InputShard(f"shard_{i}", f"/input/{i}.jsonl", 1, 1, str(i) * 64)
              for i in range(2)]
    units = runner.build_work_units(shards, 1)

    def make(root, revision, selected, completed, legacy):
        semantics = deepcopy(SETTINGS)
        if legacy:
            semantics.pop("batch_size")
            semantics.pop("stage3_shards_per_gpu")
        manifest = identity({"kind": runner.RUN_KIND, "source_revision": revision,
            "input_manifest_sha256": runner._json_sha256([asdict(unit) for unit in selected]),
            "inventory_bundle": {"sha256": "fixed"}, "preposition_mwe_lexicon": {"sha256": "prep"},
            "retention_policy": "canonical_counts", "semantic_settings": semantics,
            "units": json.loads(json.dumps([asdict(unit) for unit in selected]))})
        write(root / "run_manifest.json", manifest)
        for unit in completed:
            finish_unit(root, unit, manifest)
        if len(completed) == len(selected):
            write(root / "COMPLETE.json", {"status": "completed", "identity_sha256": manifest["identity_sha256"]})
            (root / "stage6").mkdir()
            (root / "stage6/objects.tsv").write_text("key\tcount\nfixture\t1\n", encoding="utf-8")
        return manifest

    source, baseline, candidate = (tmp_path / name for name in ("source", "baseline", "candidate"))
    source_manifest = make(source, "old", units, units[:1], True)
    make(baseline, "old", units[:1], units[:1], True)
    make(candidate, "new", units[:1], units[:1], False)
    return SimpleNamespace(source=source, baseline=baseline, candidate=candidate, units=units,
                           source_manifest=source_manifest, target=tmp_path / "target",
                           plan_path=tmp_path / "handoff.json")


def prepare(case):
    return handoff.prepare(case.source, "old", case.baseline, case.candidate, "new", case.plan_path)


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_handoff_reuses_only_completed_units_without_mutating_source(case):
    before = snapshot(case.source)
    plan = prepare(case)
    assert len(plan["units"]) == 1 and plan["reused_rows"] == 1
    resolved, meta = handoff.resolve(case.plan_path, plan["target_identity"], case.target)
    assert resolved == {"unit_000000": case.source / "units/unit_000000"}
    assert meta["source_revision"] == "old"
    assert plan["target_identity"]["semantic_settings"] == SETTINGS
    assert snapshot(case.source) == before
    assert prepare(case) == plan


@pytest.mark.parametrize("change", ["code", "batch", "grouping", "input", "inventory"])
def test_rejects_changed_target_contract(case, change):
    plan = prepare(case)
    target = deepcopy(plan["target_identity"])
    if change == "code":
        target["source_revision"] = "different"
    elif change == "batch":
        target["semantic_settings"]["batch_size"] = 1
    elif change == "grouping":
        target["units"].reverse()
    elif change == "input":
        target["input_manifest_sha256"] = "different"
    else:
        target["inventory_bundle"] = {"sha256": "different"}
    with pytest.raises(ValueError, match="target identity mismatch"):
        handoff.resolve(case.plan_path, identity(target), case.target)


@pytest.mark.parametrize("change", ["artifact", "receipt", "source_manifest", "smoke_manifest",
                                    "duplicate", "missing_proof", "overlap", "nested_root"])
def test_rejects_changed_or_unsafe_handoff(case, change):
    plan = prepare(case)
    if change == "artifact":
        path = case.source / "units/unit_000000/stage6/objects.tsv"
        path.write_text(path.read_text().replace("\t1", "\t9"), encoding="utf-8")
    elif change == "receipt":
        write(case.source / "receipts/unit_000000.json", {})
    elif change == "source_manifest":
        write(case.source / "run_manifest.json", {})
    elif change == "smoke_manifest":
        write(case.candidate / "run_manifest.json", {})
    elif change == "duplicate":
        plan["units"].append(plan["units"][0])
        write(case.plan_path, plan)
    elif change == "missing_proof":
        plan.pop("smoke_verification")
        write(case.plan_path, plan)
    elif change == "overlap":
        write(case.target / "receipts/unit_000000.json", {})
    else:
        case.target = case.source / "nested"
    with pytest.raises(ValueError):
        handoff.resolve(case.plan_path, plan["target_identity"], case.target)


def test_plan_cannot_change_inventory_even_when_target_plan_matches(case):
    plan = prepare(case)
    plan["target_identity"]["inventory_bundle"] = {"sha256": "different"}
    plan["target_identity"] = identity(plan["target_identity"])
    write(case.plan_path, plan)
    with pytest.raises(ValueError, match="preserve source inputs"):
        handoff.resolve(case.plan_path, plan["target_identity"], case.target)


@pytest.mark.parametrize("change", ["code", "input", "semantics", "graph", "counts", "summary_coverage"])
def test_prepare_requires_real_matching_smoke_and_legacy_summary_evidence(case, change):
    if change in {"code", "input", "semantics"}:
        manifest = handoff._read(case.candidate / "run_manifest.json")
        if change == "code":
            manifest["source_revision"] = "wrong"
        elif change == "input":
            manifest["input_manifest_sha256"] = "wrong"
        else:
            manifest["semantic_settings"]["batch_size"] = 1
        write(case.candidate / "run_manifest.json", identity(manifest))
    elif change == "counts":
        (case.candidate / "stage6/objects.tsv").write_text("different", encoding="utf-8")
    elif change == "graph":
        (case.candidate / "units/unit_000000/stage456_sharded/shards/shard_0000/stage5/canonical_mentions.jsonl").write_text('{}\n')
    else:
        path = case.source / "receipts/unit_000000.json"
        receipt = handoff._read(path)
        receipt["artifacts"] = [row for row in receipt["artifacts"] if not row["path"].endswith("mixed_pipeline_summary.jsonl")]
        write(path, receipt)
    with pytest.raises(ValueError):
        prepare(case)


def test_merge_uses_old_and_new_units_once_and_preserves_lineage(case, monkeypatch):
    plan = prepare(case)
    reused, meta = handoff.resolve(case.plan_path, plan["target_identity"], case.target)
    combined_identity = identity({**plan["target_identity"], "verified_unit_handoff": meta})
    args = runner.build_parser().parse_args([
        "--input-manifest", "unused.json", "--output-root", str(case.target), "--gpus", "0,1",
        "--inventory-bundle", "unused.json", "--preposition-mwe-lexicon", "unused.tsv",
        "--retention-policy", "canonical_counts", "--reuse-verified-units", str(case.plan_path)])
    bundle = SimpleNamespace(object_inventory="unused", attribute_inventory="unused",
                             action_inventory="unused", lexicon_dir="unused")
    invoked = []

    def run_units(units, **kwargs):
        for unit in units:
            if unit.unit_id not in kwargs["completed"]:
                invoked.append(unit.unit_id)
                finish_unit(case.target, unit, combined_identity)
                kwargs["completed"].add(unit.unit_id)
        return False

    roots_seen = []
    def merge(roots, output, **kwargs):
        roots_seen.extend(roots)
        output.mkdir()
        rows = [line for root in roots for line in (root / "objects.tsv").read_text().splitlines()[1:]]
        (output / "objects.tsv").write_text("key\tcount\n" + "\n".join(rows) + "\n")
        return {"rows": len(rows)}

    monkeypatch.setattr(runner, "_run_units", run_units)
    monkeypatch.setattr(runner, "merge_stage6_count_dirs", merge)
    monkeypatch.setattr(runner, "child_memory_kwargs", lambda *a: {})
    before = snapshot(case.source)
    result = runner._run_prepared(args, case.target, case.units, bundle, Path("unused.json"),
                                 Path("unused.tsv"), combined_identity, None, reused)
    assert invoked == ["unit_000001"]
    assert roots_seen == [case.source / "units/unit_000000/stage6", case.target / "units/unit_000001/stage6"]
    assert result["input_rows"] == 2 and result["stage6"]["rows"] == 2
    assert result["unit_sources"]["unit_000000"]["source_revision"] == "old"
    assert result["unit_sources"]["unit_000001"]["source_revision"] == "new"
    assert result["stage5_roots"][0].startswith(str(case.source))
    assert snapshot(case.source) == before

