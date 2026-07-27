from __future__ import annotations

import json
from contextlib import closing
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_interactive_report_db.py"


class ValidateInteractiveReportDbTest(unittest.TestCase):
    def test_requires_caption_index_summary_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "report.db"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
                )
                conn.execute(
                    "INSERT INTO metadata (key, value) VALUES ('views', ?)",
                    [json.dumps([])],
                )
                conn.execute(
                    "CREATE TABLE report_caption_index "
                    "(view_name TEXT, row_id INTEGER, caption_id TEXT)",
                )
                conn.commit()

            missing = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--report-db",
                    str(db_path),
                    "--require-caption-index",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(missing.returncode, 1)
            self.assertIn("report_caption_index_summary", missing.stdout)

            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    "INSERT INTO metadata (key, value) VALUES "
                    "('report_caption_index_summary', '{}')",
                )
                conn.commit()

            complete = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--report-db",
                    str(db_path),
                    "--require-caption-index",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(complete.returncode, 0, complete.stdout + complete.stderr)

    def test_check_all_caption_counts_detects_non_top_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "report.db"
            with closing(sqlite3.connect(db_path)) as conn:
                conn.executescript(
                    """
                    CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE attributes (
                        _row_id INTEGER PRIMARY KEY,
                        canonical_attribute TEXT,
                        caption_count INTEGER
                    );
                    CREATE TABLE report_caption_index (
                        view_name TEXT NOT NULL,
                        row_id INTEGER NOT NULL,
                        caption_id TEXT NOT NULL,
                        PRIMARY KEY (view_name, row_id, caption_id)
                    ) WITHOUT ROWID;
                    INSERT INTO attributes VALUES (1, 'high', 100);
                    INSERT INTO attributes VALUES (2, 'low', 1);
                    INSERT INTO report_caption_index VALUES ('attributes', 2, 'wrong');
                    INSERT INTO report_caption_index VALUES ('attributes', 2, 'extra');
                    """
                )
                conn.execute(
                    "INSERT INTO metadata (key, value) VALUES ('views', ?)",
                    [
                        json.dumps(
                            [
                                {
                                    "name": "attributes",
                                    "row_count": 2,
                                    "columns": [
                                        "canonical_attribute",
                                        "caption_count",
                                    ],
                                },
                            ],
                        ),
                    ],
                )
                conn.execute(
                    "INSERT INTO metadata (key, value) VALUES "
                    "('report_caption_index_summary', '{}')",
                )
                conn.commit()

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--report-db",
                    str(db_path),
                    "--require-caption-index",
                    "--check-all-caption-counts",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(
                result.returncode,
                1,
                result.stdout + result.stderr,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["caption_mismatch_count"], 2)
