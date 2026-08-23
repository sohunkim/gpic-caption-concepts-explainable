from __future__ import annotations

import importlib.util
import sqlite3
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_quote_free_interactive_report.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("build_quote_free_interactive_report", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class QuoteFreeInteractiveReportTests(unittest.TestCase):
    def test_literal_single_token_quote_row_is_removed(self) -> None:
        module = _load_module()
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE objects (
                _row_id INTEGER PRIMARY KEY,
                canonical_object TEXT,
                object_raw_surfaces TEXT,
                caption_count INTEGER
            );
            CREATE TABLE captions (
                caption_id TEXT PRIMARY KEY,
                caption_index INTEGER,
                caption_type TEXT,
                caption_shape TEXT,
                caption TEXT
            );
            CREATE TABLE report_caption_index (
                view_name TEXT NOT NULL,
                row_id INTEGER NOT NULL,
                caption_id TEXT NOT NULL,
                PRIMARY KEY (view_name, row_id, caption_id)
            ) WITHOUT ROWID;
            """
        )
        conn.execute(
            "INSERT INTO objects VALUES (1, ?, ?, 1)",
            ('"balatro."', '"balatro."'),
        )
        conn.execute(
            "INSERT INTO captions VALUES ('c1', 0, 'short', '', ?)",
            ('A sign identifies the booth as "BALATRO."',),
        )
        conn.execute("INSERT INTO report_caption_index VALUES ('objects', 1, 'c1')")

        # Use an in-memory caption map directly so the test stays filesystem-free.
        raw_by_caption = {"c1": {"balatro"}}
        structural_by_caption = {
            caption_id: module.filter_structural_quote_terms(terms)
            for caption_id, terms in raw_by_caption.items()
        }
        structural_by_caption = {
            caption_id: terms for caption_id, terms in structural_by_caption.items() if terms
        }
        row_ids, samples = module.find_quote_row_ids(
            conn,
            "objects",
            ["canonical_object", "object_raw_surfaces"],
            module.filter_structural_quote_terms({"balatro"}),
            raw_quote_terms={"balatro"},
            quote_terms_by_caption=structural_by_caption,
            raw_quote_terms_by_caption=raw_by_caption,
            quote_caption_counts=module.count_quote_term_captions(structural_by_caption),
            raw_quote_caption_counts=module.count_quote_term_captions(raw_by_caption),
        )

        self.assertEqual(row_ids, [1])
        self.assertEqual(samples[0]["matched_fields"], ["canonical_object", "object_raw_surfaces"])

    def test_leading_quote_fragment_row_is_removed_without_quote_term_match(self) -> None:
        module = _load_module()
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE objects (
                _row_id INTEGER PRIMARY KEY,
                canonical_object TEXT,
                object_raw_surfaces TEXT,
                caption_count INTEGER
            )
            """
        )
        conn.execute("INSERT INTO objects VALUES (1, ?, ?, 1)", ("'d", "'d"))
        conn.execute("INSERT INTO objects VALUES (2, ?, ?, 1)", ("dog", "dog"))

        row_ids, samples = module.find_leading_quoted_label_row_ids(
            conn,
            "objects",
            ["canonical_object", "object_raw_surfaces"],
        )

        self.assertEqual(row_ids, [1])
        self.assertEqual(samples[0]["matched_terms"], ["leading_quote_label"])


if __name__ == "__main__":
    unittest.main()
