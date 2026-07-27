from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "audit_attribute_inventory_lexicon_update.py"
SPEC = importlib.util.spec_from_file_location(
    "audit_attribute_inventory_lexicon_update",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class AuditAttributeInventoryLexiconUpdateTest(unittest.TestCase):
    def test_accepts_additive_inventory_and_synonym_update(self) -> None:
        inventory_summary, added_inventory = module.audit_inventory_rows(
            [{"span_key": "blue", "canonical_surface": "blue"}],
            [
                {"span_key": "blue", "canonical_surface": "blue"},
                {"span_key": "yellow", "canonical_surface": "yellow"},
            ],
            expected_added_keys={"yellow"},
        )
        synonym_summary, added_synonyms = module.audit_synonym_rows(
            [{"raw": "blue", "canonical": "blue"}],
            [
                {"raw": "blue", "canonical": "blue"},
                {"raw": "yellow", "canonical": "yellow"},
            ],
        )

        self.assertEqual(inventory_summary["added_inventory_rows"], 1)
        self.assertEqual(added_inventory[0]["span_key"], "yellow")
        self.assertEqual(synonym_summary["added_attribute_synonym_rows"], 1)
        self.assertEqual(added_synonyms[0]["raw"], "yellow")

    def test_rejects_existing_inventory_change(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "existing inventory semantic rows modified",
        ):
            module.audit_inventory_rows(
                [{"span_key": "blue", "canonical_surface": "blue"}],
                [{"span_key": "blue", "canonical_surface": "azure"}],
            )

    def test_allows_and_reports_existing_ngram_evidence_refresh(self) -> None:
        summary, added = module.audit_inventory_rows(
            [
                {
                    "span_key": "j",
                    "canonical_surface": "j",
                    "google_ngram_candidate_mean_frequencies": "j:-1",
                }
            ],
            [
                {
                    "span_key": "j",
                    "canonical_surface": "j",
                    "google_ngram_candidate_mean_frequencies": "j:0.001",
                }
            ],
        )

        self.assertEqual(added, [])
        self.assertEqual(
            summary["refreshed_existing_inventory_evidence_rows"],
            1,
        )
        self.assertEqual(
            summary["refreshed_existing_inventory_evidence"]["j"],
            ["google_ngram_candidate_mean_frequencies"],
        )

    def test_rejects_existing_synonym_change(self) -> None:
        with self.assertRaisesRegex(ValueError, "existing attribute synonyms changed"):
            module.audit_synonym_rows(
                [{"raw": "blue", "canonical": "blue"}],
                [{"raw": "blue", "canonical": "azure"}],
            )


if __name__ == "__main__":
    unittest.main()
