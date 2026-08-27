from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src")]
import planned_pause as pause


def test_pause_is_attempt_bound_and_resume_is_explicit(tmp_path):
    first = pause.PauseControl.start(tmp_path, "identity")
    pause.request_pause(tmp_path)
    assert first.requested()
    first.finish("paused")
    with pytest.raises(ValueError, match="explicit --resume"):
        pause.PauseControl.start(tmp_path, "identity")
    second = pause.PauseControl.start(tmp_path, "identity", resume=True)
    assert second.attempt != first.attempt
    assert not second.requested()
    pause.request_pause(tmp_path)
    assert second.requested()


def test_other_identity_cannot_reuse_pause_state(tmp_path):
    pause.PauseControl.start(tmp_path, "identity").finish("paused")
    with pytest.raises(ValueError, match="different run identity"):
        pause.PauseControl.start(tmp_path, "other", resume=True)


@pytest.mark.parametrize("state", ["failed", "completed"])
def test_terminal_run_cannot_be_paused(tmp_path, state):
    pause.PauseControl.start(tmp_path, "identity").finish(state)
    with pytest.raises(ValueError, match="cannot request pause"):
        pause.request_pause(tmp_path)


def test_legacy_run_is_not_silently_signalled(tmp_path):
    with pytest.raises(ValueError, match="no planned-pause controller"):
        pause.request_pause(tmp_path)


def test_restart_race_is_reported_without_pausing_new_attempt(tmp_path, monkeypatch):
    pause.PauseControl.start(tmp_path, "identity")
    write = pause._write

    def restart_after_request(path, value):
        write(path, value)
        if path.name == pause.REQUEST_FILE:
            pause.PauseControl.start(tmp_path, "identity", resume=True)

    monkeypatch.setattr(pause, "_write", restart_after_request)
    with pytest.raises(RuntimeError, match="restarted during pause request"):
        pause.request_pause(tmp_path)
    state = json.loads((tmp_path / pause.STATE_FILE).read_text())
    current = pause.PauseControl(tmp_path, "identity", state["attempt"])
    assert not current.requested()


def test_forwarded_request_ignores_stale_attempt_and_completed_child(tmp_path):
    first = pause.PauseControl.start(tmp_path, "identity")
    second = pause.PauseControl.start(tmp_path, "identity", resume=True)
    assert pause.request_pause(tmp_path, expected_attempt=first.attempt)["status"] == "superseded"
    assert not second.requested()
    second.finish("completed")
    assert pause.request_pause(tmp_path, expected_attempt=second.attempt)["status"] == "completed"
    assert not second.requested()
