from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "refresh_current_inventory_metadata.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("refresh_current_inventory_metadata", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


refresh = _load_module()


class RefreshCurrentInventoryMetadataTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = ROOT / ".tmp_tests" / self.id().replace(".", "_")
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)

    def tearDown(self) -> None:
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_refreshes_counts_summary_and_lexicon_provenance(self) -> None:
        current = self.tmp / "current"
        inventory = current / "inventory"
        lexicons = current / "lexicons"
        inventory.mkdir(parents=True)
        lexicons.mkdir()
        object_path = inventory / "object_inventory.tsv"
        attribute_path = inventory / "attribute_inventory.tsv"
        action_path = inventory / "action_inventory.tsv"
        action_canonical_path = inventory / "action_canonical_inventory.tsv"
        _write_tsv(object_path, [{"span_key": "dog"}, {"span_key": "cat"}])
        _write_tsv(attribute_path, [{"span_key": "blue"}])
        _write_tsv(action_path, [{"span_key": "run"}])
        _write_tsv(action_canonical_path, [{"span_key": "run"}])
        _write_tsv(lexicons / "attribute_synonyms.tsv", [{"source": "blue"}])
        (lexicons / "pipeline_state.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "artifact_type": "stage5_lexicon_bundle",
                    "stage": "5",
                    "status": "ready",
                    "preview_mode": False,
                    "attribute_inventory": "outputs/stale.tsv",
                    "action_canonical_inventory": "outputs/stale_action.tsv",
                    "output_dir": "outputs/stale_lexicons",
                    "action_canonical_exported": True,
                }
            ),
            encoding="utf-8",
        )
        (current / "inventory_bundle.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "artifact_type": "gpic_inventory_bundle",
                    "stage": "3.5-6",
                    "status": "complete",
                    "preview_mode": False,
                    "path_base": "bundle_dir",
                    "object_inventory": "inventory/object_inventory.tsv",
                    "attribute_inventory": "inventory/attribute_inventory.tsv",
                    "action_inventory": "inventory/action_inventory.tsv",
                    "action_canonical_inventory": "inventory/action_canonical_inventory.tsv",
                    "lexicon_dir": "lexicons",
                    "snapshot_label": "test",
                }
            ),
            encoding="utf-8",
        )

        summary = refresh.refresh_current_inventory_metadata(current)

        bundle = json.loads((current / "inventory_bundle.json").read_text(encoding="utf-8"))
        lexicon_state = json.loads(
            (lexicons / "pipeline_state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(summary["rows"]["object_inventory"], 2)
        self.assertEqual(bundle["inventory_rows"]["attribute_inventory"], 1)
        self.assertEqual(lexicon_state["attribute_inventory"], "../inventory/attribute_inventory.tsv")
        self.assertEqual(lexicon_state["path_base"], "lexicon_dir")
        self.assertEqual(lexicon_state["output_dir"], ".")
        self.assertEqual(
            json.loads((current / "publish_summary.json").read_text(encoding="utf-8"))[
                "status"
            ],
            "metadata_refreshed",
        )


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
