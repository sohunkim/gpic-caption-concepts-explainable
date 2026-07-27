import importlib.util
from pathlib import Path
import sys
import unittest


def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "merge_attribute_mwe_inventory_update.py"
    spec = importlib.util.spec_from_file_location("merge_attribute_mwe_inventory_update", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load_script()


class MergeAttributeMweInventoryUpdateTest(unittest.TestCase):
    def test_adds_and_replaces_only_mwe_rows(self) -> None:
        base = [
            {
                "span_key": "brown",
                "observed_surface": "brown",
                "count": "100",
                "canonical_surface": "brown",
            },
            _mwe_row("dark brown", count="2"),
        ]
        updates = [
            _mwe_row("dark brown", count="7"),
            _mwe_row("bright blue", count="3"),
        ]

        merged, summary = script.merge_attribute_mwe_inventory_rows(base, updates)

        by_key = {
            (row["attribute_unit_type"], row["span_key"]): row
            for row in merged
        }
        self.assertEqual(by_key[("single_token", "brown")]["count"], "100")
        self.assertEqual(by_key[("mwe", "dark brown")]["count"], "7")
        self.assertEqual(by_key[("mwe", "bright blue")]["count"], "3")
        self.assertEqual(summary["added_mwe_rows"], 1)
        self.assertEqual(summary["replaced_mwe_rows"], 1)
        self.assertEqual(summary["single_token_rows_preserved"], 1)
        self.assertTrue(summary["single_token_field_values_preserved"])

    def test_rejects_single_token_update(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-MWE"):
            script.merge_attribute_mwe_inventory_rows(
                [{"span_key": "brown"}],
                [{"span_key": "blue", "attribute_unit_type": "single_token"}],
            )

    def test_rejects_unresolved_mwe(self) -> None:
        row = _mwe_row("dark brown")
        row["canonical_surface"] = ""
        with self.assertRaisesRegex(ValueError, "missing canonical"):
            script.merge_attribute_mwe_inventory_rows([], [row])

    def test_schema_only_migration_preserves_single_values(self) -> None:
        base = [
            {
                "span_key": "Copa Premier",
                "observed_surface": "Copa Premier",
                "count": "7",
                "canonical_surface": "copa premier",
            }
        ]

        merged, summary = script.merge_attribute_mwe_inventory_rows(base, [])

        self.assertEqual(merged[0]["span_key"], "Copa Premier")
        self.assertEqual(merged[0]["observed_surface"], "Copa Premier")
        self.assertEqual(merged[0]["count"], "7")
        self.assertEqual(merged[0]["attribute_unit_type"], "single_token")
        self.assertEqual(summary["added_mwe_rows"], 0)
        self.assertTrue(summary["single_token_field_values_preserved"])

    def test_excluded_mwe_rows_are_preserved_as_final_decisions(self) -> None:
        chosen = _mwe_row("dark brown")
        excluded = _mwe_row("small white")
        excluded["decision_status"] = "excluded"
        excluded["selected_oewn_synset"] = ""
        excluded["canonical_surface"] = ""

        merged, summary = script.merge_attribute_mwe_inventory_rows(
            [{"span_key": "brown"}],
            [chosen, excluded],
        )

        keys = {
            (row["attribute_unit_type"], row["span_key"])
            for row in merged
        }
        self.assertIn(("mwe", "dark brown"), keys)
        self.assertIn(("mwe", "small white"), keys)
        self.assertEqual(summary["chosen_update_rows"], 1)
        self.assertEqual(summary["excluded_update_rows"], 1)

    def test_rejects_excluded_mwe_with_lexical_selection(self) -> None:
        excluded = _mwe_row("small white")
        excluded["decision_status"] = "excluded"
        excluded["canonical_surface"] = ""
        with self.assertRaisesRegex(ValueError, "retains a selected synset"):
            script.merge_attribute_mwe_inventory_rows([], [excluded])


def _mwe_row(surface: str, *, count: str = "1") -> dict[str, str]:
    return {
        "span_key": surface,
        "attribute_unit_type": "mwe",
        "span_token_count": "2",
        "anchor_token_offset": "1",
        "lookup_forms": surface,
        "attribute_mwe_rule_version": script.ATTRIBUTE_MWE_RULE_VERSION,
        "observed_surface": surface,
        "decision_status": "chosen",
        "selected_oewn_synset": "fake-a",
        "canonical_surface": surface.replace(" ", "_"),
        "canonical_selection_tag": "selected_single_observed_variant_matched_synset_lemma",
        "count": count,
    }


if __name__ == "__main__":
    unittest.main()
