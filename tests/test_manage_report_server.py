from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "manage_report_server.py"
SPEC = importlib.util.spec_from_file_location("manage_report_server", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
manager = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = manager
SPEC.loader.exec_module(manager)


class ManageReportServerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.report_dir = ROOT / ".tmp_tests" / self.id().replace(".", "_")
        self.report_dir.mkdir(parents=True, exist_ok=True)
        (self.report_dir / "report_server.py").write_text(
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.report_dir, ignore_errors=True)

    def test_start_requires_positive_readiness_timeout(self) -> None:
        with self.assertRaisesRegex(SystemExit, "must be positive"):
            manager.start_server(
                report_dir=self.report_dir,
                state_dir=self.report_dir / "state",
                state_name="test_server",
                host="127.0.0.1",
                port=8765,
                user="gpic",
                password="1234",
                readiness_timeout_seconds=0,
                overwrite_logs=True,
            )

    def test_start_detaches_and_returns_after_health_is_ready(self) -> None:
        process = Mock(pid=4321)
        process.poll.return_value = None
        with (
            patch.object(manager, "port_accepts_connections", return_value=False),
            patch.object(manager, "process_started_at_utc", return_value="started"),
            patch.object(manager, "health_ready", return_value=True),
            patch.object(manager.subprocess, "Popen", return_value=process) as popen,
        ):
            result = manager.start_server(
                report_dir=self.report_dir,
                state_dir=self.report_dir / "state",
                state_name="test_server",
                host="127.0.0.1",
                port=8765,
                user="gpic",
                password="1234",
                readiness_timeout_seconds=5,
                overwrite_logs=True,
            )

        self.assertEqual(result, 0)
        command = popen.call_args.args[0]
        self.assertEqual(command[:2], [sys.executable, "-u"])
        self.assertTrue(command[2].endswith("report_server.py"))
        self.assertTrue(
            manager.state_paths(self.report_dir / "state", "test_server")["pid"].exists()
        )

    def test_readiness_timeout_terminates_owned_process(self) -> None:
        process = Mock(pid=4321)
        process.poll.return_value = None
        with (
            patch.object(manager, "port_accepts_connections", return_value=False),
            patch.object(manager, "process_started_at_utc", return_value="started"),
            patch.object(manager, "health_ready", return_value=False),
            patch.object(manager.time, "monotonic", side_effect=[0.0, 2.0]),
            patch.object(manager.subprocess, "Popen", return_value=process),
            patch.object(manager, "terminate_process_tree") as terminate,
            self.assertRaisesRegex(SystemExit, "readiness timed out"),
        ):
            manager.start_server(
                report_dir=self.report_dir,
                state_dir=self.report_dir / "state",
                state_name="test_server",
                host="127.0.0.1",
                port=8765,
                user="gpic",
                password="1234",
                readiness_timeout_seconds=1,
                overwrite_logs=True,
            )

        terminate.assert_called_once_with(4321, timeout_seconds=10)

    def test_windows_process_start_time_uses_native_creation_ticks(self) -> None:
        unix_epoch_ticks = 11644473600 * 10_000_000
        with (
            patch.object(manager.os, "name", "nt"),
            patch.object(
                manager.subprocess,
                "run",
            ) as run,
            patch.object(
                manager,
                "_windows_process_creation_ticks",
                return_value=unix_epoch_ticks,
            ),
        ):
            self.assertEqual(
                manager.process_started_at_utc(42),
                "1970-01-01T00:00:00.0000000Z",
            )

        run.assert_not_called()

    def test_windows_process_running_uses_bounded_process_probe(self) -> None:
        with (
            patch.object(manager.os, "name", "nt"),
            patch.object(
                manager,
                "process_started_at_utc",
                side_effect=["started", ""],
            ),
        ):
            self.assertTrue(manager.process_is_running(42))
            self.assertFalse(manager.process_is_running(43))

    def test_state_name_rejects_path_separators(self) -> None:
        with self.assertRaisesRegex(SystemExit, "state-name"):
            manager.validate_state_name("../server")
