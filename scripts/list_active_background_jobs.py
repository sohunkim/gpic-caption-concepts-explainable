from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from run_background_job import process_matches_record, read_progress_snapshot


COUNT_KEYS = (
    "caption_total",
    "records_processed",
    "records_total",
    "inventory_rows",
    "inventory_rows_so_far",
    "noun_chunk_total",
    "attribute_candidate_total",
    "verb_token_total",
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "List repository background jobs recorded by run_background_job.py "
            "pid files. Use --fail-if-running as a pre-final guard."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("outputs"),
        help="Directory to scan recursively for *.pid.json files.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Show completed/stale jobs too. By default only running jobs are shown.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a compact text table.",
    )
    parser.add_argument(
        "--fail-if-running",
        action="store_true",
        help="Return exit code 2 when at least one active job is found.",
    )
    args = parser.parse_args()

    jobs = collect_jobs(args.root)
    visible = jobs if args.all else [job for job in jobs if job["running"]]

    if args.json:
        print(json.dumps(visible, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_text(visible, root=args.root)

    if args.fail_if_running and any(job["running"] for job in jobs):
        return 2
    return 0


def collect_jobs(root: Path) -> list[dict[str, Any]]:
    resolved_root = root.resolve()
    if not resolved_root.exists():
        return []

    jobs: list[dict[str, Any]] = []
    for pid_file in sorted(resolved_root.rglob("*.pid.json")):
        try:
            record = json.loads(pid_file.read_text(encoding="utf-8"))
        except Exception as exc:
            jobs.append(
                {
                    "pid_file": str(pid_file),
                    "running": False,
                    "status": "unreadable_pid_file",
                    "error": repr(exc),
                }
            )
            continue

        pid = int(record.get("pid", 0) or 0)
        running, process_status, actual_started_at = process_matches_record(record)
        command = record.get("command", [])
        if isinstance(command, str):
            command = command.split()
        progress_path = find_arg_value(command, "--progress-output")
        progress = read_progress_snapshot(Path(progress_path)) if progress_path else {}
        if not progress:
            workflow_progress_path, progress = workflow_progress_from_command(command)
            progress_path = workflow_progress_path or progress_path
        jobs.append(
            {
                "pid_file": str(pid_file),
                "name": record.get("name", ""),
                "pid": pid,
                "running": running,
                "process_status": process_status,
                "actual_process_started_at_utc": actual_started_at,
                "cwd": record.get("cwd", ""),
                "started_at_utc": record.get("started_at_utc", ""),
                "stdout": record.get("stdout", ""),
                "stderr": record.get("stderr", ""),
                "progress_output": progress_path or "",
                "progress": progress,
            }
        )
    return jobs


def find_arg_value(command: list[Any], option: str) -> str:
    items = [str(item) for item in command]
    for index, item in enumerate(items):
        if item == option and index + 1 < len(items):
            return items[index + 1]
        if item.startswith(option + "="):
            return item.split("=", 1)[1]
    return ""


def workflow_progress_from_command(command: list[Any]) -> tuple[str, dict[str, Any]]:
    items = [str(item) for item in command]
    if not any(item.endswith("run_stage35_inventory_workflow.py") for item in items):
        return "", {}
    output_dir = find_arg_value(items, "--output-dir")
    if not output_dir:
        return "", {}
    state_path = Path(output_dir) / "stage35_workflow_state.json"
    if not state_path.exists():
        return str(state_path), {
            "progress_output": str(state_path),
            "progress_file_status": "missing",
            "phase": "stage35_workflow_startup",
        }
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return str(state_path), {
            "progress_output": str(state_path),
            "progress_file_status": "unreadable",
            "error": repr(exc),
        }

    action = str(state.get("last_action", ""))
    progress_path = ""
    if action == "build_attribute_inventory":
        progress_path = str(state.get("attribute_inventory_progress", ""))
    elif action == "build_action_inventory":
        progress_path = str(state.get("action_inventory_progress", ""))

    progress: dict[str, Any] = {}
    if progress_path:
        progress = read_progress_snapshot(Path(progress_path))
        if progress.get("progress_file_status") == "ok":
            progress.setdefault("workflow_status", state.get("status", ""))
            progress.setdefault("workflow_next_required_step", state.get("next_required_step", ""))
            return progress_path, progress

    return str(state_path), {
        "artifact_type": "stage35_inventory_workflow_progress",
        "progress_output": str(state_path),
        "progress_file_status": "ok",
        "status": state.get("status", ""),
        "phase": action,
        "next_required_step": state.get("next_required_step", ""),
    }


def print_text(jobs: list[dict[str, Any]], *, root: Path) -> None:
    if not jobs:
        print(f"No active background jobs found under {root}.")
        return
    for job in jobs:
        progress = job.get("progress") or {}
        counts = ", ".join(
            f"{key}={progress[key]}" for key in COUNT_KEYS if key in progress
        )
        print(
            " | ".join(
                part
                for part in (
                    f"name={job.get('name', '')}",
                    f"pid={job.get('pid', '')}",
                    f"running={job.get('running', False)}",
                    f"phase={progress.get('phase', '')}",
                    f"status={progress.get('status', '')}",
                    counts,
                    f"pid_file={job.get('pid_file', '')}",
                )
                if part
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())
