import importlib.util
from pathlib import Path
import sys
import unittest


def _load_timeout_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_script_with_timeout.py"
    spec = importlib.util.spec_from_file_location("run_script_with_timeout", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


timeout_script = _load_timeout_script()


class RunScriptWithTimeoutGuardTest(unittest.TestCase):
    def test_stage456_scripts_are_blocked_by_default(self) -> None:
        with self.assertRaisesRegex(SystemExit, "Refusing to run Stage 4/5/6"):
            timeout_script._raise_if_forbidden_timeout_target(
                Path("scripts/run_mixed_caption_pipeline.py"),
                ["--limit", "100"],
            )

    def test_stage456_timeout_requires_explicit_override(self) -> None:
        timeout_script._raise_if_forbidden_timeout_target(
            Path("scripts/run_mixed_caption_pipeline.py"),
            ["--limit", "100"],
            allow_stage456_timeout=True,
        )

    def test_stage456_unbounded_foreground_run_is_allowed(self) -> None:
        timeout_script._raise_if_forbidden_timeout_target(
            Path("scripts/run_mixed_caption_pipeline.py"),
            [],
            hard_timeout_enabled=False,
        )

    def test_unbounded_mode_still_rejects_background_launcher(self) -> None:
        with self.assertRaisesRegex(SystemExit, "detached background launcher"):
            timeout_script._raise_if_forbidden_timeout_target(
                Path("scripts/run_background_job.py"),
                ["start"],
                hard_timeout_enabled=False,
            )

    def test_mixed_pipeline_dry_run_is_allowed_through_timeout_wrapper(self) -> None:
        timeout_script._raise_if_forbidden_timeout_target(
            Path("scripts/run_mixed_caption_pipeline.py"),
            ["--dry-run", "--auto-resources"],
        )

    def test_non_stage456_script_is_not_blocked(self) -> None:
        timeout_script._raise_if_forbidden_timeout_target(
            Path("scripts/list_active_background_jobs.py"),
            ["--root", "outputs"],
        )

    def test_background_launcher_is_never_allowed_through_timeout_wrapper(self) -> None:
        with self.assertRaisesRegex(SystemExit, "detached background launcher"):
            timeout_script._raise_if_forbidden_timeout_target(
                Path("scripts/run_background_job.py"),
                ["start"],
            )

    def test_foreground_report_services_are_never_allowed(self) -> None:
        for script_name in (
            "report_server.py",
            "launch_packaged_interactive_report.py",
        ):
            with self.subTest(script_name=script_name):
                with self.assertRaisesRegex(
                    SystemExit,
                    "foreground report service",
                ):
                    timeout_script._raise_if_forbidden_timeout_target(
                        Path(script_name),
                        [],
                    )


if __name__ == "__main__":
    unittest.main()
