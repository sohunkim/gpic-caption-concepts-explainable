from __future__ import annotations

import csv
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "merge_legacy_quantity_into_interactive_report.py"
)
SPEC = importlib.util.spec_from_file_location(
    "merge_legacy_quantity_into_interactive_report",
    SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class MergeLegacyQuantityTest(unittest.TestCase):
    def test_merges_quantity_rows_and_full_caption_index_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "report.db"
            quantity_tsv = root / "quantity_counts.tsv"
            pair_tsv = root / "object_quantity_pair_counts.tsv"
            facts = root / "quantity_facts.jsonl"
            _make_report_db(db)
            _write_tsv(
                quantity_tsv,
                [
                    {
                        "quantity": "2",
                        "count": "3",
                        "caption_count": "2",
                        "example_caption_ids": "c1|c2",
                        "raw_variants": "2",
                    },
                ],
            )
            _write_tsv(
                pair_tsv,
                [
                    {
                        "object": "weevil",
                        "quantity": "2",
                        "count": "2",
                        "caption_count": "2",
                        "example_caption_ids": "c1|c2",
                    },
                ],
            )
            facts.write_text(
                "\n".join(
                    [
                        _fact("c1", "quantity_exists", quantity="2"),
                        _fact("c2", "quantity_exists", quantity="2"),
                        _fact("c1", "has_quantity", object="weevil", quantity="2"),
                        _fact("c2", "has_quantity", object="weevil", quantity="2"),
                    ],
                )
                + "\n",
                encoding="utf-8",
            )

            first = MODULE.merge_legacy_quantity(
                report_db=db,
                quantity_counts_tsv=quantity_tsv,
                object_quantity_pair_counts_tsv=pair_tsv,
                quantity_facts_jsonl=facts,
            )
            with closing(sqlite3.connect(db)) as conn:
                quantity_row_id = conn.execute(
                    "SELECT _row_id FROM attributes "
                    "WHERE canonical_attribute = '2' AND attribute_kind = 'quantity'",
                ).fetchone()[0]
                pair_row_id = conn.execute(
                    "SELECT _row_id FROM attribute_object_pairs "
                    "WHERE object = 'weevil' AND attribute = '2' "
                    "AND attribute_kind = 'quantity'",
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO report_caption_index VALUES (?, ?, ?)",
                    ("attributes", quantity_row_id, "wrong-caption"),
                )
                conn.execute(
                    "INSERT INTO report_caption_index VALUES (?, ?, ?)",
                    ("attribute_object_pairs", pair_row_id, "wrong-caption"),
                )
                conn.commit()
            second = MODULE.merge_legacy_quantity(
                report_db=db,
                quantity_counts_tsv=quantity_tsv,
                object_quantity_pair_counts_tsv=pair_tsv,
                quantity_facts_jsonl=facts,
            )

            self.assertEqual(first["validation"]["status"], "ok")
            self.assertEqual(second["validation"]["status"], "ok")
            self.assertEqual(second["removed_index_rows"], 6)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM attributes "
                        "WHERE canonical_attribute = '2' AND attribute_kind = 'quantity'",
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT attribute_kind FROM attributes "
                        "WHERE canonical_attribute = '3'",
                    ).fetchone()[0],
                    "attribute",
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM attribute_object_pairs "
                        "WHERE object = 'weevil' AND attribute = '2' "
                        "AND attribute_kind = 'quantity'",
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM report_caption_index "
                        "WHERE view_name = 'attributes'",
                    ).fetchone()[0],
                    2,
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT COUNT(*) FROM report_caption_index "
                        "WHERE view_name = 'attribute_object_pairs'",
                    ).fetchone()[0],
                    2,
                )
                views = json.loads(
                    conn.execute(
                        "SELECT value FROM metadata WHERE key = 'views'",
                    ).fetchone()[0],
                )
                columns_by_view = {
                    view["name"]: view["columns"]
                    for view in views
                }
                self.assertEqual(
                    columns_by_view["attributes"][:2],
                    ["canonical_attribute", "attribute_kind"],
                )
                self.assertEqual(
                    columns_by_view["attribute_object_pairs"][1:3],
                    ["attribute", "attribute_kind"],
                )


def _make_report_db(path: Path) -> None:
    views = [
        {
            "name": "attributes",
            "row_count": 1,
            "columns": ["canonical_attribute", "attribute_raw_surfaces"],
        },
        {
            "name": "attribute_object_pairs",
            "row_count": 0,
            "columns": ["object", "attribute", "attribute_raw_surfaces"],
        },
    ]
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE objects (
                _row_id INTEGER PRIMARY KEY,
                canonical_object TEXT,
                object_raw_surfaces TEXT,
                object_parent_concepts TEXT
            );
            INSERT INTO objects VALUES (1, 'weevil', 'weevil', 'beetle');
            CREATE TABLE attributes (
                _row_id INTEGER PRIMARY KEY,
                _caption_ids TEXT,
                canonical_attribute TEXT,
                attribute_raw_surfaces TEXT,
                count INTEGER,
                caption_count INTEGER,
                example_caption_ids TEXT
            );
            INSERT INTO attributes VALUES (
                1, 'c0', '3', '3', 1, 1, 'c0'
            );
            CREATE TABLE attribute_object_pairs (
                _row_id INTEGER PRIMARY KEY,
                _caption_ids TEXT,
                object TEXT,
                object_raw_surfaces TEXT,
                object_parent_concepts TEXT,
                attribute TEXT,
                attribute_raw_surfaces TEXT,
                count INTEGER,
                caption_count INTEGER,
                example_caption_ids TEXT
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
            "INSERT INTO metadata VALUES ('views', ?)",
            (json.dumps(views),),
        )
        conn.commit()


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _fact(caption_id: str, fact_type: str, **values: str) -> str:
    return json.dumps(
        {
            "caption_id": caption_id,
            "fact_type": fact_type,
            "values": values,
        },
        separators=(",", ":"),
    )


if __name__ == "__main__":
    unittest.main()
