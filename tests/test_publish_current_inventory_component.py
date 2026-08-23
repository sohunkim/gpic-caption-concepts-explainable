from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "publish_current_inventory_component.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("publish_current_inventory_component", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


publish_component = _load_module()


class PublishCurrentInventoryComponentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = ROOT / ".tmp_tests" / self.id().replace(".", "_")
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)

    def tearDown(self) -> None:
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_object_component_publish_preserves_other_current_components(self) -> None:
        current = self.tmp / "current"
        inventory_dir = current / "inventory"
        lexicon_dir = current / "lexicons"
        inventory_dir.mkdir(parents=True)
        lexicon_dir.mkdir()
        old_object = inventory_dir / "object_inventory.tsv"
        old_attribute = inventory_dir / "attribute_inventory.tsv"
        old_action = inventory_dir / "action_inventory.tsv"
        _write_tsv(old_object, [{"span_key": "old", "decision_status": "chosen"}])
        _write_tsv(old_attribute, [{"span_key": "black", "decision_status": "chosen"}])
        _write_tsv(old_action, [{"span_key": "run", "decision_status": "chosen"}])
        (current / "inventory_bundle.json").write_text(
            json.dumps(
                {
                    "artifact_type": "gpic_inventory_bundle",
                    "status": "complete",
                    "object_inventory": str(old_object),
                    "attribute_inventory": str(old_attribute),
                    "action_inventory": str(old_action),
                    "lexicon_dir": str(lexicon_dir),
                    "snapshot_label": "front200k",
                }
            ),
            encoding="utf-8",
        )
        new_object = self.tmp / "new_object.tsv"
        _write_tsv(
            new_object,
            [
                {
                    "span_key": "car",
                    "observed_surface": "car",
                    "decision_status": "chosen",
                    "selected_oewn_synset": "oewn-car-n",
                    "canonical_surface": "car",
                }
            ],
        )

        summary = publish_component.publish_current_inventory_component(
            component="object",
            source=new_object,
            target_dir=current,
            snapshot_label="front1m_object",
            source_stage3_records="outputs/front1m/stage3/stage3_records.jsonl",
        )

        bundle = json.loads((current / "inventory_bundle.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "published_component")
        self.assertEqual(summary["component"], "object")
        self.assertEqual(bundle["path_base"], "bundle_dir")
        self.assertEqual(bundle["object_inventory"], "inventory/object_inventory.tsv")
        self.assertEqual(bundle["attribute_inventory"], "inventory/attribute_inventory.tsv")
        self.assertEqual(bundle["action_inventory"], "inventory/action_inventory.tsv")
        self.assertEqual(bundle["component_sources"]["object"]["snapshot_label"], "front1m_object")
        self.assertEqual(bundle["inventory_rows"]["object_inventory"], 1)
        self.assertEqual(bundle["inventory_rows"]["attribute_inventory"], 1)
        self.assertTrue((current / "publish_summary.json").is_file())
        self.assertEqual(_read_tsv(old_object)[0]["span_key"], "car")

    def test_non_object_component_requires_complete_bundle_publish(self) -> None:
        source = self.tmp / "attribute.tsv"
        _write_tsv(source, [{"span_key": "blue", "decision_status": "chosen"}])

        with self.assertRaisesRegex(
            ValueError,
            "component_requires_synchronized_lexicon_publish",
        ):
            publish_component.publish_current_inventory_component(
                component="attribute",
                source=source,
                target_dir=self.tmp / "current",
            )


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


if __name__ == "__main__":
    unittest.main()
