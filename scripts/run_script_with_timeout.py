from __future__ import annotations

import argparse
import os
import runpy
import sys
import threading
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from incident_gate import guarded_entrypoint, record_current_failure


STAGE456_TIMEOUT_GUARDED_SCRIPTS = frozenset(
    {
        "run_mixed_caption_pipeline.py",
        "run_stage4_extract_raw.py",
        "run_stage5_canonicalize.py",
        "run_stage6_export_counts.py",
    }
)
BACKGROUND_LAUNCHER = "run_background_job.py"
FOREGROUND_SERVICE_SCRIPTS = frozenset(
    {
        "launch_packaged_interactive_report.py",
        "report_server.py",
    }
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a Python script in-process with an optional hard os._exit timeout. "
            "Use --timeout-seconds 0 for a monitored foreground run without a wall-clock kill."
        )
    )
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument(
        "--allow-stage456-timeout",
        action="store_true",
        help=(
            "Explicitly allow the hard timeout wrapper for Stage 4/5/6 scripts. "
            "Use only for deliberately bounded diagnostics, never production-scale runs."
        ),
    )
    parser.add_argument("script")
    args, script_args = parser.parse_known_args()

    if args.timeout_seconds < 0:
        raise SystemExit("--timeout-seconds must be zero or greater.")

    root = Path(__file__).absolute().parent.parent
    script_path = Path(args.script)
    if not script_path.is_absolute():
        script_path = root / script_path
    if not script_path.exists():
        raise SystemExit(f"script not found: {script_path}")
    _raise_if_forbidden_timeout_target(
        script_path,
        script_args,
        allow_stage456_timeout=args.allow_stage456_timeout,
        hard_timeout_enabled=args.timeout_seconds > 0,
    )

    temp_root = script_temp_root(root)
    os.environ["TMP"] = str(temp_root)
    os.environ["TEMP"] = str(temp_root)
    os.environ["TMPDIR"] = str(temp_root)
    os.environ["PYTHONUNBUFFERED"] = "1"

    start = time.perf_counter()
    timer: threading.Timer | None = None
    if args.timeout_seconds > 0:
        timer = threading.Timer(
            args.timeout_seconds,
            timeout_exit,
            kwargs={
                "script": script_path,
                "start": start,
                "timeout_seconds": args.timeout_seconds,
            },
        )
        timer.daemon = True
    old_argv = sys.argv[:]
    try:
        if timer is not None:
            timer.start()
        sys.argv = [str(script_path), *script_args]
        runpy.run_path(str(script_path), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    finally:
        sys.argv = old_argv
        if timer is not None:
            timer.cancel()
    return 0


def _raise_if_forbidden_timeout_target(
    script_path: Path,
    script_args: list[str],
    *,
    allow_stage456_timeout: bool = False,
    hard_timeout_enabled: bool = True,
) -> None:
    if script_path.name == BACKGROUND_LAUNCHER:
        raise SystemExit(
            "Refusing to run the detached background launcher through the hard timeout "
            "wrapper. Launch run_background_job.py directly so its detached child owns "
            "the incident running marker."
        )
    if script_path.name in FOREGROUND_SERVICE_SCRIPTS:
        raise SystemExit(
            "Refusing to run a foreground report service through the script "
            "runner. Use run_report_server_operation.py so start, status, and "
            "stop operations have bounded lifetimes."
        )
    if not hard_timeout_enabled:
        return
    if allow_stage456_timeout:
        return
    if script_path.name not in STAGE456_TIMEOUT_GUARDED_SCRIPTS:
        return
    if script_path.name == "run_mixed_caption_pipeline.py" and _has_flag(script_args, "--dry-run"):
        return
    hint = ""
    if _has_explicit_small_limit(script_args):
        hint = (
            " If this is a deliberately bounded diagnostic, rerun with "
            "--allow-stage456-timeout."
        )
    raise SystemExit(
        "Refusing to run Stage 4/5/6 through the hard timeout wrapper: "
        f"{script_path.name}. Large Stage 4/5/6 jobs have no checkpoint/resume "
        "and must be launched through the monitored background-job path without "
        "a wall-clock kill timeout."
        + hint
    )


def _has_explicit_small_limit(script_args: list[str]) -> bool:
    for index, arg in enumerate(script_args):
        if arg == "--limit" and index + 1 < len(script_args):
            return _safe_int(script_args[index + 1]) is not None
        if arg.startswith("--limit="):
            return _safe_int(arg.split("=", 1)[1]) is not None
    return False


def _has_flag(script_args: list[str], flag: str) -> bool:
    return flag in script_args


def _safe_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def timeout_exit(*, script: Path, start: float, timeout_seconds: int) -> None:
    elapsed = time.perf_counter() - start
    record_current_failure(
        failure_type="hard_timeout",
        summary=f"Timed execution exceeded its hard limit: {script.name}",
        details={
            "script": str(script),
            "elapsed_seconds": round(elapsed, 3),
            "timeout_seconds": timeout_seconds,
        },
    )
    print(
        f"\nSCRIPT_TIMEOUT: killed {script} after "
        f"{elapsed:.3f}s limit={timeout_seconds}s",
        file=sys.stderr,
        flush=True,
    )
    os._exit(124)


def script_temp_root(root: Path) -> Path:
    configured = os.environ.get("GPIC_SCRIPT_TEMP_ROOT")
    if configured:
        temp_root = Path(configured)
    else:
        temp_root = root.parent / ".gpic_tmp" / "gpic-explainable-link-scripts"
    temp_root.mkdir(parents=True, exist_ok=True)
    return temp_root


if __name__ == "__main__":
    raise SystemExit(guarded_entrypoint("bounded_script_runner", main))
