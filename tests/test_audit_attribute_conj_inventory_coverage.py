from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "audit_attribute_conj_inventory_coverage.py"
SPEC = importlib.util.spec_from_file_location("attribute_conj_audit_script", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
audit_script = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit_script
SPEC.loader.exec_module(audit_script)


class AuditAttributeConjInventoryCoverageTest(unittest.TestCase):
    def test_collects_only_conj_attributes_and_deduplicates_caption_count(self) -> None:
        records = [
            {
                "caption_id": "c1",
                "mention_type": "attribute",
                "text": "Yellow",
                "source_detail": {
                    "modifier_source": "conj_of_attribute_modifier",
                    "pos": "ADJ",
                    "tag": "JJ",
                },
            },
            {
                "caption_id": "c1",
                "mention_type": "attribute",
                "text": "yellow",
                "source_detail": {
                    "modifier_source": "conj_of_attribute_modifier",
                    "pos": "ADJ",
                    "tag": "JJ",
                },
            },
            {
                "caption_id": "c2",
                "mention_type": "attribute",
                "text": "yellow",
                "source_detail": {
                    "modifier_source": "conj_of_attribute_modifier",
                    "pos": "ADJ",
                    "tag": "JJ",
                },
            },
            {
                "caption_id": "c3",
                "mention_type": "attribute",
                "text": "blue",
                "source_detail": {"modifier_source": "base_attribute_modifier"},
            },
        ]

        inventory, scanned = audit_script.collect_conj_attribute_coverage(records)

        self.assertEqual(scanned, 4)
        self.assertEqual(set(inventory), {"yellow"})
        self.assertEqual(inventory["yellow"].mention_count, 3)
        self.assertEqual(inventory["yellow"].caption_ids, {"c1", "c2"})
        self.assertEqual(inventory["yellow"].surfaces, {"Yellow": 1, "yellow": 2})

    def test_marks_exact_stage5_lexicon_hit_and_raw_fallback(self) -> None:
        hit = audit_script.ConjAttributeAccumulator("yellow")
        hit.mention_count = 2
        hit.caption_ids.update(("c1", "c2"))
        hit.surfaces["yellow"] = 2
        miss = audit_script.ConjAttributeAccumulator("golden")
        miss.mention_count = 1
        miss.caption_ids.add("c3")
        miss.surfaces["golden"] = 1

        rows = audit_script.build_audit_rows(
            {"yellow": hit, "golden": miss},
            attribute_synonyms={"yellow": "yellow"},
            probe_oewn=False,
        )
        by_key = {row["span_key"]: row for row in rows}

        self.assertEqual(by_key["yellow"]["current_lexicon_status"], "hit")
        self.assertEqual(by_key["yellow"]["current_canonical"], "yellow")
        self.assertEqual(by_key["golden"]["current_lexicon_status"], "raw_fallback")
        self.assertEqual(by_key["golden"]["current_canonical"], "")

    def test_load_attribute_synonyms_uses_stage5_exact_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attribute_synonyms.tsv"
            path.write_text(
                "raw\tcanonical\tsource\tnotes\n"
                " Yellow \tyellow\ttest\t\n",
                encoding="utf-8",
            )

            synonyms = audit_script.load_attribute_synonyms(path)

        self.assertEqual(synonyms, {"yellow": "yellow"})


if __name__ == "__main__":
    unittest.main()
