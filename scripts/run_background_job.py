from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from incident_gate import (
    RUN_TOKEN_ENV,
    STATE_DIR_ENV,
    assert_pipeline_clear,
    create_incident,
)


CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Start or inspect a detached repository job without PowerShell "
            "Start-Process or cmd.exe start."
        )
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    start = subparsers.add_parser("start")
    start.add_argument("--cwd", required=True)
    start.add_argument("--stdout", required=True)
    start.add_argument("--stderr", required=True)
    start.add_argument("--pid-file", required=True)
    start.add_argument("--name", default="")
    start.add_argument("--overwrite-logs", action="store_true")
    start.add_argument("job_args", nargs=argparse.REMAINDER)

    status = subparsers.add_parser("status")
    status.add_argument("--pid-file", required=True)
    status.add_argument("--progress-output")

    watch = subparsers.add_parser("watch")
    watch.add_argument("--pid-file", required=True)
    watch.add_argument("--interval-seconds", type=int, default=60)
    watch.add_argument("--max-seconds", type=int, default=3600)
    watch.add_argument("--expect-output")
    watch.add_argument("--progress-output")

    adopt = subparsers.add_parser("adopt")
    adopt.add_argument("--pid", type=int, required=True)
    adopt.add_argument("--pid-file", required=True)
    adopt.add_argument("--name", default="")
    adopt.add_argument("--cwd", required=True)
    adopt.add_argument("--stdout", default="")
    adopt.add_argument("--stderr", default="")
    adopt.add_argument("--job-command", default="")

    args = parser.parse_args()
    if args.action == "start":
        return start_job(args)
    if args.action == "status":
        return status_job(args)
    if args.action == "watch":
        return watch_job(args)
    if args.action == "adopt":
        return adopt_job(args)
    raise AssertionError(args.action)


def start_job(args: argparse.Namespace) -> int:
    job_args = list(args.job_args)
    if job_args and job_args[0] == "--":
        job_args = job_args[1:]
    if not job_args:
        raise SystemExit("start requires a command after --")
    job_args = normalize_child_command(job_args)
    reject_local_detached_mlxp_command(job_args)

    cwd = Path(args.cwd).resolve()
    if not cwd.exists():
        raise SystemExit(f"cwd does not exist: {cwd}")
    state_dir = cwd / ".pipeline_state"
    assert_pipeline_clear(state_dir=state_dir)

    incident_runner = Path(__file__).with_name("incident_gate.py").resolve()
    guarded_job_args = [
        sys.executable,
        str(incident_runner),
        "--state-dir",
        str(state_dir),
        "run",
        "--name",
        args.name or Path(job_args[0]).name,
        "--",
        *job_args,
    ]
    child_env = os.environ.copy()
    child_env.pop(RUN_TOKEN_ENV, None)
    child_env[STATE_DIR_ENV] = str(state_dir)

    stdout_path = Path(args.stdout).resolve()
    stderr_path = Path(args.stderr).resolve()
    pid_path = Path(args.pid_file).resolve()
    for path in (stdout_path, stderr_path, pid_path):
        path.parent.mkdir(parents=True, exist_ok=True)

    mode = "w" if args.overwrite_logs else "a"
    with stdout_path.open(mode, encoding="utf-8") as stdout, stderr_path.open(
        mode, encoding="utf-8"
    ) as stderr:
        flags = 0
        if os.name == "nt":
            flags = CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
        try:
            process = subprocess.Popen(
                guarded_job_args,
                cwd=cwd,
                stdout=stdout,
                stderr=stderr,
                stdin=subprocess.DEVNULL,
                creationflags=flags,
                close_fds=True,
                env=child_env,
            )
        except BaseException as exc:
            create_incident(
                failure_type="background_launch_failure",
                summary=f"Failed to launch detached job: {args.name or job_args[0]}",
                details={
                    "cwd": str(cwd),
                    "command": job_args,
                    "exception": repr(exc),
                },
                state_dir=state_dir,
            )
            raise

    record = {
        "name": args.name,
        "pid": process.pid,
        "cwd": str(cwd),
        "command": job_args,
        "guarded_command": guarded_job_args,
        "pipeline_state_dir": str(state_dir),
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "process_started_at_utc": process_started_at_utc(process.pid),
    }
    write_json_atomic(pid_path, record)
    print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return 0


def status_job(args: argparse.Namespace) -> int:
    pid_path = Path(args.pid_file).resolve()
    if not pid_path.exists():
        print(json.dumps({"status": "missing_pid_file", "pid_file": str(pid_path)}))
        return 2
    record = json.loads(pid_path.read_text(encoding="utf-8"))
    running, process_status, actual_started_at = process_matches_record(record)
    record["running"] = running
    record["process_status"] = process_status
    record["actual_process_started_at_utc"] = actual_started_at
    if args.progress_output:
        record["progress"] = read_progress_snapshot(Path(args.progress_output))
    print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return 0


