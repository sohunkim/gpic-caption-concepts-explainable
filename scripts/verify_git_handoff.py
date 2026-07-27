from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify that a local or remote checkout is pinned to the expected "
            "Git commit before using it for a formal pipeline handoff."
        )
    )
    parser.add_argument("--repo", default=".", type=Path)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = verify_git_handoff(
        repo=args.repo,
        expected_commit=args.expected_commit,
        require_clean=args.require_clean,
    )
    payload = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    print(payload)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(payload + "\n", encoding="utf-8")
    return 0


def verify_git_handoff(
    *,
    repo: Path,
    expected_commit: str,
    require_clean: bool,
) -> dict[str, Any]:
    repo = repo.expanduser()
    expected_commit = expected_commit.strip().lower()
    if len(expected_commit) != 40 or any(ch not in "0123456789abcdef" for ch in expected_commit):
        raise SystemExit("--expected-commit must be a full 40-character lowercase SHA-1 commit")

    actual_commit = _git(repo, "rev-parse", "HEAD").strip().lower()
    branch = _git(repo, "branch", "--show-current").strip()
    status = _git(repo, "status", "--porcelain").splitlines()
    remote = _optional_git(repo, "remote", "get-url", "origin").strip()

    if actual_commit != expected_commit:
        raise SystemExit(
            f"Git handoff commit mismatch: expected {expected_commit}, got {actual_commit}"
        )
    if require_clean and status:
        preview = "; ".join(status[:10])
        raise SystemExit(f"Git handoff checkout is dirty: {preview}")

    return {
        "status": "ok",
        "repo": str(repo),
        "commit": actual_commit,
        "branch": branch,
        "remote_origin": remote,
        "dirty": bool(status),
        "dirty_count": len(status),
    }


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result.stdout


def _optional_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


if __name__ == "__main__":
    raise SystemExit(main())
