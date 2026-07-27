from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "merge_attribute_conj_inventory_update.py"
SPEC = importlib.util.spec_from_file_location("merge_attribute_conj_update", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class FakeSynset:
    def __init__(self, synset_id: str, lexfile: str, lemmas: tuple[str, ...]) -> None:
        self.id = synset_id
        self._lexfile = lexfile
        self._lemmas = lemmas

    def lexfile(self) -> str:
        return self._lexfile

    def lemmas(self) -> list[str]:
        return list(self._lemmas)


class MergeAttributeConjInventoryUpdateTest(unittest.TestCase):
    def test_appends_auto_and_manual_rows_without_modifying_base(self) -> None:
        base = [{field: "" for field in module.ATTRIBUTE_INVENTORY_FIELDS}]
        base[0].update({"span_key": "blue", "canonical_surface": "blue"})
        auto = [audit_row("subtropical", "s1", "adj.pert")]
        manual = [audit_row("sickle", "s2", "noun.artifact")]
        expected = [*auto, *manual]

        def runtime_lookup(surface: str, **_: object):
            row = auto[0] if surface == "subtropical" else manual[0]
            synset = FakeSynset(
                row["selected_oewn_synset"],
                row["selected_oewn_lexfile"],
                (surface,),
            )
            return module.AttributeLookupResult(
                "exact",
                surface,
                (synset,),
                synset,
                "single_oewn_attribute_synset",
                "",
                module._attribute_gate_for_lexfile(synset.lexfile()),
                "chosen" if surface == "subtropical" else "needs_manual",
                (
                    "selected_attribute_compatible"
                    if surface == "subtropical"
                    else "manual_attribute_gate_required"
                ),
            )

        merged, summary = module.merge_attribute_conj_inventory_rows(
            base,
            auto_rows=auto,
            manual_rows=manual,
            expected_rows=expected,
            runtime_lookup=runtime_lookup,
        )

        self.assertEqual(merged[0], base[0])
        self.assertEqual(summary["existing_rows_modified"], 0)
        self.assertEqual(summary["added_rows"], 2)
        by_key = {row["span_key"]: row for row in merged}
        self.assertEqual(by_key["subtropical"]["decision_reason"], "selected_attribute_compatible")
        self.assertEqual(by_key["sickle"]["decision_reason"], "manual_attribute_synset_selected")
        self.assertEqual(
            by_key["sickle"]["synset_selection_tag"],
            "manual_attribute_synset_selected",
        )

    def test_rejects_existing_key_overwrite(self) -> None:
        base = [{field: "" for field in module.ATTRIBUTE_INVENTORY_FIELDS}]
        base[0]["span_key"] = "sickle"
        manual = [audit_row("sickle", "s2", "noun.artifact")]

        with self.assertRaisesRegex(ValueError, "overwrite existing"):
            module.merge_attribute_conj_inventory_rows(
                base,
                auto_rows=[],
                manual_rows=manual,
                expected_rows=manual,
                runtime_lookup=lambda *_args, **_kwargs: None,
            )


def audit_row(span_key: str, synset_id: str, lexfile: str) -> dict[str, str]:
    return {
        "span_key": span_key,
        "observed_surfaces": span_key,
        "mention_count": "2",
        "caption_count": "2",
        "example_caption_ids": "c1|c2",
        "tag_values": "JJ",
        "oewn_synset_ids": synset_id,
        "decision_status": "chosen",
        "decision_reason": "manual_attribute_synset_selected",
        "selected_oewn_synset": synset_id,
        "selected_oewn_lexfile": lexfile,
    }


if __name__ == "__main__":
    unittest.main()