def watch_job(args: argparse.Namespace) -> int:
    if args.interval_seconds < 1:
        raise SystemExit("--interval-seconds must be >= 1")
    if args.max_seconds < 1:
        raise SystemExit("--max-seconds must be >= 1")
    pid_path = Path(args.pid_file).resolve()
    if not pid_path.exists():
        print(json.dumps({"status": "missing_pid_file", "pid_file": str(pid_path)}))
        return 2

    started = time.monotonic()
    last_record: dict | None = None
    while True:
        record = json.loads(pid_path.read_text(encoding="utf-8"))
        running, process_status, actual_started_at = process_matches_record(record)
        output_exists = bool(args.expect_output and Path(args.expect_output).exists())
        last_record = {
            **record,
            "running": running,
            "process_status": process_status,
            "actual_process_started_at_utc": actual_started_at,
            "expect_output": args.expect_output or "",
            "expect_output_exists": output_exists,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        if args.progress_output:
            last_record["progress"] = read_progress_snapshot(Path(args.progress_output))
        if not running or output_exists:
            print(json.dumps(last_record, ensure_ascii=False, sort_keys=True))
            return 0
        if time.monotonic() - started >= args.max_seconds:
            last_record["status"] = "watch_timeout"
            print(json.dumps(last_record, ensure_ascii=False, sort_keys=True))
            return 124
        time.sleep(args.interval_seconds)


def adopt_job(args: argparse.Namespace) -> int:
    if not process_is_running(args.pid):
        raise SystemExit(f"pid is not running: {args.pid}")
    pid_path = Path(args.pid_file).resolve()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "name": args.name,
        "pid": args.pid,
        "cwd": str(Path(args.cwd).resolve()),
        "command": args.job_command,
        "stdout": args.stdout,
        "stderr": args.stderr,
        "started_at_utc": "",
        "adopted_at_utc": datetime.now(timezone.utc).isoformat(),
        "process_started_at_utc": process_started_at_utc(args.pid),
    }
    write_json_atomic(pid_path, record)
    print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return 0


def normalize_child_command(job_args: list[str]) -> list[str]:
    if os.name != "nt" or not job_args:
        return job_args
    executable = job_args[0]
    if not executable.lower().endswith(".ps1"):
        return job_args
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    powershell = (
        Path(system_root)
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    return [
        str(powershell),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        executable,
        *job_args[1:],
    ]


def reject_local_detached_mlxp_command(job_args: list[str]) -> None:
    if any(is_run_mlxp_bash_arg(arg) for arg in job_args):
        raise SystemExit(
            "Do not wrap scripts/run_mlxp_bash.py in a local detached background "
            "job. Launch long MLXP work inside the pod and poll its progress "
            "from a separate guarded probe."
        )


def is_run_mlxp_bash_arg(arg: str) -> bool:
    normalized = arg.replace("\\", "/").lower()
    return normalized == "run_mlxp_bash.py" or normalized.endswith("/run_mlxp_bash.py")


def process_is_running(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def process_started_at_utc(pid: int) -> str:
    if os.name != "nt":
        return _posix_process_started_at_utc(pid)

    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ""
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return ""
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        unix_seconds = ticks / 10_000_000 - 11_644_473_600
        return datetime.fromtimestamp(unix_seconds, timezone.utc).isoformat()
    finally:
        kernel32.CloseHandle(handle)


def process_matches_record(
    record: dict[str, object],
    *,
    tolerance_seconds: float = 300.0,
) -> tuple[bool, str, str]:
    pid = int(record.get("pid", 0) or 0)
    if not pid or not process_is_running(pid):
        return False, "not_running", ""
    actual_started_at = process_started_at_utc(pid)
    expected_started_at = str(
        record.get("process_started_at_utc")
        or record.get("started_at_utc")
        or "",
    )
    if actual_started_at and expected_started_at:
        try:
            actual = datetime.fromisoformat(actual_started_at)
            expected = datetime.fromisoformat(expected_started_at)
        except ValueError:
            return True, "running_start_time_unparseable", actual_started_at
        if abs((actual - expected).total_seconds()) > tolerance_seconds:
            return False, "stale_pid_reused", actual_started_at
    return True, "running", actual_started_at


def _posix_process_started_at_utc(pid: int) -> str:
    try:
        stat_fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
        start_ticks = int(stat_fields[21])
        clock_ticks = int(os.sysconf("SC_CLK_TCK"))
        boot_line = next(
            line
            for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines()
            if line.startswith("btime ")
        )
        boot_seconds = int(boot_line.split()[1])
    except (OSError, ValueError, IndexError, StopIteration):
        return ""
    started = boot_seconds + start_ticks / clock_ticks
    return datetime.fromtimestamp(started, timezone.utc).isoformat()


def write_json_atomic(path: Path, payload: dict) -> None:
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def read_progress_snapshot(path: Path) -> dict:
    resolved = path.resolve()
    if not resolved.exists():
        return {
            "progress_output": str(resolved),
            "progress_file_status": "missing",
        }
    try:
        progress = json.loads(resolved.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "progress_output": str(resolved),
            "progress_file_status": "unreadable",
            "error": repr(exc),
        }
    if isinstance(progress, dict):
        progress.setdefault("progress_output", str(resolved))
        progress.setdefault("progress_file_status", "ok")
        return progress
    return {
        "progress_output": str(resolved),
        "progress_file_status": "invalid_json_shape",
    }


if __name__ == "__main__":
    raise SystemExit(main())
