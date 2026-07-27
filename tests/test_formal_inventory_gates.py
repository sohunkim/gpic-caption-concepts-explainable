from pathlib import Path
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import uuid

from gpic_concepts_v1.pipeline_state import artifact_state_path, write_pipeline_state


ROOT = Path(__file__).resolve().parents[1]


class FormalInventoryGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.stage4_runner = _load_script("run_stage4_extract_raw.py")
        cls.stage5_runner = _load_script("run_stage5_canonicalize.py")

    def test_stage4_runner_blocks_pending_object_inventory(self) -> None:
        tmp_path = _temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            inventory = tmp_path / "object_inventory.tsv"
            _write_inventory(
                inventory,
                [
                    {
                        "span_key": "scene",
                        "observed_surface": "scene",
                        "decision_status": "needs_manual",
                        "selected_oewn_synset": "fake-scene-n",
                        "canonical_surface": "scene",
                    }
                ],
            )

            with self.assertRaises(SystemExit) as caught:
                self.stage4_runner._raise_if_object_inventory_not_ready(inventory)
        finally:
            _remove_tree(tmp_path)

        self.assertIn("blocked_object_inventory_before_stage4", str(caught.exception))

    def test_stage4_runner_blocks_object_canonical_missing(self) -> None:
        tmp_path = _temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            inventory = tmp_path / "object_inventory.tsv"
            _write_inventory(
                inventory,
                [
                    {
                        "span_key": "sun",
                        "observed_surface": "sun",
                        "decision_status": "chosen",
                        "selected_oewn_synset": "fake-sun-n",
                        "canonical_surface": "",
                    }
                ],
            )

            with self.assertRaises(SystemExit) as caught:
                self.stage4_runner._raise_if_object_inventory_not_ready(inventory)
        finally:
            _remove_tree(tmp_path)

        self.assertIn("selected_synset_missing_canonical_surface", str(caught.exception))

    def test_stage4_runner_blocks_pending_action_inventory(self) -> None:
        tmp_path = _temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            inventory = tmp_path / "action_inventory.tsv"
            _write_inventory(
                inventory,
                [
                    {
                        "span_key": "shining",
                        "observed_surface": "shining",
                        "decision_status": "needs_manual",
                        "decision_reason": "manual_action_morphy_required",
                        "selected_query": "shin|shine",
                        "selected_oewn_synset": "",
                    }
                ],
            )
            _write_action_state(inventory)

            with self.assertRaises(SystemExit) as caught:
                self.stage4_runner._raise_if_action_inventory_not_ready(inventory)
        finally:
            _remove_tree(tmp_path)

        self.assertIn("blocked_action_inventory_before_stage4", str(caught.exception))

    def test_stage4_runner_blocks_action_inventory_missing_pipeline_state(self) -> None:
        tmp_path = _temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            inventory = tmp_path / "action_inventory.tsv"
            _write_inventory(
                inventory,
                [
                    {
                        "span_key": "walk",
                        "observed_surface": "walk",
                        "decision_status": "raw_fallback",
                        "decision_reason": "no_oewn_verb_synset",
                        "selected_query": "walk",
                        "selected_oewn_synset": "",
                    }
                ],
            )

            with self.assertRaises(SystemExit) as caught:
                self.stage4_runner._raise_if_action_inventory_not_ready(inventory)
        finally:
            _remove_tree(tmp_path)

        self.assertIn(
            "blocked_action_inventory_pipeline_state_before_stage4",
            str(caught.exception),
        )

    def test_stage4_runner_allows_action_raw_fallback_inventory(self) -> None:
        tmp_path = _temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            inventory = tmp_path / "action_inventory.tsv"
            _write_inventory(
                inventory,
                [
                    {
                        "span_key": "high-fives",
                        "observed_surface": "high-fives",
                        "decision_status": "raw_fallback",
                        "decision_reason": "no_oewn_verb_synset",
                        "selected_query": "high-fives",
                        "selected_oewn_synset": "",
                    }
                ],
            )
            _write_action_state(inventory)

            self.stage4_runner._raise_if_action_inventory_not_ready(inventory)
        finally:
            _remove_tree(tmp_path)

    def test_stage4_main_requires_action_inventory_for_formal_run(self) -> None:
        tmp_path = _temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        original_argv = sys.argv[:]
        try:
            sys.argv = [
                "run_stage4_extract_raw.py",
                "--input",
                str(tmp_path / "stage3.jsonl"),
                "--object-inventory",
                str(tmp_path / "object_inventory.tsv"),
                "--raw-mentions",
                str(tmp_path / "raw_mentions.jsonl"),
                "--raw-edges",
                str(tmp_path / "raw_edges.jsonl"),
            ]

            with self.assertRaises(SystemExit) as caught:
                self.stage4_runner.main()
        finally:
            sys.argv = original_argv
            _remove_tree(tmp_path)

        self.assertIn("--action-inventory is required", str(caught.exception))

    def test_stage4_main_requires_attribute_inventory_for_formal_run(self) -> None:
        tmp_path = _temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        original_argv = sys.argv[:]
        try:
            sys.argv = [
                "run_stage4_extract_raw.py",
                "--input",
                str(tmp_path / "stage3.jsonl"),
                "--allow-runtime-oewn-lookup",
                "--allow-runtime-action-lookup-preview",
                "--raw-mentions",
                str(tmp_path / "raw_mentions.jsonl"),
                "--raw-edges",
                str(tmp_path / "raw_edges.jsonl"),
            ]

            with self.assertRaises(SystemExit) as caught:
                self.stage4_runner.main()
        finally:
            sys.argv = original_argv
            _remove_tree(tmp_path)

        self.assertIn("--attribute-inventory is required", str(caught.exception))

    def test_stage5_runner_blocks_pending_attribute_inventory(self) -> None:
        tmp_path = _temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            inventory = tmp_path / "attribute_inventory.tsv"
            _write_inventory(
                inventory,
                [
                    {
                        "span_key": "label",
                        "observed_surface": "label",
                        "decision_status": "needs_manual",
                        "selected_oewn_synset": "fake-label-n",
                        "canonical_surface": "label",
                    }
                ],
            )

            with self.assertRaises(SystemExit) as caught:
                self.stage5_runner._raise_if_attribute_inventory_not_ready(inventory)
        finally:
            _remove_tree(tmp_path)

        self.assertIn("blocked_attribute_inventory_before_stage5", str(caught.exception))

    def test_stage5_runner_allows_no_synset_attribute_fallback(self) -> None:
        tmp_path = _temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            inventory = tmp_path / "attribute_inventory.tsv"
            _write_inventory(
                inventory,
                [
                    {
                        "span_key": "tyr",
                        "observed_surface": "TYR",
                        "decision_status": "chosen",
                        "decision_reason": "no_oewn_attribute_synset",
                        "selected_oewn_synset": "",
                        "canonical_surface": "",
                    }
                ],
            )

            self.stage5_runner._raise_if_attribute_inventory_not_ready(inventory)
        finally:
            _remove_tree(tmp_path)

    def test_stage5_runner_blocks_attribute_canonical_missing(self) -> None:
        tmp_path = _temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            inventory = tmp_path / "attribute_inventory.tsv"
            _write_inventory(
                inventory,
                [
                    {
                        "span_key": "white",
                        "observed_surface": "white",
                        "decision_status": "chosen",
                        "selected_oewn_synset": "fake-white-a",
                        "canonical_surface": "",
                    }
                ],
            )

            with self.assertRaises(SystemExit) as caught:
                self.stage5_runner._raise_if_attribute_inventory_not_ready(inventory)
        finally:
            _remove_tree(tmp_path)

        self.assertIn("selected_synset_missing_canonical_surface", str(caught.exception))

    def test_stage5_preview_summary_file_is_marked_preview(self) -> None:
        tmp_path = _temp_base() / uuid.uuid4().hex
        tmp_path.mkdir(parents=True, exist_ok=True)
        original_argv = sys.argv[:]
        original_runner = self.stage5_runner.run_stage5_canonicalize
        summary_path = tmp_path / "summary.jsonl"
        try:
            self.stage5_runner.run_stage5_canonicalize = lambda *args, **kwargs: {
                "canonical_mention_total": 0,
                "canonical_edge_total": 0,
            }
            sys.argv = [
                "run_stage5_canonicalize.py",
                "--raw-mentions",
                str(tmp_path / "raw_mentions.jsonl"),
                "--raw-edges",
                str(tmp_path / "raw_edges.jsonl"),
                "--lexicon-dir",
                str(tmp_path),
                "--canonical-mentions",
                str(tmp_path / "canonical_mentions.jsonl"),
                "--canonical-edges",
                str(tmp_path / "canonical_edges.jsonl"),
                "--summary",
                str(summary_path),
                "--allow-unresolved-attribute-preview",
            ]

            with contextlib.redirect_stdout(io.StringIO()):
                self.stage5_runner.main()
            summary = json.loads(summary_path.read_text(encoding="utf-8").splitlines()[0])
        finally:
            self.stage5_runner.run_stage5_canonicalize = original_runner
            sys.argv = original_argv
            _remove_tree(tmp_path)

        self.assertFalse(summary["formal_attribute_inventory_gate"])
        self.assertEqual(
            summary["preview_warning"],
            "unresolved_attribute_inventory_preview",
        )


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_inventory(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "span_key",
        "observed_surface",
        "decision_status",
        "decision_reason",
        "selected_query",
        "selected_oewn_synset",
        "canonical_surface",
    ]
    lines = ["\t".join(fieldnames)]
    for row in rows:
        lines.append("\t".join(row.get(field, "") for field in fieldnames))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_action_state(path: Path) -> None:
    write_pipeline_state(
        artifact_state_path(path),
        {
            "artifact_type": "gpic_observed_action_inventory",
            "stage": "3.5",
            "status": "resolved",
            "preview_mode": False,
            "input": "test",
            "output": str(path),
            "needs_manual_output": "",
            "action_inventory_preposition_mwe_aware": True,
            "preposition_mwe_detection_before_action": True,
            "relation_mwe_match_total": 0,
            "relation_mwe_consumed_token_total": 0,
            "decision_status_counts": {"raw_fallback": 1},
            "needs_manual_rows": 0,
        },
    )


def _temp_base() -> Path:
    roots = [
        os.environ.get("GPIC_TEST_TEMP_ROOT"),
        r"C:\Users\Public\Documents\ESTsoft\CreatorTemp",
        tempfile.gettempdir(),
    ]
    for root in roots:
        if not root:
            continue
        base = Path(root) / "formal_inventory_gates"
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / f"{uuid.uuid4().hex}.tmp"
            probe.write_text("", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return base
        except PermissionError:
            continue
    raise PermissionError("no writable temp directory for formal inventory gate tests")


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            child.rmdir()
    path.rmdir()


if __name__ == "__main__":
    unittest.main()
