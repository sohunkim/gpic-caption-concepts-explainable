"""Attempt-bound cooperative pause for the fixed-lexicon runners."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import uuid

from gpic_concepts_v1.atomic_io import atomic_text_writer


KIND = "gpic-planned-pause-v1"
STATE_FILE = "execution_control.json"
REQUEST_FILE = "pause_request.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_text_writer(path) as handle:
        json.dump(value, handle, sort_keys=True, indent=2)
        handle.write("\n")


@dataclass(frozen=True)
class PauseControl:
    root: Path
    identity: str
    attempt: str

    @classmethod
    def start(cls, root: Path, identity: str, *, resume: bool = False) -> PauseControl:
        path = root / STATE_FILE
        if path.exists():
            old = _read(path)
            if old.get("kind") != KIND or old.get("identity") != identity:
                raise ValueError("pause control belongs to a different run identity")
            previous = cls(root, identity, old["attempt"])
            if not resume and (old["status"] == "paused" or previous.requested()):
                raise ValueError("planned pause requires explicit --resume")
        control = cls(root, identity, uuid.uuid4().hex)
        control.finish("running")
        return control

    def requested(self) -> bool:
        path = self.root / REQUEST_FILE
        if not path.exists():
            return False
        request = _read(path)
        if request.get("kind") != KIND or request.get("identity") != self.identity:
            raise ValueError("invalid pause request identity")
        return request.get("attempt") == self.attempt

    def finish(self, status: str) -> None:
        _write(self.root / STATE_FILE, {
            "kind": KIND, "identity": self.identity, "attempt": self.attempt,
            "pid": os.getpid(), "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })


def request_pause(root: Path, *, expected_attempt: str | None = None) -> dict:
    path = root / STATE_FILE
    if not path.exists():
        raise ValueError("run has no planned-pause controller; do not signal or patch a legacy run")
    state = _read(path)
    if state.get("kind") != KIND:
        raise ValueError("unsupported planned-pause controller")
    if expected_attempt is not None and state["attempt"] != expected_attempt:
        return {"status": "superseded"}
    if state.get("status") not in {"running", "draining", "paused"}:
        if expected_attempt is not None:
            return {"status": state.get("status")}
        raise ValueError(f"cannot request pause for state {state.get('status')!r}")
    request = {
        "kind": KIND, "identity": state["identity"], "attempt": state["attempt"],
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    _write(root / REQUEST_FILE, request)
    # A concurrent restart must not accidentally pause the new attempt.
    current = _read(path)
    if current["attempt"] != state["attempt"]:
        if expected_attempt is not None:
            return {"status": "superseded"}
        raise RuntimeError("run restarted during pause request; inspect current state and request again")
    return {**request, "status": "pause_requested", "root": str(root)}
