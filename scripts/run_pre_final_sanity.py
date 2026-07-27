from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from list_active_background_jobs import collect_jobs
from verify_git_handoff import verify_git_handoff


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run serial pre-final sanity checks. This avoids launching multiple "
            "incident-guarded scripts in parallel."
        )
    )
    parser.add_argument("--repo", default=".", type=Path)
    parser.add_argument("--expected-commit")
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--background-root", default="outputs", type=Path)
    parser.add_argument("--fail-if-background-running", action="store_true")
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    summary = run_checks(args)
    _emit(summary, args.output_json)
    if summary["status"] != "ok":
        return 2
    return 0


def run_checks(args: argparse.Namespace) -> dict[str, Any]:
    summary: dict[str, Any] = {"status": "ok"}

    if args.expected_commit:
        summary["git_handoff"] = verify_git_handoff(
            repo=args.repo,
            expected_commit=args.expected_commit,
            require_clean=args.require_clean,
        )

    jobs = collect_jobs(args.background_root)
    running_jobs = [job for job in jobs if job.get("running")]
    summary["background_jobs"] = {
        "root": str(args.background_root),
        "running_count": len(running_jobs),
        "running": running_jobs,
    }
    if args.fail_if_background_running and running_jobs:
        summary["status"] = "failed"

    return summary


def parse_args_from_list_for_test(argv: list[str]) -> argparse.Namespace:
    return parse_args(argv)


def run_checks_for_test(args: argparse.Namespace) -> dict[str, Any]:
    summary = run_checks(args)
    if summary["status"] != "ok":
        raise SystemExit(2)
    return summary


def _emit(summary: dict[str, Any], output_json: Path | None) -> None:
    payload = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    print(payload)
    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(payload + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
