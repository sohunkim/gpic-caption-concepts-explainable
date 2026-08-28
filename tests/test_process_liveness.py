import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import incident_gate as gate
import manage_report_server as report_server
import run_background_job as background
import run_population_continuation as continuation


@pytest.mark.parametrize("state,expected", [("Z", False), ("X", False), ("x", False),
                                          ("R", True), ("S", True), ("D", True), ("T", True)])
def test_linux_state_not_pid_existence(monkeypatch, state, expected):
    monkeypatch.setattr(gate.sys, "platform", "linux")
    monkeypatch.setattr(gate.os, "kill", lambda *_: None)
    monkeypatch.setattr(gate.Path, "read_text", lambda *_args, **_kw: f"42 (worker (test)) {state} 1 0")
    assert gate.pid_is_running(42) is expected


def test_exited_between_kill_and_proc_read(monkeypatch):
    monkeypatch.setattr(gate.sys, "platform", "linux")
    monkeypatch.setattr(gate.os, "kill", lambda *_: None)
    def vanished(*_args, **_kw):
        raise FileNotFoundError
    monkeypatch.setattr(gate.Path, "read_text", vanished)
    assert not gate.pid_is_running(42)


@pytest.mark.parametrize("text", ["broken", "42 (worker) ? 1 0"])
def test_unknown_state_cannot_admit_success(monkeypatch, text):
    monkeypatch.setattr(gate.sys, "platform", "linux")
    monkeypatch.setattr(gate.os, "kill", lambda *_: None)
    monkeypatch.setattr(gate.Path, "read_text", lambda *_args, **_kw: text)
    with pytest.raises(gate.IncidentGateError):
        gate.pid_is_running(42)


def test_permission_error_is_not_completion(monkeypatch):
    monkeypatch.setattr(gate.sys, "platform", "linux")
    def denied(*_):
        raise PermissionError("denied")
    monkeypatch.setattr(gate.os, "kill", denied)
    with pytest.raises(gate.IncidentGateError, match="cannot verify"):
        gate.pid_is_running(42)


def test_windows_missing_pid_retains_previous_behavior(monkeypatch):
    monkeypatch.setattr(gate.sys, "platform", "win32")
    def missing(*_):
        raise OSError("WinError 87: invalid parameter")
    monkeypatch.setattr(gate.os, "kill", missing)
    assert not gate.pid_is_running(999_999_999)


@pytest.fixture
def unreaped_child():
    if not sys.platform.startswith("linux"):
        pytest.skip("requires Linux /proc and unreaped child state")
    child = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if Path(f"/proc/{child.pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z":
                break
            time.sleep(0.01)
        else:
            pytest.fail("test child did not become a zombie within 5 seconds")
        # Do not Popen.poll()/wait() before the assertions: either reaps it.
        yield child.pid
    finally:
        child.wait(timeout=5)


def test_real_zombie_is_stopped_for_all_controllers(unreaped_child):
    pid = unreaped_child
    os.kill(pid, 0)  # Reproduce the original false-positive criterion.
    assert not gate.process_is_running({"pid": pid, "hostname": socket.gethostname()})
    assert background.process_matches_record({"pid": pid})[1] == "not_running"
    assert not report_server.process_is_running(pid)


@pytest.mark.parametrize("has_complete", [True, False])
def test_real_zombie_continuation_requires_complete(unreaped_child, tmp_path, has_complete):
    state = tmp_path / "state"
    state.mkdir()
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"pid": unreaped_child, "pipeline_state_dir": str(state)}))
    config = {"job": str(job), "output": str(tmp_path), "rows": 12, "identity_sha256": "test"}
    if has_complete:
        (tmp_path / "COMPLETE.json").write_text(json.dumps({
            "status": "completed", "input_rows": 12, "identity_sha256": "test"}))
        assert continuation.predecessor_ready(config)
    else:
        with pytest.raises(RuntimeError, match="without COMPLETE"):
            continuation.predecessor_ready(config)


def test_real_zombie_running_marker_becomes_incident(unreaped_child, tmp_path):
    gate.write_json_atomic(gate.running_path(tmp_path), {
        "pid": unreaped_child, "hostname": socket.gethostname(), "run_token": "zombie"})
    with pytest.raises(gate.IncidentOpenError):
        gate.assert_pipeline_clear(state_dir=tmp_path)
    assert gate.read_json(gate.incident_path(tmp_path))["failure_type"] == "unfinished_or_terminated_run"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux integration")
def test_live_process_is_still_live():
    assert gate.pid_is_running(os.getpid())
    assert background.process_matches_record({"pid": os.getpid()})[0]


def test_pid_reuse_check_is_preserved(monkeypatch):
    monkeypatch.setattr(background, "process_is_running", lambda _: True)
    monkeypatch.setattr(background, "process_started_at_utc", lambda _: "2026-08-28T08:00:00+00:00")
    result = background.process_matches_record({"pid": 42, "process_started_at_utc": "2026-08-28T01:00:00+00:00"})
    assert result[1] == "stale_pid_reused"
