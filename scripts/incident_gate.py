from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import traceback
from typing import Any, TypeVar
import uuid


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR_ENV = "GPIC_PIPELINE_STATE_DIR"
RUN_TOKEN_ENV = "GPIC_INCIDENT_RUN_TOKEN"
VERIFICATION_INCIDENT_ENV = "GPIC_INCIDENT_VERIFICATION_ID"
DEFAULT_STATE_DIR = ROOT / ".pipeline_state"
INCIDENT_FILE = "incident.json"
RUNNING_FILE = "running.json"
HISTORY_FILE = "incident_history.jsonl"
HISTORY_FALLBACK_PREFIX = "incident_history_fallback"
SCHEMA_VERSION = 1
_T = TypeVar("_T")


class IncidentGateError(RuntimeError):
    pass


class IncidentOpenError(IncidentGateError):
    pass


class PipelineAlreadyRunningError(IncidentGateError):
    pass


def configure_cli_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def state_dir_from_env() -> Path:
    configured = os.environ.get(STATE_DIR_ENV, "").strip()
    return Path(configured).expanduser().resolve() if configured else DEFAULT_STATE_DIR


def incident_path(state_dir: Path | None = None) -> Path:
    return (state_dir or state_dir_from_env()) / INCIDENT_FILE


def running_path(state_dir: Path | None = None) -> Path:
    return (state_dir or state_dir_from_env()) / RUNNING_FILE


