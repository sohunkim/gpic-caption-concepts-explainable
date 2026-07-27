import csv
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


def _load_script():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "stage_attribute_mwe_inventory_bundle.py"
    )
    spec = importlib.util.spec_from_file_location(
        "stage_attribute_mwe_inventory_bundle",
        path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load_script()


class StageAttributeMweInventoryBundleTest(unittest.TestCase):
    def test_stages_full_bundle_and_preserves_non_attribute_components(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base"
            inventory = base / "inventory"
            lexicons = base / "lexicons"
            inventory.mkdir(parents=True)
            lexicons.mkdir()
            _write_text(inventory / "object_inventory.tsv", "span_key\nobject\n")
            _write_text(inventory / "action_inventory.tsv", "span_key\naction\n")
            _write_text(
                inventory / "action_inventory.tsv.pipeline_state.json",
                '{"status":"complete"}\n',
            )
            _write_text(inventory / "action_canonical_inventory.tsv", "span_key\naction\n")
            _write_text(inventory / "attribute_inventory.tsv", "span_key\nold\n")
            _write_text(lexicons / "placeholder.tsv", "x\n")
            bundle = base / "inventory_bundle.json"
            bundle.write_text(
                json.dumps(
                    {
                        "artifact_type": "gpic_inventory_bundle",
                        "status": "complete",
                        "path_base": "bundle_dir",
                        "object_inventory": "inventory/object_inventory.tsv",
                        "attribute_inventory": "inventory/attribute_inventory.tsv",
                        "action_inventory": "inventory/action_inventory.tsv",
                        "action_canonical_inventory": (
                            "inventory/action_canonical_inventory.tsv"
                        ),
                        "lexicon_dir": "lexicons",
                    }
                ),
                encoding="utf-8",
            )

            updated_attribute = root / "updated_attribute.tsv"
            _write_attribute_inventory(updated_attribute)
            staged_lexicons = root / "staged_lexicons"
            staged_lexicons.mkdir()
            (staged_lexicons / "pipeline_state.json").write_text(
                json.dumps(
                    {
                        "status": "ready",
                        "preview_mode": False,
                        "attribute_inventory": str(updated_attribute),
                    }
                ),
                encoding="utf-8",
            )
            _write_text(
                staged_lexicons / "attribute_synonyms.tsv",
                "raw_attribute\tcanonical_attribute\nlight brown\tlight brown\n",
            )
            verification = root / "verification.json"
            verification.write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "failures": [],
                        "mwe_inventory_rows": 1,
                    }
                ),
                encoding="utf-8",
            )

            output = root / "output"
            summary = script.stage_attribute_mwe_inventory_bundle(
                base_bundle_path=bundle,
                attribute_inventory=updated_attribute,
                lexicon_dir=staged_lexicons,
                verification_path=verification,
                output_dir=output,
                snapshot_label="test",
            )

            self.assertEqual(summary["mwe_rows"], 1)
            self.assertEqual(summary["chosen_mwe_rows"], 1)
            self.assertEqual(summary["excluded_mwe_rows"], 0)
            self.assertEqual(
                (output / "inventory" / "object_inventory.tsv").read_bytes(),
                (inventory / "object_inventory.tsv").read_bytes(),
            )
            self.assertEqual(
                (output / "inventory" / "action_inventory.tsv").read_bytes(),
                (inventory / "action_inventory.tsv").read_bytes(),
            )
            self.assertEqual(
                (output / "inventory" / "attribute_inventory.tsv").read_bytes(),
                updated_attribute.read_bytes(),
            )
            self.assertTrue((output / "lexicons" / "attribute_synonyms.tsv").is_file())
            self.assertTrue((output / "inventory_bundle.json").is_file())

    def test_rejects_failed_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            verification = root / "verification.json"
            verification.write_text(
                '{"status":"failed","failures":["mismatch"]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "verification_not_clear"):
                script.stage_attribute_mwe_inventory_bundle(
                    base_bundle_path=root / "missing.json",
                    attribute_inventory=root / "missing.tsv",
                    lexicon_dir=root / "missing",
                    verification_path=verification,
                    output_dir=root / "output",
                )


def _write_attribute_inventory(path: Path) -> None:
    fieldnames = [
        "span_key",
        "attribute_unit_type",
        "span_token_count",
        "anchor_token_offset",
        "lookup_forms",
        "attribute_mwe_rule_version",
        "decision_status",
        "selected_oewn_synset",
        "canonical_surface",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerow(
            {
                "span_key": "single",
                "attribute_unit_type": "single_token",
                "span_token_count": "1",
                "anchor_token_offset": "0",
            }
        )
        writer.writerow(
            {
                "span_key": "light brown",
                "attribute_unit_type": "mwe",
                "span_token_count": "2",
                "anchor_token_offset": "1",
                "lookup_forms": "light brown",
                "attribute_mwe_rule_version": script.ATTRIBUTE_MWE_RULE_VERSION,
                "decision_status": "chosen",
                "selected_oewn_synset": "oewn-test-s",
                "canonical_surface": "light brown",
            }
        )


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
