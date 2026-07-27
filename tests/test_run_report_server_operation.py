from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import Mock, call, patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_report_server_operation.py"
SPEC = importlib.util.spec_from_file_location("run_report_server_operation", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
controller = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = controller
SPEC.loader.exec_module(controller)


class RunReportServerOperationTest(unittest.TestCase):
    def tearDown(self) -> None:
        shutil.rmtree(
            ROOT / ".tmp_tests" / self.id().replace(".", "_"),
            ignore_errors=True,
        )

    def test_operation_returns_child_exit_code(self) -> None:
        process = Mock(pid=4321)
        process.wait.return_value = 7
        with patch.object(controller.subprocess, "Popen", return_value=process):
            result = controller.run_bounded_operation(
                ["python", "manager.py", "status"],
                manager_args=["status"],
                timeout_seconds=5,
                state_dir=ROOT / ".pipeline_state",
            )
        self.assertEqual(result, 7)
        process.wait.assert_called_once_with(timeout=5)

    def test_timeout_stops_manager_and_recorded_server(self) -> None:
        state_dir = ROOT / ".tmp_tests" / self.id().replace(".", "_")
        state_dir.mkdir(parents=True, exist_ok=True)
        pid_file = state_dir / "test_server.pid.json"
        pid_file.write_text(json.dumps({"pid": 9876}), encoding="utf-8")
        process = Mock(pid=4321)
        process.wait.side_effect = subprocess.TimeoutExpired(["python"], timeout=1)
        manager_args = [
            "start",
            "--state-dir",
            str(state_dir),
            "--state-name",
            "test_server",
        ]
        with (
            patch.object(controller.subprocess, "Popen", return_value=process),
            patch.object(controller, "terminate_process_tree") as terminate,
            patch.object(controller, "create_incident") as incident,
        ):
            result = controller.run_bounded_operation(
                ["python", "manager.py", *manager_args],
                manager_args=manager_args,
                timeout_seconds=1,
                state_dir=ROOT / ".pipeline_state",
            )
        self.assertEqual(result, 124)
        self.assertEqual(
            terminate.call_args_list,
            [
                call(4321, timeout_seconds=10),
                call(9876, timeout_seconds=10),
            ],
        )
        incident.assert_called_once()

    def test_missing_pid_record_does_not_invent_server_pid(self) -> None:
        self.assertIsNone(
            controller.read_owned_server_pid(
                [
                    "start",
                    "--state-dir",
                    str(ROOT / ".tmp_tests" / "missing"),
                    "--state-name",
                    "missing",
                ]
            )
        )
