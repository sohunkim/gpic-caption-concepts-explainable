from __future__ import annotations

import importlib.util
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "reconcile_report_caption_index_kind_collisions.py"
SPEC = importlib.util.spec_from_file_location("reconcile_kind_collisions", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ReconcileReportCaptionIndexKindCollisionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(
            tempfile.mkdtemp(prefix="gpic-kind-collision-test-")
        )
        self.source = self.root / "source.db"
        self.remote = self.root / "remote.db"
        self.patch = self.root / "patch.jsonl"
        _make_db(self.source)
        shutil.copy2(self.source, self.remote)

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def test_export_and_apply_replace_all_colliding_kind_indexes(self) -> None:
        exported = MODULE.export_collision_patch(
            source_db=self.source,
            output_jsonl=self.patch,
            progress_every=100,
        )
        self.assertEqual(exported["target_count"], 4)
        self.assertEqual(exported["caption_records"], 6)

        with closing(sqlite3.connect(self.remote)) as conn:
            conn.execute(
                "DELETE FROM report_caption_index "
                "WHERE view_name IN ('attributes', 'attribute_object_pairs')"
            )
            conn.executemany(
                "INSERT INTO report_caption_index VALUES (?, ?, ?)",
                [
                    ("attributes", 1, "wrong"),
                    ("attributes", 2, "wrong"),
                    ("attribute_object_pairs", 10, "wrong"),
                    ("attribute_object_pairs", 11, "wrong"),
                ],
            )
            conn.commit()

        applied = MODULE.apply_collision_patch(
            report_db=self.remote,
            patch_jsonl=self.patch,
            progress_every=100,
            batch_size=2,
        )
        self.assertEqual(applied["target_count"], 4)
        self.assertEqual(applied["inserted_index_rows"], 6)
        with closing(sqlite3.connect(self.remote)) as conn:
            actual = conn.execute(
                "SELECT view_name, row_id, caption_id FROM report_caption_index "
                "ORDER BY view_name, row_id, caption_id"
            ).fetchall()
            expected = conn.execute(
                "ATTACH DATABASE ? AS source",
                (str(self.source),),
            )
            del expected
            source_rows = conn.execute(
                "SELECT view_name, row_id, caption_id "
                "FROM source.report_caption_index "
                "WHERE row_id IN (1, 2, 10, 11) "
                "ORDER BY view_name, row_id, caption_id"
            ).fetchall()
            self.assertEqual(actual, source_rows)

    def test_remote_caption_count_mismatch_rolls_back(self) -> None:
        MODULE.export_collision_patch(
            source_db=self.source,
            output_jsonl=self.patch,
            progress_every=100,
        )
        with closing(sqlite3.connect(self.remote)) as conn:
            conn.execute(
                "UPDATE attributes SET caption_count = 99 WHERE _row_id = 1"
            )
            conn.commit()
        with self.assertRaisesRegex(ValueError, "remote caption_count mismatch"):
            MODULE.apply_collision_patch(
                report_db=self.remote,
                patch_jsonl=self.patch,
                progress_every=100,
                batch_size=2,
            )
        with closing(sqlite3.connect(self.remote)) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM report_caption_index "
                    "WHERE view_name = 'attributes' AND row_id = 1"
                ).fetchone()[0],
                2,
            )


def _make_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(
            """
            CREATE TABLE attributes (
                _row_id INTEGER PRIMARY KEY,
                canonical_attribute TEXT,
                attribute_kind TEXT,
                caption_count INTEGER
            );
            CREATE TABLE attribute_object_pairs (
                _row_id INTEGER PRIMARY KEY,
                object TEXT,
                attribute TEXT,
                attribute_kind TEXT,
                caption_count INTEGER
            );
            CREATE TABLE report_caption_index (
                view_name TEXT NOT NULL,
                row_id INTEGER NOT NULL,
                caption_id TEXT NOT NULL,
                PRIMARY KEY (view_name, row_id, caption_id)
            ) WITHOUT ROWID;
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        conn.executemany(
            "INSERT INTO attributes VALUES (?, ?, ?, ?)",
            [
                (1, "3", "attribute", 2),
                (2, "3", "quantity", 1),
                (3, "red", "attribute", 1),
            ],
        )
        conn.executemany(
            "INSERT INTO attribute_object_pairs VALUES (?, ?, ?, ?, ?)",
            [
                (10, "weevil", "3", "attribute", 2),
                (11, "weevil", "3", "quantity", 1),
                (12, "weevil", "red", "attribute", 1),
            ],
        )
        conn.executemany(
            "INSERT INTO report_caption_index VALUES (?, ?, ?)",
            [
                ("attributes", 1, "a"),
                ("attributes", 1, "b"),
                ("attributes", 2, "q"),
                ("attribute_object_pairs", 10, "a"),
                ("attribute_object_pairs", 10, "b"),
                ("attribute_object_pairs", 11, "q"),
            ],
        )
        conn.commit()


if __name__ == "__main__":
    unittest.main()
