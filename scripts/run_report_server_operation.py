from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

SCRIPTS = Path(__file__).absolute().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from incident_gate import create_incident


CREATE_NEW_PROCESS_GROUP = 0x00000200


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run one report-server manager operation behind an outer hard deadline."
        )
    )
    parser.add_argument("--operation-timeout-seconds", type=int, required=True)
    parser.add_argument("manager_args", nargs=argparse.REMAINDER)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manager_args = list(args.manager_args)
    if manager_args and manager_args[0] == "--":
        manager_args = manager_args[1:]
    if args.operation_timeout_seconds <= 0:
        raise SystemExit("--operation-timeout-seconds must be positive")
    if not manager_args or manager_args[0] not in {"start", "status", "stop"}:
        raise SystemExit("manager operation must be start, status, or stop")

    manager = SCRIPTS / "manage_report_server.py"
    command = [sys.executable, "-u", str(manager), *manager_args]
    return run_bounded_operation(
        command,
        manager_args=manager_args,
        timeout_seconds=args.operation_timeout_seconds,
        state_dir=SCRIPTS.parent / ".pipeline_state",
    )


def run_bounded_operation(
    command: list[str],
    *,
    manager_args: list[str],
    timeout_seconds: int,
    state_dir: Path,
) -> int:
    popen_kwargs: dict[str, object] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **popen_kwargs)
    try:
        return process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        terminate_process_tree(process.pid, timeout_seconds=10)
        owned_server_pid = read_owned_server_pid(manager_args)
        if owned_server_pid is not None:
            terminate_process_tree(owned_server_pid, timeout_seconds=10)
        create_incident(
            failure_type="report_server_operation_timeout",
            summary=(
                "A report-server control operation exceeded its outer hard deadline"
            ),
            details={
                "command": command,
                "manager_pid": process.pid,
                "owned_server_pid": owned_server_pid,
                "timeout_seconds": timeout_seconds,
            },
            state_dir=state_dir,
        )
        print(
            "report-server operation timed out after "
            f"{timeout_seconds}s; its process tree was stopped",
            file=sys.stderr,
            flush=True,
        )
        return 124


def read_owned_server_pid(manager_args: list[str]) -> int | None:
    state_dir = _argument_value(manager_args, "--state-dir")
    if not state_dir:
        return None
    state_name = _argument_value(manager_args, "--state-name") or "report_server"
    pid_file = Path(state_dir).expanduser() / f"{state_name}.pid.json"
    try:
        payload = json.loads(pid_file.read_text(encoding="utf-8"))
        pid = int(payload.get("pid", 0) or 0)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return pid if pid > 0 else None


def _argument_value(arguments: list[str], flag: str) -> str:
    for index, argument in enumerate(arguments):
        if argument == flag and index + 1 < len(arguments):
            return arguments[index + 1]
        if argument.startswith(flag + "="):
            return argument.split("=", 1)[1]
    return ""


def terminate_process_tree(pid: int, *, timeout_seconds: int) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        return

    try:
        process_group = os.getpgid(pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
