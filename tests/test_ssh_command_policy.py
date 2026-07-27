from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ssh_command_policy.py"
SPEC = importlib.util.spec_from_file_location("ssh_command_policy", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
POLICY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = POLICY
SPEC.loader.exec_module(POLICY)


class SshCommandPolicyTest(unittest.TestCase):
    def test_allows_uploaded_script_invocation(self) -> None:
        POLICY.reject_ad_hoc_multistep_ssh(
            [
                "ssh",
                "-p",
                "55031",
                "user@host",
                "bash /tmp/checked_probe.sh",
            ]
        )

    def test_rejects_multistep_remote_shell_command(self) -> None:
        for remote_command in (
            "hostname; stat /tmp/file",
            "hostname && stat /tmp/file",
            "ss -ltnp | grep :8770",
            "first\nsecond",
        ):
            with self.subTest(remote_command=remote_command):
                with self.assertRaisesRegex(
                    SystemExit,
                    "ad-hoc multi-step SSH",
                ):
                    POLICY.reject_ad_hoc_multistep_ssh(
                        ["ssh", "user@host", remote_command]
                    )

    def test_scp_is_not_rejected(self) -> None:
        POLICY.reject_ad_hoc_multistep_ssh(
            ["scp", "local.sh", "user@host:/tmp/probe.sh"]
        )
