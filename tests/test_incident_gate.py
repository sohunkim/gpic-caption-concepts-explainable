from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "incident_gate.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("incident_gate", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_module()


class IncidentGateTest(unittest.TestCase):
    def test_cli_streams_use_utf8_when_reconfigure_is_available(self) -> None:
        stdout = Mock()
        stderr = Mock()
        with (
            patch.object(gate.sys, "stdout", stdout),
            patch.object(gate.sys, "stderr", stderr),
        ):
            gate.configure_cli_streams()
        stdout.reconfigure.assert_called_once_with(
            encoding="utf-8",
            errors="backslashreplace",
        )
        stderr.reconfigure.assert_called_once_with(
            encoding="utf-8",
            errors="backslashreplace",
        )

    def setUp(self) -> None:
        self.tmp = ROOT / ".tmp_tests" / self.id().replace(".", "_")
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        self.state_dir = self.tmp / ".pipeline_state"
        self.old_token = os.environ.pop(gate.RUN_TOKEN_ENV, None)
        self.old_state_dir = os.environ.pop(gate.STATE_DIR_ENV, None)

    def tearDown(self) -> None:
        if self.old_token is not None:
            os.environ[gate.RUN_TOKEN_ENV] = self.old_token
        else:
            os.environ.pop(gate.RUN_TOKEN_ENV, None)
        if self.old_state_dir is not None:
            os.environ[gate.STATE_DIR_ENV] = self.old_state_dir
        else:
            os.environ.pop(gate.STATE_DIR_ENV, None)
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_success_removes_running_marker(self) -> None:
        result = gate.guarded_entrypoint(
            "successful-test",
            lambda: 0,
            state_dir=self.state_dir,
        )

        self.assertEqual(result, 0)
        self.assertFalse(gate.running_path(self.state_dir).exists())
        self.assertFalse(gate.incident_path(self.state_dir).exists())

    def test_failure_creates_incident_and_blocks_next_run(self) -> None:
        def fail() -> None:
            raise ValueError("boom")

        with self.assertRaisesRegex(ValueError, "boom"):
            gate.guarded_entrypoint("failing-test", fail, state_dir=self.state_dir)

        incident = gate.read_json(gate.incident_path(self.state_dir))
        self.assertEqual(incident["status"], "open")
        self.assertEqual(incident["failure_type"], "unhandled_exception")
        self.assertFalse(gate.running_path(self.state_dir).exists())
        with self.assertRaises(gate.IncidentOpenError):
            gate.guarded_entrypoint("blocked-test", lambda: 0, state_dir=self.state_dir)

    def test_nonzero_return_creates_incident(self) -> None:
        result = gate.guarded_entrypoint(
            "nonzero-test",
            lambda: 17,
            state_dir=self.state_dir,
        )

        self.assertEqual(result, 17)
        incident = gate.read_json(gate.incident_path(self.state_dir))
        self.assertEqual(incident["failure_type"], "nonzero_exit")
        self.assertEqual(incident["details"]["returncode"], 17)

    def test_zero_system_exit_is_not_an_incident(self) -> None:
        def exit_successfully() -> None:
            raise SystemExit(0)

        with self.assertRaises(SystemExit) as raised:
            gate.guarded_entrypoint(
                "help-test",
                exit_successfully,
                state_dir=self.state_dir,
            )

        self.assertEqual(raised.exception.code, 0)
        self.assertFalse(gate.running_path(self.state_dir).exists())
        self.assertFalse(gate.incident_path(self.state_dir).exists())

    def test_nonzero_system_exit_creates_incident(self) -> None:
        def exit_with_error() -> None:
            raise SystemExit(2)

        with self.assertRaises(SystemExit) as raised:
            gate.guarded_entrypoint(
                "usage-error-test",
                exit_with_error,
                state_dir=self.state_dir,
            )

        self.assertEqual(raised.exception.code, 2)
        incident = gate.read_json(gate.incident_path(self.state_dir))
        self.assertEqual(incident["failure_type"], "nonzero_exit")
        self.assertEqual(incident["details"]["returncode"], 2)

    def test_stale_running_marker_is_promoted_to_incident(self) -> None:
        gate.write_json_atomic(
            gate.running_path(self.state_dir),
            {
                "status": "running",
                "run_token": "stale-token",
                "run_name": "stale-run",
                "pid": 999_999_999,
                "hostname": socket.gethostname(),
            },
        )

        with self.assertRaises(gate.IncidentOpenError):
            gate.assert_pipeline_clear(state_dir=self.state_dir)

        incident = gate.read_json(gate.incident_path(self.state_dir))
        self.assertEqual(incident["failure_type"], "unfinished_or_terminated_run")
        self.assertFalse(gate.running_path(self.state_dir).exists())

    def test_nested_run_reuses_owner_marker(self) -> None:
        with gate.PipelineRun("outer", state_dir=self.state_dir) as outer:
            with gate.PipelineRun("inner", state_dir=self.state_dir) as inner:
                self.assertTrue(outer.owner)
                self.assertFalse(inner.owner)
                running = gate.read_json(gate.running_path(self.state_dir))
                self.assertEqual(running["run_token"], outer.run_token)
        self.assertFalse(gate.running_path(self.state_dir).exists())

    def test_clear_requires_review_and_archives_resolution(self) -> None:
        gate.create_incident(
            failure_type="test_failure",
            summary="test incident",
            state_dir=self.state_dir,
        )

        with self.assertRaisesRegex(gate.IncidentGateError, "missing"):
            gate.clear_incident(
                root_cause="",
                guard_added="guard",
                verification_evidence="test passed",
                state_dir=self.state_dir,
            )

        resolved = gate.clear_incident(
            root_cause="root cause",
            guard_added="durable guard",
            verification_evidence="bounded test passed",
            verification_command=[sys.executable, "-c", "raise SystemExit(0)"],
            verification_timeout_seconds=5,
            state_dir=self.state_dir,
        )

        self.assertEqual(resolved["status"], "resolved")
        self.assertFalse(gate.incident_path(self.state_dir).exists())
        history = [
            json.loads(line)
            for line in gate.history_path(self.state_dir).read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["guard_added"], "durable guard")

    def test_clear_verification_can_run_one_guarded_command(self) -> None:
        gate.create_incident(
            failure_type="test_failure",
            summary="verification incident",
            state_dir=self.state_dir,
        )
        command = [
            sys.executable,
            str(SCRIPT_PATH),
            "--state-dir",
            str(self.state_dir),
            "run",
            "--name",
            "verification-run",
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(0)",
        ]

        resolved = gate.clear_incident(
            root_cause="root cause",
            guard_added="durable guard",
            verification_evidence="guarded smoke run passed",
            verification_command=command,
            verification_timeout_seconds=30,
            state_dir=self.state_dir,
        )

        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(
            resolved["verification_command_result"]["returncode"],
            0,
        )
        self.assertFalse(gate.incident_path(self.state_dir).exists())

    def test_clear_verification_decodes_utf8_output(self) -> None:
        gate.create_incident(
            failure_type="test_failure",
            summary="utf8 verification incident",
            state_dir=self.state_dir,
        )

        resolved = gate.clear_incident(
            root_cause="root cause",
            guard_added="decode verification output as utf-8",
            verification_evidence="verification command emitted utf-8",
            verification_command=[
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write('verified \u2713\\n'.encode('utf-8'))",
            ],
            verification_timeout_seconds=5,
            state_dir=self.state_dir,
        )

        self.assertEqual(resolved["status"], "resolved")
        self.assertIn(
            "verified \u2713",
            resolved["verification_command_result"]["stdout_tail"],
        )

    def test_clear_verification_rejects_direct_powershell_script(self) -> None:
        gate.create_incident(
            failure_type="test_failure",
            summary="ps1 verification incident",
            state_dir=self.state_dir,
        )

        with self.assertRaisesRegex(gate.IncidentGateError, "cannot execute a .ps1"):
            gate.clear_incident(
                root_cause="root cause",
                guard_added="durable guard",
                verification_evidence="verification command should be explicit",
                verification_command=[r".\scripts\run_python.ps1", "-c", "print('bad')"],
                verification_timeout_seconds=5,
                state_dir=self.state_dir,
            )

    def test_clear_verification_requires_positive_timeout(self) -> None:
        gate.create_incident(
            failure_type="test_failure",
            summary="unbounded verification incident",
            state_dir=self.state_dir,
        )

        with self.assertRaisesRegex(
            gate.IncidentGateError,
            "unbounded incident verification is forbidden",
        ):
            gate.clear_incident(
                root_cause="root cause",
                guard_added="bounded verification guard",
                verification_evidence="timeout requirement tested",
                verification_command=[sys.executable, "-c", "raise SystemExit(0)"],
                state_dir=self.state_dir,
            )

        self.assertTrue(gate.incident_path(self.state_dir).exists())

    def test_clear_verification_timeout_keeps_incident_open(self) -> None:
        gate.create_incident(
            failure_type="test_failure",
            summary="timed verification incident",
            state_dir=self.state_dir,
        )

        with self.assertRaisesRegex(
            gate.IncidentGateError,
            "verification command timed out",
        ):
            gate.clear_incident(
                root_cause="root cause",
                guard_added="bounded verification guard",
                verification_evidence="timeout path tested",
                verification_command=[
                    sys.executable,
                    "-c",
                    "import time; time.sleep(10)",
                ],
                verification_timeout_seconds=1,
                state_dir=self.state_dir,
            )

        self.assertTrue(gate.incident_path(self.state_dir).exists())

    def test_history_append_permission_error_uses_fallback_history_file(self) -> None:
        calls = []

        def fake_append(path: Path, payload: dict) -> None:
            calls.append((path, payload))
            if len(calls) == 1:
                raise PermissionError("history locked")

        with patch.object(gate, "append_jsonl", side_effect=fake_append):
            result = gate.write_history_with_fallback(
                self.state_dir / "incident_history.jsonl",
                {"status": "resolved"},
            )

        self.assertEqual(len(calls), 2)
        self.assertEqual(result["history_path"], str(self.state_dir / "incident_history.jsonl"))
        self.assertIn("incident_history_fallback_", result["history_fallback_path"])
        self.assertIn("history_append_error", result)
        self.assertIn("history_append_error", calls[1][1])

    def test_history_append_permission_error_uses_temp_fallback_when_state_dir_is_blocked(self) -> None:
        calls = []
        temp_fallback_dir = self.tmp / "temp_fallback"

        def fake_append(path: Path, payload: dict) -> None:
            calls.append((path, payload))
            if len(calls) <= 2:
                raise PermissionError("history path blocked")

        with (
            patch.object(gate, "append_jsonl", side_effect=fake_append),
            patch.object(gate.tempfile, "gettempdir", return_value=str(temp_fallback_dir)),
        ):
            result = gate.write_history_with_fallback(
                self.state_dir / "incident_history.jsonl",
                {"status": "resolved"},
            )

        self.assertEqual(len(calls), 3)
        self.assertEqual(result["history_path"], str(self.state_dir / "incident_history.jsonl"))
        self.assertIn("incident_history_fallback_", result["history_fallback_path"])
        self.assertTrue(result["history_fallback_path"].startswith(str(temp_fallback_dir)))
        self.assertEqual(len(result["history_fallback_errors"]), 1)
        self.assertIn("history_append_error", calls[2][1])

    def test_explicit_timeout_failure_is_recorded_before_hard_exit(self) -> None:
        with gate.PipelineRun("timeout-test", state_dir=self.state_dir):
            gate.record_current_failure(
                failure_type="hard_timeout",
                summary="timed out",
                details={"timeout_seconds": 1},
            )

        incident = gate.read_json(gate.incident_path(self.state_dir))
        self.assertEqual(incident["failure_type"], "hard_timeout")
        self.assertFalse(gate.running_path(self.state_dir).exists())

    def test_hard_timeout_process_writes_incident_before_os_exit(self) -> None:
        timeout_runner = ROOT / "scripts" / "run_script_with_timeout.py"
        slow_script = ROOT / "tests" / "fixtures" / "incident_gate_sleep.py"
        env = os.environ.copy()
        env[gate.STATE_DIR_ENV] = str(self.state_dir)
        env.pop(gate.RUN_TOKEN_ENV, None)

        completed = subprocess.run(
            [
                sys.executable,
                str(timeout_runner),
                "--timeout-seconds",
                "1",
                "--",
                str(slow_script),
            ],
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )

        self.assertEqual(completed.returncode, 124)
        incident = gate.read_json(gate.incident_path(self.state_dir))
        self.assertEqual(incident["failure_type"], "hard_timeout")
        self.assertEqual(incident["details"]["timeout_seconds"], 1)
        self.assertFalse(gate.running_path(self.state_dir).exists())


if __name__ == "__main__":
    unittest.main()
