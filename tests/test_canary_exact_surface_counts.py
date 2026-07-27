from __future__ import annotations

import importlib.util
import sqlite3
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_canary_display_t5_lexicon_comparison.py"
SPEC = importlib.util.spec_from_file_location("build_canary_display_t5_lexicon_comparison", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ExactSurfaceCountTest(unittest.TestCase):
    def test_surface_counts_are_not_copied_from_canonical_key(self) -> None:
        source = {
            "count_basis": "unique_caption_id",
            "attribute_label_policy": "exact_t5_attribute_name",
            "entity_caption_counts": {"mushroom": 10},
            "attribute_caption_counts": {
                "cluster": 2_896,
                "clustered": 6,
                "clusters": 1,
                "silhouette": 2_681,
                "silhouetted": 893,
            },
            "entity_attribute_pair_caption_counts": {
                "mushroom\tcluster": 19,
                "mushroom\tclustered": 1,
                "mushroom\tclusters": 1,
            },
        }
        selected = MODULE._select_exact_surface_t5_counts(
            entities=["mushroom"],
            attributes=["cluster", "clustered", "clusters", "silhouette", "silhouetted"],
            pairs=[
                ("mushroom", "cluster"),
                ("mushroom", "clustered"),
                ("mushroom", "clusters"),
            ],
            t5_counts=source,
        )
        self.assertEqual(selected["attribute_caption_counts"]["cluster"], 2_896)
        self.assertEqual(selected["attribute_caption_counts"]["clustered"], 6)
        self.assertEqual(selected["attribute_caption_counts"]["clusters"], 1)
        self.assertEqual(selected["attribute_caption_counts"]["silhouette"], 2_681)
        self.assertEqual(selected["attribute_caption_counts"]["silhouetted"], 893)
        self.assertEqual(
            selected["entity_attribute_pair_caption_counts"]["mushroom\tclustered"],
            1,
        )

    def test_frequency_or_canonical_inputs_are_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            MODULE._assert_exact_surface_caption_counts(
                {
                    "count_basis": "frequency",
                    "attribute_label_policy": "exact_t5_attribute_name",
                },
            )
        with self.assertRaises(SystemExit):
            MODULE._assert_exact_surface_caption_counts(
                {
                    "count_basis": "unique_caption_id",
                    "attribute_label_policy": "canonical_remap",
                },
            )

    def test_lexicon_rows_use_distinct_caption_union(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE report_caption_index ("
            "view_name TEXT NOT NULL, row_id INTEGER NOT NULL, caption_id TEXT NOT NULL"
            ")",
        )
        conn.executemany(
            "INSERT INTO report_caption_index VALUES (?, ?, ?)",
            [
                ("attributes", 1, "c1"),
                ("attributes", 1, "c2"),
                ("attributes", 2, "c2"),
                ("attributes", 2, "c3"),
            ],
        )
        self.assertEqual(
            MODULE._distinct_caption_count(conn, "attributes", {1, 2}),
            3,
        )

    def test_legacy_quantity_ids_are_unioned_with_attribute_and_pair_ids(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE report_caption_index ("
            "view_name TEXT NOT NULL, row_id INTEGER NOT NULL, caption_id TEXT NOT NULL"
            ")",
        )
        conn.executemany(
            "INSERT INTO report_caption_index VALUES (?, ?, ?)",
            [
                ("attributes", 1, "c1"),
                ("attribute_object_pairs", 10, "c1"),
            ],
        )
        matcher = MODULE.LabelMatcher(
            [MODULE.CountRow(1, "3", "3", 1)],
            include_variants_with_exact=True,
            conn=conn,
            view="attributes",
            supplemental_caption_ids={"3": {"c1", "c2"}},
        )
        match = matcher.match("3")
        self.assertEqual(match.labels, ("3",))
        self.assertEqual(match.caption_count, 2)

        pair_rows = {
            ("dog", "3"): {
                MODULE.PairRow(10, "dog", "3"),
            },
        }
        self.assertEqual(
            MODULE._fetch_pair_caption_count(
                conn,
                pair_rows_by_key=pair_rows,
                entity_labels=("dog",),
                attribute_labels=("3",),
                supplemental_caption_ids={"dog\t3": {"c1", "c3"}},
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
