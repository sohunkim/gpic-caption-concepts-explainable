from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "extract_legacy_quantity_caption_ids.py"
SPEC = importlib.util.spec_from_file_location("extract_legacy_quantity_caption_ids", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class LegacyQuantityCaptionIdsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = ROOT / ".tmp_tests" / f"legacy_quantity_{uuid.uuid4().hex}"
        self.tmp.mkdir(parents=True, exist_ok=False)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_extracts_distinct_requested_quantity_and_pair_caption_ids(self) -> None:
        facts_path = self.tmp / "facts.jsonl"
        rows = [
            _fact("c1", "quantity_exists", quantity="2"),
            _fact("c1", "quantity_exists", quantity="2"),
            _fact("c2", "quantity_exists", quantity="2"),
            _fact("c3", "quantity_exists", quantity="4"),
            _fact("c1", "has_quantity", object="dog", quantity="2"),
            _fact("c2", "has_quantity", object="cat", quantity="2"),
            _fact("c4", "attribute_exists", attribute="2"),
        ]
        facts_path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

        result = MODULE.extract_legacy_quantity_caption_ids(
            facts_path=facts_path,
            wanted_attributes={"2"},
            wanted_pairs={("dog", "2")},
            progress_every=1,
        )

        self.assertEqual(result["attribute_caption_ids"], {"2": ["c1", "c2"]})
        self.assertEqual(
            result["entity_attribute_pair_caption_ids"],
            {"dog\t2": ["c1"]},
        )
        self.assertEqual(result["summary"]["relevant_facts"], 6)


def _fact(caption_id: str, fact_type: str, **values: str) -> dict[str, object]:
    return {
        "caption_id": caption_id,
        "fact_type": fact_type,
        "values": values,
    }


if __name__ == "__main__":
    unittest.main()
