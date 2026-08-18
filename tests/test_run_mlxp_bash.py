from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_mlxp_bash.py"
PROBE_SCRIPT_PATH = ROOT / "scripts" / "run_mlxp_probe_bash.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("run_mlxp_bash", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load run_mlxp_bash.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_probe_module():
    spec = importlib.util.spec_from_file_location("run_mlxp_probe_bash", PROBE_SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load run_mlxp_probe_bash.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RunMlxpBashTests(unittest.TestCase):
    def test_main_requires_explicit_pod_or_env(self) -> None:
        module = _load_module()

        with patch.dict(os.environ, {module.DEFAULT_POD_ENV: ""}, clear=False):
            with self.assertRaisesRegex(SystemExit, "--pod is required"):
                module.main(["missing.sh"])

    def test_main_uses_mlxp_pod_environment_fallback(self) -> None:
        module = _load_module()

        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "probe.sh"
            script.write_text("echo ok\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {module.DEFAULT_POD_ENV: "pod-from-env"}, clear=False),
                patch.object(module.subprocess, "run") as run_mock,
            ):
                preflight = unittest.mock.Mock()
                preflight.returncode = 0
                preflight.stdout = b"WSL_OK"
                preflight.stderr = b""
                pod_preflight = unittest.mock.Mock()
                pod_preflight.returncode = 0
                pod_preflight.stdout = json.dumps(
                    {"status": {"phase": "Running"}},
                ).encode("utf-8")
                pod_preflight.stderr = b""
                remote = unittest.mock.Mock()
                remote.returncode = 0
                run_mock.side_effect = [preflight, pod_preflight, remote]

                self.assertEqual(module.main([str(script)]), 0)

        command = run_mock.call_args_list[-1].args[0]
        self.assertIn("pod-from-env", command)
        self.assertEqual(run_mock.call_args_list[0].args[0][:2], ["wsl", "-e"])
        self.assertIn("get", run_mock.call_args_list[1].args[0])

    def test_main_resolves_single_running_pod_by_prefix(self) -> None:
        module = _load_module()

        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "probe.sh"
            script.write_text("echo ok\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {module.DEFAULT_POD_ENV: ""}, clear=False),
                patch.object(module.subprocess, "run") as run_mock,
            ):
                resolver = unittest.mock.Mock()
                resolver.returncode = 0
                resolver.stdout = json.dumps(
                    {
                        "items": [
                            {
                                "metadata": {"name": "prod-rsv-snu14ksh-old"},
                                "status": {"phase": "Succeeded"},
                            },
                            {
                                "metadata": {"name": "prod-rsv-snu14ksh-current"},
                                "status": {"phase": "Running"},
                            },
                        ],
                    },
                ).encode("utf-8")
                resolver.stderr = b""
                preflight = unittest.mock.Mock()
                preflight.returncode = 0
                preflight.stdout = b"WSL_OK"
                preflight.stderr = b""
                pod_preflight = unittest.mock.Mock()
                pod_preflight.returncode = 0
                pod_preflight.stdout = json.dumps(
                    {"status": {"phase": "Running"}},
                ).encode("utf-8")
                pod_preflight.stderr = b""
                remote = unittest.mock.Mock()
                remote.returncode = 0
                run_mock.side_effect = [resolver, preflight, pod_preflight, remote]

                self.assertEqual(
                    module.main([str(script), "--pod-prefix", "prod-rsv-snu14ksh-"]),
                    0,
                )

        command = run_mock.call_args_list[-1].args[0]
        self.assertIn("prod-rsv-snu14ksh-current", command)

    def test_main_stops_before_remote_command_when_wsl_preflight_fails(self) -> None:
        module = _load_module()

        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "probe.sh"
            script.write_text("echo ok\n", encoding="utf-8")
            with patch.object(module.subprocess, "run") as run_mock:
                preflight = unittest.mock.Mock()
                preflight.returncode = 0xFFFFFFFF
                preflight.stdout = b""
                preflight.stderr = "액세스가 거부되었습니다".encode("utf-16-le")
                run_mock.return_value = preflight

                self.assertEqual(module.main([str(script), "--pod", "pod1"]), 0xFFFFFFFF)

        self.assertEqual(run_mock.call_count, 1)

    def test_decode_process_output_handles_utf16_wsl_errors(self) -> None:
        module = _load_module()

        payload = "액세스가 거부되었습니다".encode("utf-16-le")

        self.assertIn("거부", module._decode_process_output(payload))

    def test_strip_utf_bom(self) -> None:
        module = _load_module()

        self.assertEqual(module._strip_utf_bom(b"\xef\xbb\xbfset -eu\n"), b"set -eu\n")

    def test_normalize_bash_newlines(self) -> None:
        module = _load_module()

        payload = b"echo one\r\necho two\rsort\r\n"

        self.assertEqual(
            module._normalize_bash_newlines(payload),
            b"echo one\necho two\nsort\n",
        )

    def test_runtime_env_guard_is_prepended(self) -> None:
        module = _load_module()

        payload = module._prepend_mlxp_runtime_env(b"set -eu\necho ok\n")

        self.assertIn(b"LD_LIBRARY_PATH", payload)
        self.assertIn(b"/root/work/gpic-linux-env/bin/python", payload)
        self.assertTrue(payload.endswith(b"set -eu\necho ok\n"))

    def test_probe_runner_prepends_runtime_env_by_default(self) -> None:
        module = _load_probe_module()

        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "probe_runtime.sh"
            script.write_text("echo ok\n", encoding="utf-8")
            with patch.object(module.subprocess, "run") as run_mock:
                pod_preflight = unittest.mock.Mock()
                pod_preflight.returncode = 0
                pod_preflight.stdout = json.dumps(
                    {"status": {"phase": "Running"}},
                ).encode("utf-8")
                pod_preflight.stderr = b""
                remote = unittest.mock.Mock()
                remote.returncode = 0
                run_mock.side_effect = [pod_preflight, remote]

                self.assertEqual(
                    module.main([str(script), "--pod", "pod1", "--timeout-seconds", "5"]),
                    0,
                )

        payload = run_mock.call_args_list[-1].kwargs["input"]
        self.assertIn(b"LD_LIBRARY_PATH", payload)
        self.assertTrue(payload.endswith(b"echo ok\n"))


if __name__ == "__main__":
    unittest.main()
