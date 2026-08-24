from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_background_job.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("run_background_job", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


background = _load_module()


class BackgroundJobIncidentGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = ROOT / ".tmp_tests" / self.id().replace(".", "_")
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)

    def tearDown(self) -> None:
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def args(self):
        return SimpleNamespace(
            cwd=str(self.tmp),
            stdout=str(self.tmp / "stdout.log"),
            stderr=str(self.tmp / "stderr.log"),
            pid_file=str(self.tmp / "job.json"),
            name="detached-test",
            overwrite_logs=True,
            job_args=["--", sys.executable, "-c", "raise SystemExit(0)"],
        )

    def test_detached_child_is_wrapped_by_incident_runner(self) -> None:
        process = Mock(pid=4321)
        with patch.object(background.subprocess, "Popen", return_value=process) as popen:
            result = background.start_job(self.args())

        self.assertEqual(result, 0)
        command = popen.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(Path(command[1]).name, "incident_gate.py")
        self.assertIn("run", command)
        self.assertIn("detached-test", command)
        child_env = popen.call_args.kwargs["env"]
        self.assertNotIn(background.RUN_TOKEN_ENV, child_env)
        self.assertEqual(
            child_env[background.STATE_DIR_ENV],
            str(self.tmp / ".pipeline_state"),
        )
        record = json.loads((self.tmp / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(record["pid"], 4321)
        self.assertEqual(record["pipeline_state_dir"], str(self.tmp / ".pipeline_state"))

    def test_open_incident_blocks_detached_launch(self) -> None:
        state_dir = self.tmp / ".pipeline_state"
        background.create_incident(
            failure_type="test",
            summary="open incident",
            state_dir=state_dir,
        )

        with (
            patch.object(background.subprocess, "Popen") as popen,
            self.assertRaisesRegex(RuntimeError, "open incident"),
        ):
            background.start_job(self.args())

        popen.assert_not_called()

    def test_windows_ps1_child_is_invoked_through_powershell(self) -> None:
        args = self.args()
        args.job_args = [
            "--",
            ".\\scripts\\run_python.ps1",
            "scripts\\safe_probe.py",
            "probe.sh",
        ]
        process = Mock(pid=4321)

        with (
            patch.object(background, "_is_windows", return_value=True),
            patch.object(background.subprocess, "Popen", return_value=process) as popen,
        ):
            result = background.start_job(args)

        self.assertEqual(result, 0)
        command = popen.call_args.args[0]
        marker = command.index("--")
        child_command = command[marker + 1 :]
        self.assertTrue(child_command[0].lower().endswith("powershell.exe"))
        self.assertEqual(
            child_command[1:6],
            [
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                ".\\scripts\\run_python.ps1",
            ],
        )
        self.assertEqual(
            child_command[6:],
            ["scripts\\safe_probe.py", "probe.sh"],
        )

    def test_local_detached_mlxp_runner_is_rejected(self) -> None:
        args = self.args()
        args.job_args = [
            "--",
            ".\\scripts\\run_python.ps1",
            "scripts\\run_mlxp_bash.py",
            "long_remote_job.sh",
        ]

        with (
            patch.object(background, "_is_windows", return_value=True),
            patch.object(background.subprocess, "Popen") as popen,
            self.assertRaisesRegex(SystemExit, "Do not wrap scripts/run_mlxp_bash.py"),
        ):
            background.start_job(args)

        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
