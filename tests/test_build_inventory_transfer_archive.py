from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "build_inventory_transfer_archive.py"
SPEC = importlib.util.spec_from_file_location(
    "build_inventory_transfer_archive",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class BuildInventoryTransferArchiveTest(unittest.TestCase):
    def test_recursively_includes_action_pipeline_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            source = repo / "resources" / "gpic_inventory" / "current"
            for relative in module.REQUIRED_RELATIVE_FILES:
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")
            extra = source / "lexicons" / "pipeline_state.json"
            extra.write_text("{}\n", encoding="utf-8")
            output = repo / "bundle.zip"
            manifest = repo / "bundle.json"

            summary = module.build_inventory_transfer_archive(
                source_dir=source,
                output=output,
                manifest=manifest,
                repo_root=repo,
            )

            with zipfile.ZipFile(output, "r") as archive:
                names = set(archive.namelist())
            self.assertIn(
                "resources/gpic_inventory/current/inventory/"
                "action_inventory.tsv.pipeline_state.json",
                names,
            )
            self.assertIn(
                "resources/gpic_inventory/current/lexicons/pipeline_state.json",
                names,
            )
            self.assertEqual(json.loads(manifest.read_text())["file_count"], len(names))
            self.assertEqual(summary["file_count"], len(names))

    def test_rejects_missing_action_pipeline_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            source = repo / "resources" / "gpic_inventory" / "current"
            for relative in module.REQUIRED_RELATIVE_FILES:
                if relative.name.endswith(".pipeline_state.json"):
                    continue
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing required files"):
                module.build_inventory_transfer_archive(
                    source_dir=source,
                    output=repo / "bundle.zip",
                    manifest=repo / "bundle.json",
                    repo_root=repo,
                )


if __name__ == "__main__":
    unittest.main()
