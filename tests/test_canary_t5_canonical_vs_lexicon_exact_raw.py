from __future__ import annotations

import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "build_canary_t5_canonical_vs_lexicon_exact_raw.py"
)
SPEC = importlib.util.spec_from_file_location(
    "build_canary_t5_canonical_vs_lexicon_exact_raw",
    SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CanaryCanonicalT5ExactRawLexiconTest(unittest.TestCase):
    def test_display_surface_remap_cannot_be_used_as_canonical_count_input(
        self,
    ) -> None:
        with self.assertRaisesRegex(ValueError, "global canonical-count artifact"):
            MODULE._validate_t5_canonical_count_contract(
                {
                    "attribute_label_policy": (
                        "8756 display surface labels remapped from full "
                        "T5 canonical counts"
                    ),
                    "attribute_caption_counts": {
                        "blooms": 1015,
                        "carved": 4217,
                    },
                },
            )

    def test_inflected_display_surfaces_use_t5_canonical_count_keys(self) -> None:
        t5_counts = {
            "bloom": 1015,
            "carve": 4217,
        }
        pair_counts = {
            "flower\tbloom": 341,
            "mushroom\tcarve": 1,
        }

        self.assertEqual(
            MODULE._canonical_attribute_count(
                "blooms",
                ("bloom",),
                t5_counts,
                {},
            ),
            1015,
        )
        self.assertEqual(
            MODULE._canonical_attribute_count(
                "carved",
                ("carve",),
                t5_counts,
                {},
            ),
            4217,
        )
        self.assertEqual(
            MODULE._canonical_pair_count(
                "flower",
                "blooms",
                ("bloom",),
                pair_counts,
                {},
            ),
            341,
        )
        self.assertEqual(
            MODULE._canonical_pair_count(
                "mushroom",
                "carved",
                ("carve",),
                pair_counts,
                {},
            ),
            1,
        )

    def test_t5_uses_explicit_canonical_name_and_lexicon_uses_selected_rows(
        self,
    ) -> None:
        display = {
            "entities": ["dog"],
            "attribute_records": [
                {"surface": "hand-drawn", "internal_names": ["hand - draw"]},
                {"surface": "glowing", "internal_names": ["glow", "glowing"]},
            ],
            "pairs": [
                {
                    "entity": "dog",
                    "attribute": "hand-drawn",
                    "attribute_internal_names": ["hand - draw"],
                },
                {
                    "entity": "dog",
                    "attribute": "glowing",
                    "attribute_internal_names": ["glow", "glowing"],
                },
            ],
        }
        t5 = {
            "entity_caption_counts": {"dog": 10},
            "attribute_caption_counts": {
                "hand - draw": 7,
                "glow": 9,
                "glowing": 3,
            },
            "entity_attribute_pair_caption_counts": {
                "dog\thand - draw": 2,
                "dog\tglow": 4,
                "dog\tglowing": 1,
            },
        }
        t5_multi = {
            "attribute_caption_counts": {"glowing": 11},
            "entity_attribute_pair_caption_counts": {"dog\tglowing": 5},
        }
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "report.db"
            conn = sqlite3.connect(db)
            conn.executescript(
                "CREATE TABLE objects (_row_id INTEGER PRIMARY KEY, canonical_object TEXT);"
                "CREATE TABLE attributes ("
                "_row_id INTEGER PRIMARY KEY, canonical_attribute TEXT, "
                "attribute_raw_surfaces TEXT);"
                "CREATE TABLE attribute_object_pairs ("
                "_row_id INTEGER PRIMARY KEY, object TEXT, attribute TEXT, "
                "attribute_raw_surfaces TEXT);"
                "CREATE TABLE report_caption_index ("
                "view_name TEXT, row_id INTEGER, caption_id TEXT);"
                "INSERT INTO objects VALUES (1, 'dog');"
                "INSERT INTO attributes VALUES "
                "(2, 'hand-drawn', 'hand-drawn|hand drawn'),"
                "(3, 'glow', 'glow|glowing');"
                "INSERT INTO attribute_object_pairs VALUES "
                "(4, 'dog', 'hand-drawn', 'hand-drawn|hand drawn'),"
                "(5, 'dog', 'glow', 'glow|glowing');"
                "INSERT INTO report_caption_index VALUES ('objects', 1, 'c1');"
                "INSERT INTO report_caption_index VALUES ('objects', 1, 'c2');"
                "INSERT INTO report_caption_index VALUES ('attributes', 2, 'c1');"
                "INSERT INTO report_caption_index VALUES ('attributes', 2, 'c2');"
                "INSERT INTO report_caption_index VALUES ('attributes', 3, 'c2');"
                "INSERT INTO report_caption_index VALUES ('attributes', 3, 'c3');"
                "INSERT INTO report_caption_index VALUES ('attribute_object_pairs', 4, 'c1');"
                "INSERT INTO report_caption_index VALUES ('attribute_object_pairs', 5, 'c2');"
            )
            conn.commit()
            conn.close()

            result = MODULE.build_comparison(
                display=display,
                t5=t5,
                t5_multi=t5_multi,
                legacy_quantity={},
                report_db=db,
            )

        attributes = {row["surface"]: row for row in result["attributes"]}
        self.assertEqual(attributes["hand-drawn"]["t5_caption_count"], 7)
        self.assertEqual(attributes["glowing"]["t5_caption_count"], 11)
        self.assertEqual(attributes["hand-drawn"]["lexicon_caption_count"], 2)
        self.assertEqual(attributes["glowing"]["lexicon_caption_count"], 2)
        self.assertAlmostEqual(
            attributes["hand-drawn"]["lexicon_minus_t5_percent"],
            -71.42857142857143,
        )
        self.assertEqual(attributes["hand-drawn"]["difference_band"], "50%+")
        self.assertEqual(
            result["entities"][0]["lexicon_minus_t5_percent"],
            -80.0,
        )
        self.assertEqual(result["entities"][0]["difference_band"], "50%+")
        pairs = {(row["entity"], row["surface"]): row for row in result["pairs"]}
        self.assertEqual(pairs[("dog", "hand-drawn")]["t5_caption_count"], 2)
        self.assertEqual(pairs[("dog", "glowing")]["t5_caption_count"], 5)
        self.assertEqual(
            pairs[("dog", "hand-drawn")]["lexicon_minus_t5_percent"],
            -50.0,
        )
        self.assertEqual(
            pairs[("dog", "hand-drawn")]["difference_band"],
            "50%+",
        )
        self.assertEqual(
            result["difference_band_summary"],
            {
                "entities": {
                    "0~5%": 0,
                    "5~10%": 0,
                    "10~20%": 0,
                    "20~30%": 0,
                    "30~50%": 0,
                    "50%+": 1,
                    "N/A": 0,
                },
                "attributes": {
                    "0~5%": 0,
                    "5~10%": 0,
                    "10~20%": 0,
                    "20~30%": 0,
                    "30~50%": 0,
                    "50%+": 2,
                    "N/A": 0,
                },
                "pairs": {
                    "0~5%": 0,
                    "5~10%": 0,
                    "10~20%": 0,
                    "20~30%": 0,
                    "30~50%": 0,
                    "50%+": 2,
                    "N/A": 0,
                },
            },
        )
        self.assertEqual(result["statistics"]["entities"]["rows"], 1)
        self.assertIsNone(result["statistics"]["entities"]["pearson_r"])
        self.assertEqual(result["statistics"]["attributes"]["rows"], 2)
        self.assertIsNone(result["statistics"]["attributes"]["pearson_r"])
        self.assertFalse(result["generated_variants"])

    def test_exact_raw_overlap_selects_whole_canonical_row_without_generated_forms(
        self,
    ) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            "CREATE TABLE attributes ("
            "_row_id INTEGER PRIMARY KEY, canonical_attribute TEXT, "
            "attribute_raw_surfaces TEXT);"
            "CREATE TABLE report_caption_index ("
            "view_name TEXT, row_id INTEGER, caption_id TEXT);"
            "INSERT INTO attributes VALUES (1, 'water', 'water|watering|watered');"
            "INSERT INTO attributes VALUES (2, 'watercolor', 'watered-looking');"
            "INSERT INTO report_caption_index VALUES ('attributes', 1, 'c1');"
            "INSERT INTO report_caption_index VALUES ('attributes', 1, 'c2');"
            "INSERT INTO report_caption_index VALUES ('attributes', 1, 'c3');"
            "INSERT INTO report_caption_index VALUES ('attributes', 2, 'c4');"
        )

        match = MODULE._lexicon_attribute_match(conn, aliases={"water"})

        self.assertEqual(match["canonical_attributes"], ["water"])
        self.assertEqual(match["raw_surfaces"], ["water"])
        self.assertEqual(match["caption_count"], 3)

    def test_legacy_quantity_caption_ids_are_unioned_with_regular_attribute_ids(
        self,
    ) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            "CREATE TABLE attributes ("
            "_row_id INTEGER PRIMARY KEY, canonical_attribute TEXT, "
            "attribute_raw_surfaces TEXT);"
            "CREATE TABLE report_caption_index ("
            "view_name TEXT, row_id INTEGER, caption_id TEXT);"
            "INSERT INTO attributes VALUES (1, '3', '3');"
            "INSERT INTO report_caption_index VALUES ('attributes', 1, 'c1');"
            "INSERT INTO report_caption_index VALUES ('attributes', 1, 'c2');"
        )
        legacy = {
            "attribute_caption_ids": {
                "3": ["c2", "c3", "c4"],
            },
        }

        match = MODULE._lexicon_attribute_match(
            conn,
            aliases={"3"},
            extra_caption_ids=MODULE._legacy_attribute_caption_ids(
                legacy,
                aliases={"3"},
            ),
        )

        self.assertEqual(match["caption_count"], 4)

    def test_numeric_quantity_alias_matches_spelled_raw_surface(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            "CREATE TABLE attributes ("
            "_row_id INTEGER PRIMARY KEY, canonical_attribute TEXT, "
            "attribute_raw_surfaces TEXT);"
            "CREATE TABLE attribute_object_pairs ("
            "_row_id INTEGER PRIMARY KEY, object TEXT, attribute TEXT, "
            "attribute_raw_surfaces TEXT);"
            "CREATE TABLE report_caption_index ("
            "view_name TEXT, row_id INTEGER, caption_id TEXT);"
            "INSERT INTO attributes VALUES (1, '3', '3');"
            "INSERT INTO attributes VALUES (2, 'three', 'three');"
            "INSERT INTO attribute_object_pairs VALUES (3, 'mushroom', 'three', 'three');"
            "INSERT INTO report_caption_index VALUES ('attributes', 1, 'c1');"
            "INSERT INTO report_caption_index VALUES ('attributes', 2, 'c2');"
            "INSERT INTO report_caption_index VALUES ('attributes', 2, 'c3');"
            "INSERT INTO report_caption_index VALUES ('attribute_object_pairs', 3, 'c3');"
        )
        aliases = MODULE._lexicon_aliases_for_display_attribute("3", ("3",))

        attribute_match = MODULE._lexicon_attribute_match(conn, aliases=aliases)
        pair_match = MODULE._lexicon_pair_match(
            conn,
            entity="mushroom",
            attribute_aliases=aliases,
        )

        self.assertEqual(aliases, {"3", "three"})
        self.assertEqual(attribute_match["raw_surfaces"], ["3", "three"])
        self.assertEqual(attribute_match["canonical_attributes"], ["3", "three"])
        self.assertEqual(attribute_match["caption_count"], 3)
        self.assertEqual(pair_match["caption_count"], 1)

    def test_multiple_t5_names_cannot_be_summed_without_union_override(self) -> None:
        with self.assertRaises(ValueError):
            MODULE._canonical_attribute_count(
                "glowing",
                ("glow", "glowing"),
                {"glow": 9, "glowing": 3},
                {},
            )

    def test_relative_difference_percent_uses_t5_as_baseline(self) -> None:
        self.assertEqual(MODULE._relative_difference_percent(125, 100), 25.0)
        self.assertEqual(MODULE._relative_difference_percent(75, 100), -25.0)
        self.assertIsNone(MODULE._relative_difference_percent(3, 0))
        self.assertEqual(MODULE._fmt_percent(None), "N/A")
        self.assertEqual(MODULE._fmt_percent(12.345), "+12.3%")
        self.assertEqual(MODULE._difference_band(None), "N/A")
        self.assertEqual(MODULE._difference_band(4.999), "0~5%")
        self.assertEqual(MODULE._difference_band(-5.0), "5~10%")
        self.assertEqual(MODULE._difference_band(9.999), "5~10%")
        self.assertEqual(MODULE._difference_band(-10.0), "10~20%")
        self.assertEqual(MODULE._difference_band(19.999), "10~20%")
        self.assertEqual(MODULE._difference_band(-20.0), "20~30%")
        self.assertEqual(MODULE._difference_band(29.999), "20~30%")
        self.assertEqual(MODULE._difference_band(-30.0), "30~50%")
        self.assertEqual(MODULE._difference_band(49.999), "30~50%")
        self.assertEqual(MODULE._difference_band(-50.0), "50%+")

    def test_aggregate_statistics_include_correlation_and_error_metrics(self) -> None:
        rows = [
            {"t5_caption_count": 1, "lexicon_caption_count": 2},
            {"t5_caption_count": 2, "lexicon_caption_count": 4},
            {"t5_caption_count": 3, "lexicon_caption_count": 6},
        ]

        stats = MODULE._aggregate_statistics(rows)

        self.assertEqual(stats["rows"], 3)
        self.assertEqual(stats["t5_total"], 6)
        self.assertEqual(stats["lexicon_total"], 12)
        self.assertEqual(stats["total_diff"], 6)
        self.assertEqual(stats["total_diff_percent"], 100.0)
        self.assertAlmostEqual(stats["pearson_r"], 1.0)
        self.assertAlmostEqual(stats["r_squared"], 1.0)
        self.assertEqual(stats["mean_absolute_error"], 2.0)
        self.assertEqual(stats["mean_absolute_percentage_error"], 100.0)

    def test_summary_count_percent_formatter_uses_group_total(self) -> None:
        self.assertEqual(MODULE._fmt_count_percent(3, 8), "3 (37.5%)")
        self.assertEqual(MODULE._fmt_count_percent(0, 0), "0 (N/A)")


if __name__ == "__main__":
    unittest.main()
