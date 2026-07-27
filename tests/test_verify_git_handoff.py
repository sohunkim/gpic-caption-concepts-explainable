from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_git_handoff.py"
SPEC = importlib.util.spec_from_file_location("verify_git_handoff", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


@unittest.skipUnless(
    subprocess.run(["git", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode == 0,
    "git executable is required",
)
class VerifyGitHandoffTest(unittest.TestCase):
    def test_accepts_clean_expected_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = _make_repo(Path(temp_dir))
            commit = _git(repo, "rev-parse", "HEAD")

            summary = module.verify_git_handoff(
                repo=repo,
                expected_commit=commit,
                require_clean=True,
            )

            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["commit"], commit)
            self.assertFalse(summary["dirty"])

    def test_rejects_dirty_checkout_when_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = _make_repo(Path(temp_dir))
            commit = _git(repo, "rev-parse", "HEAD")
            (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")

            with self.assertRaisesRegex(SystemExit, "dirty"):
                module.verify_git_handoff(
                    repo=repo,
                    expected_commit=commit,
                    require_clean=True,
                )

    def test_rejects_wrong_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = _make_repo(Path(temp_dir))
            wrong = "0" * 40

            with self.assertRaisesRegex(SystemExit, "commit mismatch"):
                module.verify_git_handoff(
                    repo=repo,
                    expected_commit=wrong,
                    require_clean=False,
                )


def _make_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test User")
    (repo / "tracked.txt").write_text("ok\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "initial")
    return repo


if __name__ == "__main__":
    unittest.main()
