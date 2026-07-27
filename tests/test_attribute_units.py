import csv
from pathlib import Path
import tempfile
import unittest

from gpic_concepts_v1.attribute_units import (
    ATTRIBUTE_MWE_RULE_VERSION,
    AttributeAnchor,
    AttributeTokenView,
    ResolvedAttributeMweIndex,
    select_attribute_mwes,
)


class AttributeUnitsTest(unittest.TestCase):
    def test_longest_nonoverlapping_span_wins(self) -> None:
        tokens = tuple(
            AttributeTokenView(
                i=index,
                text=text,
                lemma=text,
                dep=("amod" if index == 2 else "compound"),
                pos="ADJ",
                tag="JJ",
            )
            for index, text in enumerate(("very", "light", "brown"))
        )
        matches = {
            "light brown": {"span_key": "light brown"},
            "very light brown": {"span_key": "very light brown"},
        }

        selected = select_attribute_mwes(
            tokens,
            anchors=(AttributeAnchor(2),),
            excluded_token_indices=set(),
            lookup=lambda candidate: matches.get(candidate.surface),
        )

        self.assertEqual(
            [match.candidate.surface for match in selected],
            ["very light brown"],
        )

    def test_quote_merged_single_token_with_spaces_is_not_loaded_as_mwe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "attribute_inventory.tsv"
            _write_inventory(
                path,
                [
                    {
                        "span_key": "Copa Premier",
                        "attribute_unit_type": "single_token",
                        "span_token_count": "1",
                        "anchor_token_offset": "0",
                        "lookup_forms": "Copa Premier",
                        "attribute_mwe_rule_version": ATTRIBUTE_MWE_RULE_VERSION,
                        "decision_status": "chosen",
                    }
                ],
            )

            index = ResolvedAttributeMweIndex.from_tsv(path)

        self.assertEqual(len(index), 0)

    def test_stale_inventory_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "attribute_inventory.tsv"
            path.write_text(
                "span_key\tdecision_status\nbrown\tchosen\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "predates the Attribute MWE schema"):
                ResolvedAttributeMweIndex.from_tsv(path)

    def test_exact_surface_owns_key_before_morphy_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "attribute_inventory.tsv"
            _write_inventory(
                path,
                [
                    {
                        "span_key": "red carpeted",
                        "observed_surface": "red carpeted",
                        "attribute_unit_type": "mwe",
                        "span_token_count": "2",
                        "anchor_token_offset": "1",
                        "lookup_forms": "red carpeted|red carpet",
                        "attribute_mwe_rule_version": ATTRIBUTE_MWE_RULE_VERSION,
                        "decision_status": "chosen",
                        "canonical_surface": "red carpet",
                    },
                    {
                        "span_key": "red carpet",
                        "observed_surface": "red carpet",
                        "attribute_unit_type": "mwe",
                        "span_token_count": "2",
                        "anchor_token_offset": "1",
                        "lookup_forms": "red carpet",
                        "attribute_mwe_rule_version": ATTRIBUTE_MWE_RULE_VERSION,
                        "decision_status": "chosen",
                        "canonical_surface": "red carpet",
                    },
                ],
            )

            index = ResolvedAttributeMweIndex.from_tsv(path)

        self.assertEqual(index.lookup_surface("red carpet")["span_key"], "red carpet")
        self.assertEqual(
            index.lookup_surface("red carpeted")["span_key"],
            "red carpeted",
        )

    def test_conflicting_aliases_without_exact_owner_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "attribute_inventory.tsv"
            _write_inventory(
                path,
                [
                    {
                        "span_key": "red carpeting",
                        "observed_surface": "red carpeting",
                        "attribute_unit_type": "mwe",
                        "span_token_count": "2",
                        "anchor_token_offset": "1",
                        "lookup_forms": "red carpeting|red floor",
                        "attribute_mwe_rule_version": ATTRIBUTE_MWE_RULE_VERSION,
                        "decision_status": "chosen",
                        "canonical_surface": "red carpeting",
                    },
                    {
                        "span_key": "red rugging",
                        "observed_surface": "red rugging",
                        "attribute_unit_type": "mwe",
                        "span_token_count": "2",
                        "anchor_token_offset": "1",
                        "lookup_forms": "red rugging|red floor",
                        "attribute_mwe_rule_version": ATTRIBUTE_MWE_RULE_VERSION,
                        "decision_status": "chosen",
                        "canonical_surface": "red rugging",
                    },
                ],
            )

            with self.assertRaisesRegex(
                ValueError,
                "conflicting attribute MWE inventory alias",
            ):
                ResolvedAttributeMweIndex.from_tsv(path)


def _write_inventory(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "span_key",
        "observed_surface",
        "example_surfaces",
        "attribute_unit_type",
        "span_token_count",
        "anchor_token_offset",
        "lookup_forms",
        "attribute_mwe_rule_version",
        "decision_status",
        "canonical_surface",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
