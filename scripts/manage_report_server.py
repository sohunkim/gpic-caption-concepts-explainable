from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start, inspect, or stop a report server without foreground waits."
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    start = subparsers.add_parser("start")
    _add_common_arguments(start)
    start.add_argument("--readiness-timeout-seconds", type=int, required=True)
    start.add_argument("--overwrite-logs", action="store_true")

    status = subparsers.add_parser("status")
    _add_common_arguments(status)

    stop = subparsers.add_parser("stop")
    _add_common_arguments(stop)
    stop.add_argument("--stop-timeout-seconds", type=int, required=True)
    return parser.parse_args()


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--state-name", default="report_server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--user", default="gpic")
    parser.add_argument("--password", default="")


def main() -> int:
    args = parse_args()
    report_dir = args.report_dir.expanduser().resolve()
    state_dir = (
        args.state_dir.expanduser().resolve()
        if args.state_dir is not None
        else report_dir / ".server_state"
    )
    state_name = validate_state_name(args.state_name)
    if args.action == "start":
        return start_server(
            report_dir=report_dir,
            state_dir=state_dir,
            state_name=state_name,
            host=args.host,
            port=args.port,
            user=args.user,
            password=args.password,
            readiness_timeout_seconds=args.readiness_timeout_seconds,
            overwrite_logs=args.overwrite_logs,
        )
    if args.action == "status":
        return status_server(
            report_dir=report_dir,
            state_dir=state_dir,
            state_name=state_name,
            host=args.host,
            port=args.port,
            user=args.user,
            password=args.password,
        )
    if args.action == "stop":
        return stop_server(
            report_dir=report_dir,
            state_dir=state_dir,
            state_name=state_name,
            stop_timeout_seconds=args.stop_timeout_seconds,
        )
    raise AssertionError(args.action)


def start_server(
    *,
    report_dir: Path,
    state_dir: Path,
    state_name: str,
    host: str,
    port: int,
    user: str,
    password: str,
    readiness_timeout_seconds: int,
    overwrite_logs: bool,
) -> int:
    if readiness_timeout_seconds <= 0:
        raise SystemExit("--readiness-timeout-seconds must be positive")
    server_script = report_dir / "report_server.py"
    if not server_script.is_file():
        raise SystemExit(f"report_server.py does not exist: {server_script}")

    paths = state_paths(state_dir, state_name)
    paths["state_dir"].mkdir(parents=True, exist_ok=True)
    existing = read_json(paths["pid"])
    if existing and process_matches_record(existing):
        if health_ready(host, port, user, password, timeout_seconds=2):
            print_json({**existing, "status": "already_ready"})
            return 0
        raise SystemExit(
            f"Recorded report server pid={existing.get('pid')} is running but not ready"
        )
    if port_accepts_connections(host, port, timeout_seconds=1):
        raise SystemExit(
            f"{host}:{port} is already accepting connections without a matching PID record"
        )

    mode = "w" if overwrite_logs else "a"
    child_env = os.environ.copy()
    child_env.update(
        {
            "REPORT_HOST": host,
            "REPORT_PORT": str(port),
            "REPORT_USER": user,
            "REPORT_PASSWORD": password,
            "REPORT_OPEN_BROWSER": "0",
        }
    )
    flags = 0
    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        flags = CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_NO_WINDOW
    else:
        popen_kwargs["start_new_session"] = True

    with paths["stdout"].open(mode, encoding="utf-8") as stdout, paths["stderr"].open(
        mode, encoding="utf-8"
    ) as stderr:
        process = subprocess.Popen(
            [sys.executable, "-u", str(server_script)],
            cwd=report_dir,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
            creationflags=flags,
            env=child_env,
            **popen_kwargs,
        )

    record = {
        "pid": process.pid,
        "report_dir": str(report_dir),
        "host": host,
        "port": port,
        "user": user,
        "stdout": str(paths["stdout"]),
        "stderr": str(paths["stderr"]),
        "started_at_utc": utc_now(),
        "process_started_at_utc": process_started_at_utc(process.pid),
    }
    write_json(paths["pid"], record)

    deadline = time.monotonic() + readiness_timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise_server_start_error(
                record,
                f"report server exited before readiness with code {process.returncode}",
            )
        if health_ready(host, port, user, password, timeout_seconds=2):
            print_json({**record, "status": "ready"})
            return 0
        time.sleep(0.5)

    terminate_process_tree(process.pid, timeout_seconds=10)
    raise_server_start_error(
        record,
        f"report server readiness timed out after {readiness_timeout_seconds} seconds",
    )
    return 1


