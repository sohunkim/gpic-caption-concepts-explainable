from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_stage35_inventory_workflow.py"


def _load_workflow_module():
    spec = importlib.util.spec_from_file_location("run_stage35_inventory_workflow", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


workflow = _load_workflow_module()


class Stage35InventoryWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = ROOT / ".tmp_tests" / self.id().replace(".", "_")
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        self.stage3 = self.tmp / "stage3_records.jsonl"
        self.stage3.write_text("", encoding="utf-8")
        self.object_inventory = self.tmp / "object.tsv"
        _write_tsv(
            self.object_inventory,
            [
                {
                    "span_key": "dog",
                    "observed_surface": "dog",
                    "decision_status": "chosen",
                    "selected_oewn_synset": "oewn-dog-n",
                    "canonical_surface": "dog",
                }
            ],
        )

    def tearDown(self) -> None:
        if self.tmp.exists():
            shutil.rmtree(self.tmp)

    def config(self, **overrides):
        values = {
            "stage3_records": self.stage3,
            "output_dir": self.tmp,
            "object_inventory": self.object_inventory,
        }
        values.update(overrides)
        return workflow.WorkflowConfig(**values)

    def args(self, **overrides):
        values = {
            "stage3_records": str(self.stage3),
            "output_dir": str(self.tmp),
            "use_current_inventory": False,
            "prior_inventory_bundle": None,
            "object_inventory": None,
            "attribute_prior_inventory": None,
            "action_prior_inventory": None,
            "attribute_manual_resolved": None,
            "attribute_manual_decisions": None,
            "action_manual_decisions": None,
            "attribute_inventory": None,
            "attribute_resolved_inventory": None,
            "attribute_canonical_inventory": None,
            "action_inventory": None,
            "action_resolved_inventory": None,
            "action_canonical_inventory": None,
            "preposition_mwe_lexicon": str(workflow.DEFAULT_PREPOSITION_MWE_LEXICON),
            "ngram_evidence": str(workflow.DEFAULT_NGRAM_EVIDENCE),
            "base_lexicon_dir": None,
            "lexicon_output_dir": None,
            "state_output": None,
            "publish_current": False,
            "publish_target_dir": str(workflow.DEFAULT_CURRENT_INVENTORY_DIR),
            "snapshot_label": "",
            "publish_summary": None,
            "subcommand_timeout_seconds": workflow.DEFAULT_SUBCOMMAND_TIMEOUT_SECONDS,
            "allow_explicit_inventory_inputs": False,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_attribute_clear_advances_to_action_inventory_build(self) -> None:
        attribute_canonical = self.tmp / "attribute_canonical.tsv"
        _write_tsv(
            attribute_canonical,
            [
                {
                    "span_key": "black",
                    "observed_surface": "black",
                    "decision_status": "chosen",
                    "selected_oewn_synset": "oewn-black-a",
                    "canonical_surface": "black",
                }
            ],
        )

        decision = workflow.determine_next_step(
            self.config(attribute_canonical_inventory=attribute_canonical)
        )

        self.assertEqual(decision.action, "build_action_inventory")
        self.assertEqual(decision.status, "ready_to_run")
        self.assertEqual(decision.next_required_step, "build_action_inventory")

    def test_action_needs_manual_blocks_next_step(self) -> None:
        attribute_canonical = self.tmp / "attribute_canonical.tsv"
        action_inventory = self.tmp / "action.tsv"
        _write_tsv(
            attribute_canonical,
            [
                {
                    "span_key": "black",
                    "observed_surface": "black",
                    "decision_status": "chosen",
                    "selected_oewn_synset": "oewn-black-a",
                    "canonical_surface": "black",
                }
            ],
        )
        _write_tsv(
            action_inventory,
            [
                {
                    "span_key": "sit in",
                    "observed_surface": "sitting in",
                    "decision_status": "needs_manual",
                    "selected_oewn_synset": "",
                    "canonical_surface": "",
                }
            ],
        )

        decision = workflow.determine_next_step(
            self.config(
                attribute_canonical_inventory=attribute_canonical,
                action_inventory=action_inventory,
            )
        )

        self.assertEqual(decision.status, "blocked_action_needs_manual")
        self.assertEqual(decision.next_required_step, "resolve_action_manual")
        self.assertEqual(decision.details["blocker_count"], 1)

    def test_attribute_canonical_step_uses_attribute_specific_enricher(self) -> None:
        attribute_resolved = self.tmp / "attribute_resolved.tsv"
        _write_tsv(
            attribute_resolved,
            [
                {
                    "span_key": "rough-hewn",
                    "observed_surface": "rough-hewn",
                    "decision_status": "chosen",
                    "selected_oewn_synset": "oewn-rough-hew-v",
                }
            ],
        )
        config = self.config(attribute_resolved_inventory=attribute_resolved)
        decision = workflow.WorkflowDecision(
            action="enrich_attribute_canonical",
            status="ready_to_run",
            next_required_step="enrich_attribute_canonical",
        )

        with patch.object(workflow, "_run_script") as run_script:
            workflow._execute_decision(config, decision)

        command = run_script.call_args.args[0]
        self.assertEqual(Path(command[0]).name, "enrich_gpic_attribute_inventory_canonical.py")

    def test_attribute_inventory_build_writes_progress_artifact(self) -> None:
        config = self.config()
        decision = workflow.WorkflowDecision(
            action="build_attribute_inventory",
            status="ready_to_run",
            next_required_step="build_attribute_inventory",
        )

        with (
            patch.object(workflow, "_run_script") as run_script,
            patch.object(workflow, "_write_needs_manual_subset"),
        ):
            workflow._execute_decision(config, decision)

        command = run_script.call_args.args[0]
        self.assertEqual(Path(command[0]).name, "build_gpic_observed_attribute_inventory.py")
        self.assertIn("--progress-output", command)
        progress_path = Path(command[command.index("--progress-output") + 1])
        self.assertEqual(progress_path.name, "gpic_observed_attribute_inventory_stage35_workflow_progress.json")
        self.assertEqual(progress_path.parent, self.tmp / "inventory")
        self.assertIn("--checkpoint-output", command)
        checkpoint_path = Path(command[command.index("--checkpoint-output") + 1])
        self.assertEqual(
            checkpoint_path.name,
            "gpic_observed_attribute_inventory_stage35_workflow_checkpoint.json",
        )
        self.assertEqual(checkpoint_path.parent, self.tmp / "inventory")
        self.assertIn("--resume-checkpoint", command)

    def test_action_inventory_build_writes_progress_artifact(self) -> None:
        config = self.config()
        decision = workflow.WorkflowDecision(
            action="build_action_inventory",
            status="ready_to_run",
            next_required_step="build_action_inventory",
        )

        with patch.object(workflow, "_run_script") as run_script:
            workflow._execute_decision(config, decision)

        command = run_script.call_args.args[0]
        self.assertEqual(Path(command[0]).name, "build_gpic_observed_action_inventory.py")
        self.assertIn("--progress-output", command)
        progress_path = Path(command[command.index("--progress-output") + 1])
        self.assertEqual(progress_path.name, "gpic_observed_action_inventory_stage35_workflow_progress.json")
        self.assertEqual(progress_path.parent, self.tmp / "inventory")
        self.assertIn("--checkpoint-output", command)
        checkpoint_path = Path(command[command.index("--checkpoint-output") + 1])
        self.assertEqual(
            checkpoint_path.name,
            "gpic_observed_action_inventory_stage35_workflow_checkpoint.json",
        )
        self.assertEqual(checkpoint_path.parent, self.tmp / "inventory")
        self.assertIn("--resume-checkpoint", command)

    def test_explicit_attribute_input_keeps_downstream_outputs_in_workflow_output_dir(self) -> None:
        external = self.tmp / "external"
        external.mkdir()
        attribute_inventory = external / "attribute.tsv"
        action_inventory = external / "action.tsv"

        paths = workflow.workflow_paths(
            self.config(
                attribute_inventory=attribute_inventory,
                action_inventory=action_inventory,
            )
        )

        self.assertEqual(paths.attribute_inventory, attribute_inventory)
        self.assertEqual(paths.action_inventory, action_inventory)
        self.assertEqual(paths.attribute_resolved_inventory.parent, self.tmp / "inventory")
        self.assertEqual(paths.attribute_canonical_inventory.parent, self.tmp / "inventory")
        self.assertEqual(paths.attribute_needs_manual.parent, self.tmp / "inventory")
        self.assertEqual(paths.action_resolved_inventory.parent, self.tmp / "inventory")
        self.assertEqual(paths.action_canonical_inventory.parent, self.tmp / "inventory")
        self.assertEqual(paths.action_needs_manual.parent, self.tmp / "inventory")

    def test_ready_canonical_inventories_export_lexicon_bundle(self) -> None:
        attribute_canonical = self.tmp / "attribute_canonical.tsv"
        action_canonical = self.tmp / "action_canonical.tsv"
        _write_tsv(
            attribute_canonical,
            [
                {
                    "span_key": "black",
                    "observed_surface": "black",
                    "decision_status": "chosen",
                    "selected_oewn_synset": "oewn-black-a",
                    "canonical_surface": "black",
                }
            ],
        )
        _write_tsv(
            action_canonical,
            [
                {
                    "span_key": "sit in",
                    "observed_surface": "sits in",
                    "decision_status": "chosen",
                    "selected_oewn_synset": "oewn-sit-in-v",
                    "canonical_surface": "sit_in",
                }
            ],
        )

        decision = workflow.determine_next_step(
            self.config(
                attribute_canonical_inventory=attribute_canonical,
                action_canonical_inventory=action_canonical,
            )
        )

        self.assertEqual(decision.action, "export_stage5_lexicon_bundle")
        self.assertEqual(decision.status, "ready_to_run")

    def test_attribute_google_ngram_missing_refreshes_before_manual_blocker(self) -> None:
        attribute_canonical = self.tmp / "attribute_canonical.tsv"
        ngram_evidence = self.tmp / "ngram.tsv"
        _write_tsv(
            attribute_canonical,
            [
                {
                    "span_key": "de-icing",
                    "observed_surface": "de-icing",
                    "decision_status": "chosen",
                    "selected_oewn_synset": "oewn-deice-v",
                    "canonical_surface": "",
                    "canonical_selection_tag": (
                        "ambiguous_wn30_all_zero_or_missing_google_ngram_evidence_missing"
                    ),
                    "google_ngram_candidate_surfaces": "deice|de-ice",
                }
            ],
        )
        _write_tsv(ngram_evidence, [])

        decision = workflow.determine_next_step(
            self.config(
                attribute_canonical_inventory=attribute_canonical,
                ngram_evidence=ngram_evidence,
            )
        )

        self.assertEqual(decision.action, "refresh_attribute_google_ngram_evidence")
        self.assertEqual(decision.status, "ready_to_run")
        self.assertEqual(decision.next_required_step, "refresh_google_ngram_evidence")
        self.assertEqual(decision.details["missing_evidence_pair_count"], 2)

    def test_attribute_google_ngram_queried_missing_blocks_without_refresh_loop(self) -> None:
        attribute_canonical = self.tmp / "attribute_canonical.tsv"
        ngram_evidence = self.tmp / "ngram.tsv"
        _write_tsv(
            attribute_canonical,
            [
                {
                    "span_key": "rare-form",
                    "observed_surface": "rare-form",
                    "decision_status": "chosen",
                    "selected_oewn_synset": "oewn-rare-a",
                    "canonical_surface": "",
                    "canonical_selection_tag": (
                        "ambiguous_wn30_all_zero_or_missing_google_ngram_evidence_missing"
                    ),
                    "google_ngram_candidate_surfaces": "rare-form",
                }
            ],
        )
        _write_tsv(
            ngram_evidence,
            [
                {
                    "selected_oewn_synset": "oewn-rare-a",
                    "surface": "rare-form",
                    "surface_key": "rare-form",
                    "status": "missing",
                }
            ],
        )

        decision = workflow.determine_next_step(
            self.config(
                attribute_canonical_inventory=attribute_canonical,
                ngram_evidence=ngram_evidence,
            )
        )

        self.assertEqual(decision.action, "stop")
        self.assertEqual(decision.status, "blocked_attribute_canonical")
        self.assertEqual(decision.next_required_step, "resolve_attribute_canonical")

    def test_refresh_attribute_google_ngram_then_reruns_attribute_canonical(self) -> None:
        attribute_resolved = self.tmp / "attribute_resolved.tsv"
        attribute_canonical = self.tmp / "attribute_canonical.tsv"
        _write_tsv(
            attribute_resolved,
            [
                {
                    "span_key": "de-icing",
                    "observed_surface": "de-icing",
                    "decision_status": "chosen",
                    "selected_oewn_synset": "oewn-deice-v",
                }
            ],
        )
        config = self.config(
            attribute_resolved_inventory=attribute_resolved,
            attribute_canonical_inventory=attribute_canonical,
        )
        decision = workflow.WorkflowDecision(
            action="refresh_attribute_google_ngram_evidence",
            status="ready_to_run",
            next_required_step="refresh_google_ngram_evidence",
        )

        with patch.object(workflow, "_run_script") as run_script:
            workflow._execute_decision(config, decision)

        commands = [call.args[0] for call in run_script.call_args_list]
        self.assertEqual(
            Path(commands[0][0]).name,
            "refresh_google_ngram_evidence_for_canonical_inventory.py",
        )
        self.assertEqual(Path(commands[1][0]).name, "enrich_gpic_attribute_inventory_canonical.py")

    def test_ready_lexicon_bundle_completes_workflow(self) -> None:
        attribute_canonical = self.tmp / "attribute_canonical.tsv"
        action_canonical = self.tmp / "action_canonical.tsv"
        lexicons = self.tmp / "lexicons"
        lexicons.mkdir()
        _write_tsv(
            attribute_canonical,
            [
                {
                    "span_key": "black",
                    "observed_surface": "black",
                    "decision_status": "chosen",
                    "selected_oewn_synset": "oewn-black-a",
                    "canonical_surface": "black",
                }
            ],
        )
        _write_tsv(
            action_canonical,
            [
                {
                    "span_key": "sit in",
                    "observed_surface": "sits in",
                    "decision_status": "chosen",
                    "selected_oewn_synset": "oewn-sit-in-v",
                    "canonical_surface": "sit_in",
                }
            ],
        )
        (lexicons / "pipeline_state.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "artifact_type": "stage5_lexicon_bundle",
                    "preview_mode": False,
                    "action_canonical_exported": True,
                }
            ),
            encoding="utf-8",
        )

        decision = workflow.determine_next_step(
            self.config(
                attribute_canonical_inventory=attribute_canonical,
                action_canonical_inventory=action_canonical,
                lexicon_output_dir=lexicons,
            )
        )

        self.assertEqual(decision.status, "complete")
        self.assertEqual(decision.next_required_step, "formal_stage4_5_6")

    def test_complete_workflow_state_writes_inventory_bundle(self) -> None:
        decision = workflow.WorkflowDecision(
            action="complete",
            status="complete",
            next_required_step="formal_stage4_5_6",
        )

        workflow._write_workflow_state(self.config(), decision, ["export_stage5_lexicon_bundle"])

        bundle_path = self.tmp / "inventory_bundle.json"
        self.assertTrue(bundle_path.exists())
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        self.assertEqual(bundle["artifact_type"], "gpic_inventory_bundle")
        self.assertEqual(bundle["status"], "complete")
        self.assertEqual(bundle["object_inventory"], str(self.object_inventory))
        self.assertTrue(bundle["attribute_inventory"].endswith("_manual_resolved_canonical.tsv"))
        self.assertTrue(bundle["action_inventory"].endswith("gpic_observed_action_inventory_stage35_workflow.tsv"))
        self.assertTrue(bundle["lexicon_dir"].endswith("lexicons_after_stage35_workflow"))

    def test_complete_workflow_bundle_uses_resolved_action_inventory_when_present(self) -> None:
        config = self.config()
        paths = workflow.workflow_paths(config)
        paths.action_resolved_inventory.parent.mkdir(parents=True, exist_ok=True)
        paths.action_resolved_inventory.write_text("span_key\nrun\n", encoding="utf-8")
        decision = workflow.WorkflowDecision(
            action="complete",
            status="complete",
            next_required_step="formal_stage4_5_6",
        )

        workflow._write_workflow_state(config, decision, ["export_stage5_lexicon_bundle"])

        bundle = json.loads((self.tmp / "inventory_bundle.json").read_text(encoding="utf-8"))
        self.assertEqual(bundle["action_inventory"], str(paths.action_resolved_inventory))

    def test_complete_workflow_can_publish_current_bundle(self) -> None:
        target_dir = self.tmp / "current"
        config = self.config(
            publish_current=True,
            publish_target_dir=target_dir,
            snapshot_label="unit-test",
        )
        paths = workflow.workflow_paths(config)
        paths.inventory_dir.mkdir(parents=True)
        _write_tsv(
            paths.attribute_canonical_inventory,
            [{"span_key": "black", "decision_status": "chosen", "canonical_surface": "black"}],
        )
        _write_tsv(
            paths.action_inventory,
            [{"span_key": "run", "decision_status": "chosen", "canonical_surface": "run"}],
        )
        paths.action_inventory.with_name(
            f"{paths.action_inventory.name}.pipeline_state.json"
        ).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "artifact_type": "gpic_observed_action_inventory",
                    "preview_mode": False,
                    "action_inventory_preposition_mwe_aware": True,
                    "preposition_mwe_detection_before_action": True,
                    "output": str(paths.action_inventory),
                }
            ),
            encoding="utf-8",
        )
        _write_tsv(
            paths.action_canonical_inventory,
            [{"span_key": "run", "decision_status": "chosen", "canonical_surface": "run"}],
        )
        paths.lexicon_output_dir.mkdir(parents=True)
        (paths.lexicon_output_dir / "action_synonyms.tsv").write_text(
            "source_label\tcanonical_label\nrun\trun\n",
            encoding="utf-8",
        )
        (paths.lexicon_output_dir / "pipeline_state.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "artifact_type": "stage5_lexicon_bundle",
                    "stage": "5",
                    "status": "ready",
                    "preview_mode": False,
                    "attribute_inventory": str(paths.attribute_canonical_inventory),
                    "action_canonical_inventory": str(paths.action_canonical_inventory),
                    "output_dir": str(paths.lexicon_output_dir),
                    "action_canonical_exported": True,
                }
            ),
            encoding="utf-8",
        )
        decision = workflow.WorkflowDecision(
            action="complete",
            status="complete",
            next_required_step="formal_stage4_5_6",
        )

        workflow._write_workflow_state(config, decision, ["export_stage5_lexicon_bundle"])

        central_bundle = target_dir / "inventory_bundle.json"
        state = json.loads(paths.state_output.read_text(encoding="utf-8"))
        bundle = json.loads(central_bundle.read_text(encoding="utf-8"))
        self.assertEqual(state["published_current_bundle"], str(central_bundle))
        self.assertEqual(state["publish_summary"]["status"], "published")
        self.assertEqual(bundle["snapshot_label"], "unit-test")
        self.assertEqual(bundle["path_base"], "bundle_dir")
        self.assertEqual(bundle["object_inventory"], "inventory/object_inventory.tsv")

    def test_prior_inventory_bundle_populates_workflow_config(self) -> None:
        bundle_path = self.tmp / "inventory_bundle.json"
        object_inventory = self.tmp / "prior_object.tsv"
        attribute_inventory = self.tmp / "prior_attribute.tsv"
        action_inventory = self.tmp / "prior_action.tsv"
        lexicon_dir = self.tmp / "prior_lexicons"
        bundle_path.write_text(
            json.dumps(
                {
                    "artifact_type": "gpic_inventory_bundle",
                    "status": "complete",
                    "object_inventory": str(object_inventory),
                    "attribute_inventory": str(attribute_inventory),
                    "action_inventory": str(action_inventory),
                    "lexicon_dir": str(lexicon_dir),
                }
            ),
            encoding="utf-8",
        )

        config = workflow.config_from_args(self.args(prior_inventory_bundle=str(bundle_path)))

        self.assertEqual(config.object_inventory, object_inventory)
        self.assertEqual(config.attribute_prior_inventory, attribute_inventory)
        self.assertEqual(config.action_prior_inventory, action_inventory)
        self.assertEqual(config.base_lexicon_dir, lexicon_dir)

    def test_use_current_inventory_loads_default_current_bundle(self) -> None:
        current_bundle = self.tmp / "current" / "inventory_bundle.json"
        object_inventory = self.tmp / "current_object.tsv"
        attribute_inventory = self.tmp / "current_attribute.tsv"
        action_inventory = self.tmp / "current_action.tsv"
        lexicon_dir = self.tmp / "current_lexicons"
        current_bundle.parent.mkdir(parents=True)
        current_bundle.write_text(
            json.dumps(
                {
                    "artifact_type": "gpic_inventory_bundle",
                    "status": "complete",
                    "object_inventory": str(object_inventory),
                    "attribute_inventory": str(attribute_inventory),
                    "action_inventory": str(action_inventory),
                    "lexicon_dir": str(lexicon_dir),
                }
            ),
            encoding="utf-8",
        )

        with patch.object(workflow, "DEFAULT_CURRENT_INVENTORY_BUNDLE", current_bundle):
            config = workflow.config_from_args(self.args(use_current_inventory=True))

        self.assertEqual(config.inventory_input_mode, "current")
        self.assertEqual(config.source_inventory_bundle, current_bundle)
        self.assertEqual(config.object_inventory, object_inventory)
        self.assertEqual(config.attribute_prior_inventory, attribute_inventory)
        self.assertEqual(config.action_prior_inventory, action_inventory)
        self.assertEqual(config.base_lexicon_dir, lexicon_dir)

    def test_use_current_inventory_rejects_explicit_inventory_paths(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            workflow.config_from_args(
                self.args(
                    use_current_inventory=True,
                    object_inventory=str(self.object_inventory),
                )
            )

    def test_publish_current_rejects_explicit_inventory_without_migration_flag(self) -> None:
        with self.assertRaisesRegex(ValueError, "--publish-current requires --use-current-inventory"):
            workflow.config_from_args(
                self.args(
                    publish_current=True,
                    object_inventory=str(self.object_inventory),
                )
            )

    def test_nonzero_canonical_command_is_allowed_when_output_exists(self) -> None:
        script = self.tmp / "write_then_fail.py"
        output = self.tmp / "canonical.tsv"
        script.write_text(
            "from pathlib import Path\n"
            "import sys\n"
            "Path(sys.argv[1]).write_text('partial output', encoding='utf-8')\n"
            "raise SystemExit(2)\n",
            encoding="utf-8",
        )

        workflow._run_script([script, output], nonzero_ok_output=output)

        self.assertEqual(output.read_text(encoding="utf-8"), "partial output")

    def test_zero_subcommand_timeout_disables_subprocess_timeout(self) -> None:
        with patch.object(workflow.subprocess, "run") as run:
            run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")

            workflow._run_script([self.tmp / "script.py"], timeout_seconds=0)

        self.assertIsNone(run.call_args.kwargs["timeout"])

    def test_negative_subcommand_timeout_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be >= 0"):
            workflow.config_from_args(self.args(subcommand_timeout_seconds=-1))


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
