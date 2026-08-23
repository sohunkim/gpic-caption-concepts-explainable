from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_mlxp_probe_bash.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("run_mlxp_probe_bash", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load_module()


class RunMlxpProbeBashTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = ROOT / ".tmp_tests" / self.id().replace(".", "_")
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)

    def tearDown(self) -> None:
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def test_rejects_non_probe_script_name(self) -> None:
        script = self.tmp / "run_job.sh"
        script.write_text("echo no\n", encoding="utf-8")

        with self.assertRaisesRegex(SystemExit, "only accepts probe_"):
            probe.main([str(script)])

    def test_rejects_nonpositive_timeout(self) -> None:
        script = self.tmp / "probe_status.sh"
        script.write_text("echo ok\n", encoding="utf-8")

        with self.assertRaisesRegex(SystemExit, "timeout-seconds"):
            probe.main([str(script), "--timeout-seconds", "0"])

    def test_runs_probe_script_through_kubectl_without_incident_gate(self) -> None:
        script = self.tmp / "probe_status.sh"
        script.write_bytes(b"echo ok\r\n")
        completed = Mock(returncode=0)

        with (
            patch.object(probe, "_preflight_pod_running", return_value=0) as preflight,
            patch.object(probe.subprocess, "run", return_value=completed) as run,
        ):
            result = probe.main(
                [
                    str(script),
                    "--pod",
                    "pod-a",
                    "--namespace",
                    "ns-a",
                    "--kubectl",
                    "/bin/kubectl",
                    "--timeout-seconds",
                    "7",
                ],
            )

        self.assertEqual(result, 0)
        preflight.assert_called_once_with(
            kubectl="/bin/kubectl",
            namespace="ns-a",
            pod="pod-a",
        )
        command = run.call_args.args[0]
        self.assertEqual(
            command,
            [
                "wsl",
                "-e",
                "/bin/kubectl",
                "-n",
                "ns-a",
                "exec",
                "-i",
                "pod-a",
                "--",
                "bash",
                "-s",
            ],
        )
        payload = run.call_args.kwargs["input"]
        self.assertIn(b"GPIC MLXP runtime guard", payload)
        self.assertTrue(payload.endswith(b"echo ok\n"))
        self.assertEqual(run.call_args.kwargs["timeout"], 7)

    def test_timeout_returns_124(self) -> None:
        script = self.tmp / "probe_status.sh"
        script.write_text("sleep 10\n", encoding="utf-8")

        with (
            patch.object(probe, "_preflight_pod_running", return_value=0),
            patch.object(
                probe.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["wsl"], timeout=1),
            ),
        ):
            result = probe.main(
                [str(script), "--pod", "pod-a", "--timeout-seconds", "1"]
            )

        self.assertEqual(result, 124)


if __name__ == "__main__":
    unittest.main()