def status_server(
    *,
    report_dir: Path,
    state_dir: Path,
    state_name: str,
    host: str,
    port: int,
    user: str,
    password: str,
) -> int:
    paths = state_paths(state_dir, state_name)
    record = read_json(paths["pid"])
    if record is None:
        print_json({"status": "missing_pid_record", "pid_file": str(paths["pid"])})
        return 2
    running = process_matches_record(record)
    ready = running and health_ready(host, port, user, password, timeout_seconds=2)
    print_json({**record, "running": running, "ready": ready})
    return 0 if ready else 1


def stop_server(
    *,
    report_dir: Path,
    state_dir: Path,
    state_name: str,
    stop_timeout_seconds: int,
) -> int:
    if stop_timeout_seconds <= 0:
        raise SystemExit("--stop-timeout-seconds must be positive")
    paths = state_paths(state_dir, state_name)
    record = read_json(paths["pid"])
    if record is None:
        print_json({"status": "already_stopped", "pid_file": str(paths["pid"])})
        return 0
    pid = int(record.get("pid", 0) or 0)
    if pid > 0 and process_matches_record(record):
        terminate_process_tree(pid, timeout_seconds=stop_timeout_seconds)
    write_json(
        paths["pid"],
        {**record, "status": "stopped", "stopped_at_utc": utc_now()},
    )
    print_json({**record, "status": "stopped"})
    return 0


def state_paths(state_dir: Path, state_name: str) -> dict[str, Path]:
    return {
        "state_dir": state_dir,
        "pid": state_dir / f"{state_name}.pid.json",
        "stdout": state_dir / f"{state_name}.stdout.log",
        "stderr": state_dir / f"{state_name}.stderr.log",
    }


def validate_state_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise SystemExit(
            "--state-name may contain only ASCII letters, digits, dot, underscore, or hyphen"
        )
    return value


def health_ready(
    host: str,
    port: int,
    user: str,
    password: str,
    *,
    timeout_seconds: int,
) -> bool:
    url_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    request = Request(f"http://{url_host}:{port}/healthz")
    if password:
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return response.status == 200
    except (HTTPError, URLError, OSError, TimeoutError):
        return False


def port_accepts_connections(host: str, port: int, *, timeout_seconds: int) -> bool:
    return health_ready(host, port, "", "", timeout_seconds=timeout_seconds)


def terminate_process_tree(pid: int, *, timeout_seconds: int) -> None:
    if os.name == "nt":
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if completed.returncode not in {0, 128} and process_is_running(pid):
            raise RuntimeError(
                f"taskkill failed for pid={pid}: {(completed.stderr or completed.stdout).strip()}"
            )
        return

    try:
        process_group = os.getpgid(pid)
    except ProcessLookupError:
        return
    os.killpg(process_group, signal.SIGTERM)
    deadline = time.monotonic() + timeout_seconds
    while process_is_running(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if process_is_running(pid):
        os.killpg(process_group, signal.SIGKILL)


def process_matches_record(record: dict[str, Any]) -> bool:
    pid = int(record.get("pid", 0) or 0)
    if pid <= 0:
        return False
    expected = str(record.get("process_started_at_utc") or "")
    actual = process_started_at_utc(pid)
    if os.name == "nt":
        if not actual:
            return False
    elif not process_is_running(pid):
        return False
    return not expected or not actual or expected == actual


def process_is_running(pid: int) -> bool:
    if os.name == "nt":
        return bool(process_started_at_utc(pid))
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def process_started_at_utc(pid: int) -> str:
    if os.name != "nt":
        return ""
    ticks = _windows_process_creation_ticks(pid)
    if ticks is None:
        return ""
    whole_seconds, fractional_ticks = divmod(ticks, 10_000_000)
    created = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(
        seconds=whole_seconds
    )
    return f"{created:%Y-%m-%dT%H:%M:%S}.{fractional_ticks:07d}Z"


def _windows_process_creation_ticks(pid: int) -> int | None:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(
        process_query_limited_information,
        False,
        pid,
    )
    if not handle:
        return None
    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    try:
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
    finally:
        kernel32.CloseHandle(handle)
    return (creation.dwHighDateTime << 32) | creation.dwLowDateTime


def raise_server_start_error(record: dict[str, Any], message: str) -> None:
    stderr_tail = read_tail(Path(str(record["stderr"])))
    stdout_tail = read_tail(Path(str(record["stdout"])))
    raise SystemExit(
        json.dumps(
            {
                **record,
                "status": "start_failed",
                "message": message,
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def read_tail(path: Path, limit: int = 4000) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[-limit:]


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
