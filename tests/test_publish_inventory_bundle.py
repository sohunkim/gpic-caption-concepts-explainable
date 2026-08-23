from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "publish_inventory_bundle.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("publish_inventory_bundle", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


publish = _load_module()


class PublishInventoryBundleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = ROOT / ".tmp_tests" / self.id().replace(".", "_")
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)

    def tearDown(self) -> None:
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_publish_bundle_copies_current_files_and_repoints_manifest(self) -> None:
        source = self.tmp / "source"
        target = self.tmp / "resources" / "gpic_inventory" / "current"
        source.mkdir()
        lexicons = source / "lexicons"
        lexicons.mkdir()
        object_inventory = source / "object.tsv"
        attribute_inventory = source / "attribute.tsv"
        action_inventory = source / "action.tsv"
        action_canonical_inventory = source / "action_canonical.tsv"
        _write_tsv(object_inventory, [{"span_key": "dog", "decision_status": "chosen"}])
        _write_tsv(attribute_inventory, [{"span_key": "black", "decision_status": "chosen"}])
        _write_tsv(action_inventory, [{"span_key": "run", "decision_status": "chosen"}])
        _write_tsv(action_canonical_inventory, [{"span_key": "run", "canonical_surface": "run"}])
        (source / "action.tsv.pipeline_state.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "artifact_type": "gpic_observed_action_inventory",
                    "preview_mode": False,
                    "action_inventory_preposition_mwe_aware": True,
                    "preposition_mwe_detection_before_action": True,
                    "output": str(action_inventory),
                }
            ),
            encoding="utf-8",
        )
        _write_lexicon_state(
            lexicons,
            attribute_inventory=attribute_inventory,
            action_canonical_inventory=action_canonical_inventory,
        )
        (lexicons / "attribute_synonyms.tsv").write_text(
            "source_label\tcanonical_label\nblack\tblack\n",
            encoding="utf-8",
        )
        bundle = source / "inventory_bundle.json"
        bundle.write_text(
            json.dumps(
                {
                    "artifact_type": "gpic_inventory_bundle",
                    "status": "complete",
                    "object_inventory": str(object_inventory),
                    "attribute_inventory": str(attribute_inventory),
                    "action_inventory": str(action_inventory),
                    "action_canonical_inventory": str(action_canonical_inventory),
                    "lexicon_dir": str(lexicons),
                }
            ),
            encoding="utf-8",
        )

        summary = publish.publish_inventory_bundle(
            source_bundle=bundle,
            target_dir=target,
            snapshot_label="front100",
            source_stage3_records="outputs/front100/stage3/stage3_records.jsonl",
        )

        central_bundle = target / "inventory_bundle.json"
        self.assertEqual(summary["status"], "published")
        self.assertEqual(summary["rows"]["object_inventory"], 1)
        data = json.loads(central_bundle.read_text(encoding="utf-8"))
        self.assertEqual(data["snapshot_label"], "front100")
        self.assertEqual(data["path_base"], "bundle_dir")
        self.assertEqual(data["object_inventory"], "inventory/object_inventory.tsv")
        self.assertEqual(data["attribute_inventory"], "inventory/attribute_inventory.tsv")
        self.assertEqual(data["action_inventory"], "inventory/action_inventory.tsv")
        self.assertEqual(data["lexicon_dir"], "lexicons")
        self.assertEqual(data["inventory_rows"]["attribute_inventory"], 1)
        self.assertNotIn(str(source), data["object_inventory"])
        self.assertTrue((target / "lexicons" / "attribute_synonyms.tsv").exists())
        self.assertTrue((target / "publish_summary.json").is_file())
        lexicon_state = json.loads(
            (target / "lexicons" / "pipeline_state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            lexicon_state["attribute_inventory"],
            "../inventory/attribute_inventory.tsv",
        )
        self.assertEqual(lexicon_state["path_base"], "lexicon_dir")
        self.assertEqual(lexicon_state["output_dir"], ".")
        action_state = json.loads(
            (target / "inventory" / "action_inventory.tsv.pipeline_state.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(action_state["output"], str(target / "inventory" / "action_inventory.tsv"))

    def test_publish_rejects_current_bundle_as_its_own_source(self) -> None:
        target = self.tmp / "resources" / "gpic_inventory" / "current"
        inventory = target / "inventory"
        lexicons = target / "lexicons"
        inventory.mkdir(parents=True)
        lexicons.mkdir()
        object_inventory = inventory / "object_inventory.tsv"
        attribute_inventory = inventory / "attribute_inventory.tsv"
        action_inventory = inventory / "action_inventory.tsv"
        _write_tsv(object_inventory, [{"span_key": "dog"}])
        _write_tsv(attribute_inventory, [{"span_key": "black"}])
        _write_tsv(action_inventory, [{"span_key": "run"}])
        bundle = target / "inventory_bundle.json"
        bundle.write_text(
            json.dumps(
                {
                    "artifact_type": "gpic_inventory_bundle",
                    "status": "complete",
                    "path_base": "bundle_dir",
                    "object_inventory": "inventory/object_inventory.tsv",
                    "attribute_inventory": "inventory/attribute_inventory.tsv",
                    "action_inventory": "inventory/action_inventory.tsv",
                    "lexicon_dir": "lexicons",
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "refuse_in_place_bundle_publish"):
            publish.publish_inventory_bundle(source_bundle=bundle, target_dir=target)

        self.assertTrue(bundle.is_file())
        self.assertTrue(lexicons.is_dir())

    def test_publish_replaces_stale_lexicon_tree(self) -> None:
        source = self.tmp / "source"
        target = self.tmp / "resources" / "gpic_inventory" / "current"
        lexicons = source / "lexicons"
        lexicons.mkdir(parents=True)
        (target / "lexicons").mkdir(parents=True)
        (target / "lexicons" / "stale.tsv").write_text("old\n", encoding="utf-8")
        object_inventory = source / "object.tsv"
        attribute_inventory = source / "attribute.tsv"
        action_inventory = source / "action.tsv"
        action_canonical_inventory = source / "action_canonical.tsv"
        _write_tsv(object_inventory, [{"span_key": "dog"}])
        _write_tsv(attribute_inventory, [{"span_key": "black"}])
        _write_tsv(action_inventory, [{"span_key": "run"}])
        _write_tsv(action_canonical_inventory, [{"span_key": "run"}])
        (source / "action.tsv.pipeline_state.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "artifact_type": "gpic_observed_action_inventory",
                    "preview_mode": False,
                    "action_inventory_preposition_mwe_aware": True,
                    "preposition_mwe_detection_before_action": True,
                    "output": str(action_inventory),
                }
            ),
            encoding="utf-8",
        )
        _write_lexicon_state(
            lexicons,
            attribute_inventory=attribute_inventory,
            action_canonical_inventory=action_canonical_inventory,
        )
        bundle = source / "inventory_bundle.json"
        bundle.write_text(
            json.dumps(
                {
                    "artifact_type": "gpic_inventory_bundle",
                    "status": "complete",
                    "object_inventory": str(object_inventory),
                    "attribute_inventory": str(attribute_inventory),
                    "action_inventory": str(action_inventory),
                    "action_canonical_inventory": str(action_canonical_inventory),
                    "lexicon_dir": str(lexicons),
                }
            ),
            encoding="utf-8",
        )

        publish.publish_inventory_bundle(source_bundle=bundle, target_dir=target)

        self.assertFalse((target / "lexicons" / "stale.tsv").exists())
        self.assertTrue((target / "lexicons" / "pipeline_state.json").exists())

    def test_publish_requires_action_inventory_sidecar(self) -> None:
        source = self.tmp / "source"
        target = self.tmp / "resources" / "gpic_inventory" / "current"
        lexicons = source / "lexicons"
        lexicons.mkdir(parents=True)
        object_inventory = source / "object.tsv"
        attribute_inventory = source / "attribute.tsv"
        action_inventory = source / "action.tsv"
        _write_tsv(object_inventory, [{"span_key": "dog"}])
        _write_tsv(attribute_inventory, [{"span_key": "black"}])
        _write_tsv(action_inventory, [{"span_key": "run"}])
        (lexicons / "pipeline_state.json").write_text("{}\n", encoding="utf-8")
        bundle = source / "inventory_bundle.json"
        bundle.write_text(
            json.dumps(
                {
                    "artifact_type": "gpic_inventory_bundle",
                    "status": "complete",
                    "object_inventory": str(object_inventory),
                    "attribute_inventory": str(attribute_inventory),
                    "action_inventory": str(action_inventory),
                    "lexicon_dir": str(lexicons),
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(FileNotFoundError, "missing_action_inventory_pipeline_state"):
            publish.publish_inventory_bundle(source_bundle=bundle, target_dir=target)

    def test_replace_tree_restores_existing_tree_when_swap_fails(self) -> None:
        source = self.tmp / "source_lexicons"
        target = self.tmp / "target_lexicons"
        source.mkdir()
        target.mkdir()
        (source / "new.tsv").write_text("new\n", encoding="utf-8")
        (target / "old.tsv").write_text("old\n", encoding="utf-8")
        real_replace = publish.os.replace
        calls = 0

        def fail_second_replace(left, right):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated swap failure")
            return real_replace(left, right)

        with patch.object(publish.os, "replace", side_effect=fail_second_replace):
            with self.assertRaisesRegex(OSError, "simulated swap failure"):
                publish._replace_tree(source, target)

        self.assertTrue((target / "old.tsv").is_file())
        self.assertFalse((target / "new.tsv").exists())


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_lexicon_state(
    lexicon_dir: Path,
    *,
    attribute_inventory: Path,
    action_canonical_inventory: Path,
) -> None:
    (lexicon_dir / "pipeline_state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_type": "stage5_lexicon_bundle",
                "stage": "5",
                "status": "ready",
                "preview_mode": False,
                "attribute_inventory": str(attribute_inventory),
                "action_canonical_inventory": str(action_canonical_inventory),
                "output_dir": str(lexicon_dir),
                "action_canonical_exported": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
