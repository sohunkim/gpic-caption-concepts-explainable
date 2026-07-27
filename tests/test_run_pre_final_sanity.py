from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_pre_final_sanity.py"
SPEC = importlib.util.spec_from_file_location("run_pre_final_sanity", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class RunPreFinalSanityTest(unittest.TestCase):
    def test_fails_when_background_job_is_running(self) -> None:
        args = module.parse_args_from_list_for_test(
            [
                "--background-root",
                "outputs",
                "--fail-if-background-running",
            ]
        )
        with mock.patch.object(
            module,
            "collect_jobs",
            return_value=[{"running": True, "pid": 123}],
        ):
            with self.assertRaisesRegex(SystemExit, "2"):
                module.run_checks_for_test(args)

    def test_runs_git_handoff_and_reports_no_background_jobs(self) -> None:
        args = module.parse_args_from_list_for_test(
            [
                "--repo",
                ".",
                "--expected-commit",
                "1" * 40,
                "--require-clean",
            ]
        )
        with (
            mock.patch.object(
                module,
                "verify_git_handoff",
                return_value={"status": "ok", "commit": "1" * 40},
            ) as verify,
            mock.patch.object(module, "collect_jobs", return_value=[]),
        ):
            summary = module.run_checks_for_test(args)

        verify.assert_called_once()
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["background_jobs"]["running_count"], 0)


if __name__ == "__main__":
    unittest.main()
