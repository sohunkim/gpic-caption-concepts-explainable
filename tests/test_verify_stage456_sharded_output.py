from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "verify_stage456_sharded_output.py"
SPEC = importlib.util.spec_from_file_location(
    "verify_stage456_sharded_output",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class VerifyStage456ShardedOutputTest(unittest.TestCase):
    def test_accepts_stage6_merged_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            merged = output / "stage6_merged"
            merged.mkdir()
            table = merged / "object_counts.tsv"
            table.write_text("count_key\tcount\nobject\t1\n", encoding="utf-8")
            (output / "summary.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "stage6_merged": {
                            "table_paths": {"object_counts.tsv": str(table)},
                            "table_row_counts": {"object_counts.tsv": 1},
                        },
                        "timing_seconds": {"total": 1.0},
                    }
                ),
                encoding="utf-8",
            )

            result = module.verify_stage456_sharded_output(
                output,
                expected_table_count=1,
            )

            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["stage6_dir"], str(merged))

    def test_rejects_monolithic_stage6_directory_assumption(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            (output / "stage6").mkdir()
            (output / "summary.json").write_text(
                json.dumps({"status": "completed", "stage6_merged": {}}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "stage6_merged"):
                module.verify_stage456_sharded_output(
                    output,
                    expected_table_count=1,
                )

    def test_report_contract_requires_triple_helper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            merged = output / "stage6_merged"
            merged.mkdir()
            table = merged / "object_counts.tsv"
            table.write_text("count_key\tcount\nobject\t1\n", encoding="utf-8")
            (output / "summary.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "stage6_merged": {
                            "table_paths": {"object_counts.tsv": str(table)},
                            "table_row_counts": {"object_counts.tsv": 1},
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "missing report helper"):
                module.verify_stage456_sharded_output(
                    output,
                    expected_table_count=1,
                    require_report_helper=True,
                )

            helper = merged / "patient_action_agent_triple_counts.tsv"
            helper.write_text(
                "count_key\tcount\npatient:a|action:b|agent:c\t1\n",
                encoding="utf-8",
            )
            summary = module.verify_stage456_sharded_output(
                output,
                expected_table_count=1,
                require_report_helper=True,
            )
            self.assertEqual(summary["report_helper"], str(helper))


if __name__ == "__main__":
    unittest.main()
