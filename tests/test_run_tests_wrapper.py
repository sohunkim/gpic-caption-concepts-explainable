from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUN_TESTS = ROOT / "scripts" / "run_tests.ps1"


class RunTestsWrapperTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("powershell.exe"), "requires Windows PowerShell")
    def test_two_invocations_restore_test_temp_environment(self) -> None:
        original_temp = os.environ.get("TEMP", "")
        command = (
            "$before=$env:TEMP; "
            f"& '{RUN_TESTS}' --pytest -q "
            "tests/test_run_mlxp_probe_bash.py::RunMlxpProbeBashTest::test_rejects_nonpositive_timeout "
            "--timeout-seconds 120; "
            "if ($LASTEXITCODE -ne 0 -or $env:TEMP -ne $before) { exit 21 }; "
            f"& '{RUN_TESTS}' --pytest -q "
            "tests/test_run_mlxp_probe_bash.py::RunMlxpProbeBashTest::test_rejects_nonpositive_timeout "
            "--timeout-seconds 120; "
            "if ($LASTEXITCODE -ne 0 -or $env:TEMP -ne $before) { exit 22 }"
        )

        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(os.environ.get("TEMP", ""), original_temp)

    def test_default_scratch_is_not_under_repository_outputs(self) -> None:
        script = RUN_TESTS.read_text(encoding="utf-8")

        self.assertIn("[System.IO.Path]::GetTempPath()", script)
        self.assertNotIn('Join-Path $Root "outputs\\.test_tmp', script)


if __name__ == "__main__":
    unittest.main()