def history_path(state_dir: Path | None = None) -> Path:
    return (state_dir or state_dir_from_env()) / HISTORY_FILE


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "status": "unreadable_state",
            "path": str(path),
            "error": repr(exc),
        }
    if not isinstance(payload, dict):
        return {
            "status": "invalid_state_shape",
            "path": str(path),
        }
    return payload


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def write_history_with_fallback(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        append_jsonl(path, payload)
        return {"history_path": str(path), "history_fallback_path": ""}
    except PermissionError as exc:
        first_error = repr(exc)
    fallback_errors: list[str] = []
    for fallback in _history_fallback_candidates(path):
        try:
            append_jsonl(fallback, {**payload, "history_append_error": first_error})
            return {
                "history_path": str(path),
                "history_fallback_path": str(fallback),
                "history_append_error": first_error,
                "history_fallback_errors": fallback_errors,
            }
        except PermissionError as fallback_exc:
            fallback_errors.append(f"{fallback}: {fallback_exc!r}")
    raise PermissionError(
        "could not write incident history or fallback history files: "
        + "; ".join([first_error, *fallback_errors])
    )


def _history_fallback_candidates(path: Path) -> list[Path]:
    file_name = (
        f"{HISTORY_FALLBACK_PREFIX}_"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_"
        f"{uuid.uuid4().hex}.jsonl"
    )
    return [
        path.with_name(file_name),
        Path(tempfile.gettempdir()) / file_name,
    ]


def redact_argv(argv: Sequence[str]) -> list[str]:
    secret_flags = {
        "--password",
        "--password-env-value",
        "--token",
        "--api-key",
        "--secret",
    }
    redacted: list[str] = []
    hide_next = False
    for value in argv:
        if hide_next:
            redacted.append("<redacted>")
            hide_next = False
            continue
        lower = value.lower()
        if lower in secret_flags:
            redacted.append(value)
            hide_next = True
            continue
        if any(lower.startswith(flag + "=") for flag in secret_flags):
            redacted.append(value.split("=", 1)[0] + "=<redacted>")
            continue
        redacted.append(value)
    return redacted


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError as exc:
        if not sys.platform.startswith("linux"):
            return False
        raise IncidentGateError(f"cannot verify process {pid}: {exc}") from exc
    if not sys.platform.startswith("linux"):
        return True
    try:
        # kill(pid, 0) also succeeds for an exited, unreaped child. The comm
        # field may contain spaces/parentheses; state follows its final ')'.
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        state = fields[0]
    except FileNotFoundError:
        return False
    except (OSError, IndexError) as exc:
        raise IncidentGateError(f"cannot read process {pid} state: {exc}") from exc
    if state in {"Z", "X", "x"}:
        return False
    if state not in {"R", "S", "D", "T", "t", "W", "K", "P", "I"}:
        raise IncidentGateError(f"unknown process {pid} state: {state!r}")
    return True


def process_is_running(record: dict[str, Any]) -> bool:
    if record.get("hostname") != socket.gethostname():
        return False
    try:
        pid = int(record["pid"])
    except (KeyError, TypeError, ValueError):
        return False
    return pid_is_running(pid)


def build_incident(
    *,
    failure_type: str,
    summary: str,
    run: dict[str, Any] | None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "incident_id": uuid.uuid4().hex,
        "status": "open",
        "opened_at_utc": utc_now(),
        "failure_type": failure_type,
        "summary": summary,
        "run": run or {},
        "details": details or {},
        "root_cause": "",
        "guard_added": "",
        "verification_evidence": "",
    }


def create_incident(
    *,
    failure_type: str,
    summary: str,
    run: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
    state_dir: Path | None = None,
) -> dict[str, Any]:
    directory = state_dir or state_dir_from_env()
    path = incident_path(directory)
    existing = read_json(path)
    if existing is not None:
        return existing
    payload = build_incident(
        failure_type=failure_type,
        summary=summary,
        run=run,
        details=details,
    )
    write_json_atomic(path, payload)
    return payload


def remove_running_if_token_matches(state_dir: Path, token: str) -> None:
    path = running_path(state_dir)
    record = read_json(path)
    if record is not None and record.get("run_token") == token:
        path.unlink(missing_ok=True)


def promote_unfinished_run_to_incident(
    state_dir: Path,
    record: dict[str, Any],
) -> dict[str, Any]:
    reason = (
        "running marker belongs to another host and cannot be verified"
        if record.get("hostname") != socket.gethostname()
        else "recorded process is no longer running"
    )
    payload = create_incident(
        failure_type="unfinished_or_terminated_run",
        summary=f"Previous official run did not complete: {record.get('run_name', '')}",
        run=record,
        details={"detection_reason": reason},
        state_dir=state_dir,
    )
    running_path(state_dir).unlink(missing_ok=True)
    return payload


def assert_pipeline_clear(*, state_dir: Path | None = None) -> None:
    directory = state_dir or state_dir_from_env()
    incident = read_json(incident_path(directory))
    verification_incident_id = os.environ.get(VERIFICATION_INCIDENT_ENV, "")
    verification_allowed = bool(
        incident is not None
        and verification_incident_id
        and incident.get("incident_id") == verification_incident_id
    )
    if incident is not None and not verification_allowed:
        raise IncidentOpenError(
            f"Official execution blocked by open incident: {incident_path(directory)}\n"
            f"summary={incident.get('summary', '')}"
        )
    running = read_json(running_path(directory))
    if running is None:
        return
    active_token = os.environ.get(RUN_TOKEN_ENV, "")
    if active_token and running.get("run_token") == active_token:
        return
    if process_is_running(running):
        raise PipelineAlreadyRunningError(
            f"Official execution blocked because another run is active: "
            f"{running.get('run_name', '')} pid={running.get('pid', '')}"
        )
    incident = promote_unfinished_run_to_incident(directory, running)
    raise IncidentOpenError(
        f"Official execution blocked after detecting an unfinished run. "
        f"Incident created: {incident_path(directory)}\n"
        f"summary={incident.get('summary', '')}"
    )


class PipelineRun:
    def __init__(
        self,
        run_name: str,
        *,
        argv: Sequence[str] | None = None,
        state_dir: Path | None = None,
    ) -> None:
        self.run_name = run_name
        self.argv = list(argv if argv is not None else sys.argv)
        self.state_dir = state_dir or state_dir_from_env()
        self.run_token = uuid.uuid4().hex
        self.owner = False
        self.previous_token: str | None = None
        self.previous_state_dir: str | None = None
        self.record: dict[str, Any] = {}

    def __enter__(self) -> PipelineRun:
        assert_pipeline_clear(state_dir=self.state_dir)
        active_token = os.environ.get(RUN_TOKEN_ENV, "")
        running = read_json(running_path(self.state_dir))
        if active_token and running is not None and running.get("run_token") == active_token:
            self.run_token = active_token
            self.record = running
            return self

        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.record = {
            "schema_version": SCHEMA_VERSION,
            "status": "running",
            "run_token": self.run_token,
            "run_name": self.run_name,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "cwd": str(Path.cwd()),
            "argv": redact_argv(self.argv),
            "started_at_utc": utc_now(),
        }
        payload = (
            json.dumps(self.record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        path = running_path(self.state_dir)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            assert_pipeline_clear(state_dir=self.state_dir)
            raise PipelineAlreadyRunningError(f"Concurrent run claimed {path}")
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.owner = True
        self.previous_token = os.environ.get(RUN_TOKEN_ENV)
        self.previous_state_dir = os.environ.get(STATE_DIR_ENV)
        os.environ[RUN_TOKEN_ENV] = self.run_token
        os.environ[STATE_DIR_ENV] = str(self.state_dir)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if not self.owner:
            return False
        successful_system_exit = isinstance(exc, SystemExit) and exc.code in (None, 0)
        if exc is None or successful_system_exit:
            remove_running_if_token_matches(self.state_dir, self.run_token)
        elif isinstance(exc, SystemExit):
            returncode = exc.code if isinstance(exc.code, int) else 1
            self.record_nonzero_exit(returncode)
        else:
            details = {
                "exception_type": exc_type.__name__ if exc_type else "",
                "exception": repr(exc),
                "traceback": "".join(traceback.format_exception(exc_type, exc, tb))[-12000:],
            }
            create_incident(
                failure_type="unhandled_exception",
                summary=f"Official run failed: {self.run_name}",
                run=self.record,
                details=details,
                state_dir=self.state_dir,
            )
            remove_running_if_token_matches(self.state_dir, self.run_token)
        if self.previous_token is None:
            os.environ.pop(RUN_TOKEN_ENV, None)
        else:
            os.environ[RUN_TOKEN_ENV] = self.previous_token
        if self.previous_state_dir is None:
            os.environ.pop(STATE_DIR_ENV, None)
        else:
            os.environ[STATE_DIR_ENV] = self.previous_state_dir
        return False

    def record_nonzero_exit(self, returncode: int) -> None:
        if not self.owner:
            return
        create_incident(
            failure_type="nonzero_exit",
            summary=f"Official run exited nonzero: {self.run_name}",
            run=self.record,
            details={"returncode": returncode},
            state_dir=self.state_dir,
        )
        remove_running_if_token_matches(self.state_dir, self.run_token)


def guarded_entrypoint(
    run_name: str,
    function: Callable[[], _T],
    *,
    argv: Sequence[str] | None = None,
    state_dir: Path | None = None,
) -> int:
    with PipelineRun(run_name, argv=argv, state_dir=state_dir) as run:
        result = function()
        returncode = int(result) if isinstance(result, int) else 0
        if returncode != 0:
            run.record_nonzero_exit(returncode)
        return returncode


def record_current_failure(
    *,
    failure_type: str,
    summary: str,
    details: dict[str, Any] | None = None,
) -> None:
    state_dir = state_dir_from_env()
    token = os.environ.get(RUN_TOKEN_ENV, "")
    running = read_json(running_path(state_dir))
    if not token or running is None or running.get("run_token") != token:
        create_incident(
            failure_type=failure_type,
            summary=summary,
            details=details,
            state_dir=state_dir,
        )
        return
    create_incident(
        failure_type=failure_type,
        summary=summary,
        run=running,
        details=details,
        state_dir=state_dir,
    )
    remove_running_if_token_matches(state_dir, token)


def clear_incident(
    *,
    root_cause: str,
    guard_added: str,
    verification_evidence: str,
    verification_command: Sequence[str] | None = None,
    verification_timeout_seconds: int | None = None,
    state_dir: Path | None = None,
) -> dict[str, Any]:
    directory = state_dir or state_dir_from_env()
    incident = read_json(incident_path(directory))
    if incident is None:
        raise IncidentGateError(f"No open incident exists at {incident_path(directory)}")
    required = {
        "root_cause": root_cause.strip(),
        "guard_added": guard_added.strip(),
        "verification_evidence": verification_evidence.strip(),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise IncidentGateError("Cannot clear incident; missing: " + ", ".join(missing))

    running = read_json(running_path(directory))
    if running is not None and process_is_running(running):
        raise IncidentGateError(
            f"Cannot clear incident while recorded process is active: pid={running.get('pid', '')}"
        )

    verification_result: dict[str, Any] = {}
    if verification_command:
        if verification_timeout_seconds is None or verification_timeout_seconds <= 0:
            raise IncidentGateError(
                "--verify-command requires a positive --verify-timeout-seconds; "
                "unbounded incident verification is forbidden"
            )
        command = list(verification_command)
        if command and command[0] == "--":
            command = command[1:]
        if not command:
            raise IncidentGateError("--verify-command requires a command")
        reject_direct_powershell_script_command(command)
        verification_env = os.environ.copy()
        verification_env[STATE_DIR_ENV] = str(directory)
        verification_env[VERIFICATION_INCIDENT_ENV] = str(incident.get("incident_id", ""))
        verification_env.pop(RUN_TOKEN_ENV, None)
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                env=verification_env,
                timeout=verification_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            verification_result = {
                "command": redact_argv(command),
                "timeout_seconds": verification_timeout_seconds,
                "stdout_tail": _timeout_output_tail(exc.stdout),
                "stderr_tail": _timeout_output_tail(exc.stderr),
            }
            raise IncidentGateError(
                "Incident verification command timed out; incident remains open.\n"
                + json.dumps(verification_result, ensure_ascii=False, sort_keys=True)
            ) from exc
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        verification_result = {
            "command": redact_argv(command),
            "returncode": completed.returncode,
            "timeout_seconds": verification_timeout_seconds,
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-4000:],
        }
        if completed.returncode != 0:
            raise IncidentGateError(
                "Incident verification command failed; incident remains open.\n"
                + json.dumps(verification_result, ensure_ascii=False, sort_keys=True)
            )

    resolved = {
        **incident,
        **required,
        "status": "resolved",
        "resolved_at_utc": utc_now(),
        "verification_command_result": verification_result,
    }
    history_result = write_history_with_fallback(history_path(directory), resolved)
    resolved = {**resolved, **history_result}
    incident_path(directory).unlink(missing_ok=True)
    running_path(directory).unlink(missing_ok=True)
    return resolved


def _timeout_output_tail(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[-4000:]


def reject_direct_powershell_script_command(command: Sequence[str]) -> None:
    executable = command[0] if command else ""
    if executable.lower().endswith(".ps1"):
        raise IncidentGateError(
            "Incident verification command cannot execute a .ps1 script "
            "directly. Python subprocess on Windows treats .ps1 files as "
            "non-executable and raises WinError 193. Use python.exe directly "
            "for Python scripts, or invoke powershell.exe -File explicitly."
        )


def run_command(args: argparse.Namespace) -> int:
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("run requires a command after --")
    return guarded_entrypoint(
        args.name or Path(command[0]).name,
        lambda: subprocess.run(command).returncode,
        argv=command,
        state_dir=args.state_dir,
    )


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Block official GPIC execution until failures receive a verified incident review."
    )
    parser.add_argument("--state-dir", type=Path, default=state_dir_from_env())
    subparsers = parser.add_subparsers(dest="action", required=True)

    subparsers.add_parser("status")
    subparsers.add_parser("assert-clear")

    open_parser = subparsers.add_parser("open")
    open_parser.add_argument("--failure-type", required=True)
    open_parser.add_argument("--summary", required=True)
    open_parser.add_argument("--details", default="")

    clear_parser = subparsers.add_parser("clear")
    clear_parser.add_argument("--root-cause", required=True)
    clear_parser.add_argument("--guard-added", required=True)
    clear_parser.add_argument("--verification-evidence", required=True)
    clear_parser.add_argument("--verify-timeout-seconds", type=int)
    clear_parser.add_argument("--verify-command", nargs=argparse.REMAINDER)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--name", default="")
    run_parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    configure_cli_streams()
    args = parse_args(argv)
    args.state_dir = args.state_dir.expanduser().resolve()
    if args.action == "status":
        payload = {
            "state_dir": str(args.state_dir),
            "incident": read_json(incident_path(args.state_dir)),
            "running": read_json(running_path(args.state_dir)),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.action == "assert-clear":
        assert_pipeline_clear(state_dir=args.state_dir)
        print(json.dumps({"status": "clear", "state_dir": str(args.state_dir)}))
        return 0
    if args.action == "open":
        details = {"note": args.details} if args.details else {}
        payload = create_incident(
            failure_type=args.failure_type,
            summary=args.summary,
            details=details,
            state_dir=args.state_dir,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.action == "clear":
        payload = clear_incident(
            root_cause=args.root_cause,
            guard_added=args.guard_added,
            verification_evidence=args.verification_evidence,
            verification_command=args.verify_command,
            verification_timeout_seconds=args.verify_timeout_seconds,
            state_dir=args.state_dir,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.action == "run":
        return run_command(args)
    raise AssertionError(args.action)


if __name__ == "__main__":
    raise SystemExit(main())
