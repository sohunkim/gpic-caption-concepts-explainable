from __future__ import annotations

import importlib.util
import sqlite3
import sys
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "build_report_caption_index_from_facts.py"
)
SPEC = importlib.util.spec_from_file_location(
    "build_report_caption_index_from_facts",
    SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ReportCaptionIndexKeyTest(unittest.TestCase):
    def test_attribute_kind_keeps_attribute_and_quantity_rows_distinct(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE attributes (
                _row_id INTEGER PRIMARY KEY,
                canonical_attribute TEXT,
                attribute_kind TEXT
            );
            INSERT INTO attributes VALUES (1, '3', 'attribute');
            INSERT INTO attributes VALUES (2, '3', 'quantity');
            """
        )

        row_maps = MODULE._load_row_maps(conn, {"attributes"})

        self.assertEqual(
            row_maps["attributes"][MODULE._key("3", "attribute")],
            1,
        )
        self.assertEqual(
            row_maps["attributes"][MODULE._key("3", "quantity")],
            2,
        )

    def test_merged_kind_row_accepts_both_fact_kinds(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE attributes (
                _row_id INTEGER PRIMARY KEY,
                canonical_attribute TEXT,
                attribute_kind TEXT
            );
            INSERT INTO attributes VALUES (7, 'three', 'attribute|quantity');
            """
        )

        row_maps = MODULE._load_row_maps(conn, {"attributes"})

        self.assertEqual(
            row_maps["attributes"][MODULE._key("three", "attribute")],
            7,
        )
        self.assertEqual(
            row_maps["attributes"][MODULE._key("three", "quantity")],
            7,
        )

    def test_fact_type_selects_the_corresponding_kind_key(self) -> None:
        added: list[tuple[str, str, str]] = []

        def add(view: str, key: str, caption_id: str) -> None:
            added.append((view, key, caption_id))

        MODULE._add_simple_fact_index(
            {
                "caption_id": "c1",
                "fact_type": "attribute_exists",
                "values": {
                    "attribute": "3",
                    "attribute_kind": "attribute",
                },
            },
            add,
        )
        MODULE._add_simple_fact_index(
            {
                "caption_id": "c2",
                "fact_type": "quantity_exists",
                "values": {"quantity": "3"},
            },
            add,
        )

        self.assertEqual(
            added,
            [
                ("attributes", MODULE._key("3", "attribute"), "c1"),
                ("attributes", MODULE._key("3", "quantity"), "c2"),
            ],
        )

    def test_stale_schema_is_rejected_before_existing_index_is_dropped(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE attributes (
                _row_id INTEGER PRIMARY KEY,
                canonical_attribute TEXT
            );
            CREATE TABLE report_caption_index (
                view_name TEXT NOT NULL,
                row_id INTEGER NOT NULL,
                caption_id TEXT NOT NULL
            );
            INSERT INTO report_caption_index VALUES ('attributes', 1, 'c1');
            """
        )

        with self.assertRaisesRegex(ValueError, "key schema is stale"):
            MODULE._validate_view_key_schema(conn, {"attributes"})

        self.assertEqual(
            conn.execute("SELECT COUNT(*) FROM report_caption_index").fetchone()[0],
            1,
        )


if __name__ == "__main__":
    unittest.main()
