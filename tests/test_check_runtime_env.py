from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_runtime_env.py"
SPEC = importlib.util.spec_from_file_location("check_runtime_env", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

_exit_code_from_report = module._exit_code_from_report


class CheckRuntimeEnvTests(unittest.TestCase):
    def test_require_spacy_gpu_failure_exits_nonzero(self) -> None:
        args = argparse.Namespace(require_spacy_gpu=True, spacy_model=None)
        report = {"spacy": {"installed": False, "import_error": "boom"}}

        self.assertEqual(_exit_code_from_report(report, args), 1)

    def test_requested_model_load_failure_exits_nonzero(self) -> None:
        args = argparse.Namespace(require_spacy_gpu=False, spacy_model="en_core_web_trf")
        report = {"spacy": {"installed": True, "model": {"loaded": False}}}

        self.assertEqual(_exit_code_from_report(report, args), 1)

    def test_requested_gpu_and_model_success_exits_zero(self) -> None:
        args = argparse.Namespace(require_spacy_gpu=True, spacy_model="en_core_web_trf")
        report = {
            "spacy": {
                "installed": True,
                "require_gpu": True,
                "model": {"loaded": True},
            }
        }

        self.assertEqual(_exit_code_from_report(report, args), 0)


if __name__ == "__main__":
    unittest.main()
