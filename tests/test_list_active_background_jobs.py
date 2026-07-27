from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "list_active_background_jobs.py"
if str(SCRIPT.parent) not in sys.path:
    sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("list_active_background_jobs", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ActiveBackgroundJobsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = ROOT / ".tmp_tests" / self.id().replace(".", "_")
        self.tmp.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        for path in sorted(self.tmp.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        self.tmp.rmdir()

    def test_reused_pid_is_not_reported_as_running(self) -> None:
        pid_path = self.tmp / "old.pid.json"
        pid_path.write_text(
            json.dumps(
                {
                    "name": "old-job",
                    "pid": 42,
                    "started_at_utc": "2026-07-15T00:00:00+00:00",
                    "command": ["python", "old_job.py"],
                },
            ),
            encoding="utf-8",
        )
        with patch.object(
            MODULE,
            "process_matches_record",
            return_value=(False, "stale_pid_reused", "2026-07-22T00:00:00+00:00"),
        ):
            jobs = MODULE.collect_jobs(self.tmp)

        self.assertEqual(len(jobs), 1)
        self.assertFalse(jobs[0]["running"])
        self.assertEqual(jobs[0]["process_status"], "stale_pid_reused")
