from pathlib import Path
import importlib.util
import json
import os
import tempfile
import unittest
import uuid
from types import SimpleNamespace

from gpic_concepts_v1.io_jsonl import iter_jsonl, write_jsonl
from gpic_concepts_v1.pipeline_state import artifact_state_path, write_pipeline_state
from gpic_concepts_v1.runtime_resources import HardwareResources


def load_script_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_mixed_caption_pipeline.py"
    spec = importlib.util.spec_from_file_location("run_mixed_caption_pipeline", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load run_mixed_caption_pipeline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MixedCaptionPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_script_module()
        self.tmp_path = _temp_base() / uuid.uuid4().hex
        self.tmp_path.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        for path in sorted(self.tmp_path.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                path.rmdir()
        self.tmp_path.rmdir()

    def test_combines_rows_and_stage3_records_in_caption_order(self) -> None:
        caption_records = [
            {"caption_id": "s1", "caption_shape": "sentence", "skipped": False},
            {"caption_id": "t1", "caption_shape": "tag_list", "skipped": False},
            {"caption_id": "s2", "caption_shape": "sentence", "skipped": False},
            {"caption_id": "t2", "caption_shape": "tag_list", "skipped": False},
        ]
        sentence_rows = [
            {"key": "s1", "caption": "A dog runs.", "caption_type": "short"},
            {"key": "s2", "caption": "A cat sits.", "caption_type": "short"},
        ]
        tag_rows = [
            {"key": "t1", "caption": "red shirt, blue wall", "caption_type": "tag"},
            {"key": "t2", "caption": "green grass, white lines", "caption_type": "tag"},
        ]
        sentence_stage3 = [
            {"caption_id": "s1", "tokens": [], "noun_chunks": [], "meta": {"caption_shape": "sentence"}},
            {"caption_id": "s2", "tokens": [], "noun_chunks": [], "meta": {"caption_shape": "sentence"}},
        ]
        tag_stage3 = [
            {"caption_id": "t1", "tokens": [], "noun_chunks": [], "meta": {"caption_shape": "tag_list"}},
            {"caption_id": "t2", "tokens": [], "noun_chunks": [], "meta": {"caption_shape": "tag_list"}},
        ]
        caption_records_path = self.tmp_path / "caption_records.jsonl"
        sentence_rows_path = self.tmp_path / "sentence_rows.jsonl"
        tag_rows_path = self.tmp_path / "tag_rows.jsonl"
        sentence_stage3_path = self.tmp_path / "sentence_stage3.jsonl"
        tag_stage3_path = self.tmp_path / "tag_stage3.jsonl"
        mixed_rows_path = self.tmp_path / "caption_rows_mixed.jsonl"
        mixed_stage3_path = self.tmp_path / "stage3_records.jsonl"
        write_jsonl(caption_records_path, caption_records)
        write_jsonl(sentence_rows_path, sentence_rows)
        write_jsonl(tag_rows_path, tag_rows)
        write_jsonl(sentence_stage3_path, sentence_stage3)
        write_jsonl(tag_stage3_path, tag_stage3)

        row_summary = self.module.combine_caption_rows_in_caption_order(
            caption_records_path=caption_records_path,
            sentence_rows_path=sentence_rows_path,
            tag_rows_path=tag_rows_path,
            output_path=mixed_rows_path,
        )
        stage3_summary = self.module.combine_stage3_records_in_caption_order(
            caption_records_path=caption_records_path,
            sentence_stage3_path=sentence_stage3_path,
            tag_stage3_path=tag_stage3_path,
            output_path=mixed_stage3_path,
        )

        self.assertEqual(row_summary["caption_shape_counts"], {"sentence": 2, "tag_list": 2})
        self.assertEqual(stage3_summary["caption_shape_counts"], {"sentence": 2, "tag_list": 2})
        self.assertEqual([row["key"] for row in iter_jsonl(mixed_rows_path)], ["s1", "t1", "s2", "t2"])
        self.assertEqual(
            [row["caption_id"] for row in iter_jsonl(mixed_stage3_path)],
            ["s1", "t1", "s2", "t2"],
        )

    def test_combine_stage3_rejects_order_mismatch(self) -> None:
        caption_records_path = self.tmp_path / "caption_records.jsonl"
        sentence_stage3_path = self.tmp_path / "sentence_stage3.jsonl"
        tag_stage3_path = self.tmp_path / "tag_stage3.jsonl"
        write_jsonl(
            caption_records_path,
            [{"caption_id": "s1", "caption_shape": "sentence", "skipped": False}],
        )
        write_jsonl(sentence_stage3_path, [{"caption_id": "other", "meta": {"caption_shape": "sentence"}}])
        write_jsonl(tag_stage3_path, [])

        with self.assertRaisesRegex(ValueError, "order mismatch"):
            self.module.combine_stage3_records_in_caption_order(
                caption_records_path=caption_records_path,
                sentence_stage3_path=sentence_stage3_path,
                tag_stage3_path=tag_stage3_path,
                output_path=self.tmp_path / "out.jsonl",
            )

    def test_combines_lite_id_rows_in_caption_order(self) -> None:
        caption_records_path = self.tmp_path / "caption_records.jsonl"
        sentence_rows_path = self.tmp_path / "sentence_rows.jsonl"
        tag_rows_path = self.tmp_path / "tag_rows.jsonl"
        output_path = self.tmp_path / "caption_rows_mixed.jsonl"
        write_jsonl(
            caption_records_path,
            [{"caption_id": "lite-sha", "caption_shape": "sentence", "skipped": False}],
        )
        write_jsonl(
            sentence_rows_path,
            [
                {
                    "id": "lite-sha",
                    "caption": "A dog runs.",
                    "caption_type": "short",
                    "global_index": 0,
                }
            ],
        )
        write_jsonl(tag_rows_path, [])

        summary = self.module.combine_caption_rows_in_caption_order(
            caption_records_path=caption_records_path,
            sentence_rows_path=sentence_rows_path,
            tag_rows_path=tag_rows_path,
            output_path=output_path,
        )

        self.assertEqual(summary["written"], 1)
        self.assertEqual(list(iter_jsonl(output_path))[0]["id"], "lite-sha")

    def test_combine_stage3_preserves_raw_stage3_lines(self) -> None:
        caption_records_path = self.tmp_path / "caption_records.jsonl"
        sentence_stage3_path = self.tmp_path / "sentence_stage3.jsonl"
        tag_stage3_path = self.tmp_path / "tag_stage3.jsonl"
        output_path = self.tmp_path / "stage3_records.jsonl"
        write_jsonl(
            caption_records_path,
            [
                {"caption_id": "s1", "caption_shape": "sentence", "skipped": False},
                {"caption_id": "t1", "caption_shape": "tag_list", "skipped": False},
            ],
            sort_keys=False,
        )
        sentence_stage3_path.write_text(
            '{"tokens":[],"caption_id":"s1","meta":{"caption_shape":"sentence"}}\n',
            encoding="utf-8",
        )
        tag_stage3_path.write_text(
            '  {"meta":{"caption_shape":"tag_list"},"caption_id":"t1","tokens":[]}\n',
            encoding="utf-8",
        )

        self.module.combine_stage3_records_in_caption_order(
            caption_records_path=caption_records_path,
            sentence_stage3_path=sentence_stage3_path,
            tag_stage3_path=tag_stage3_path,
            output_path=output_path,
        )

        self.assertEqual(
            output_path.read_text(encoding="utf-8"),
            '{"tokens":[],"caption_id":"s1","meta":{"caption_shape":"sentence"}}\n'
            '  {"meta":{"caption_shape":"tag_list"},"caption_id":"t1","tokens":[]}\n',
        )

    def test_formal_pipeline_requires_action_inventory_before_running(self) -> None:
        with self.assertRaisesRegex(ValueError, "action_inventory is required"):
            self.module.run_mixed_caption_pipeline(
                input_paths=[self.tmp_path / "missing.jsonl"],
                output_dir=self.tmp_path / "out",
                object_inventory=self.tmp_path / "object.tsv",
                attribute_inventory=self.tmp_path / "attribute.tsv",
                action_inventory=None,
            )

    def test_formal_pipeline_rejects_invalid_stage6_count_backend(self) -> None:
        with self.assertRaisesRegex(ValueError, "stage6_count_backend"):
            self.module.run_mixed_caption_pipeline(
                input_paths=[self.tmp_path / "missing.jsonl"],
                output_dir=self.tmp_path / "out",
                object_inventory=self.tmp_path / "object.tsv",
                attribute_inventory=self.tmp_path / "attribute.tsv",
                action_inventory=None,
                stage6_count_backend="bogus",
            )

    def test_formal_pipeline_rejects_invalid_stage6_facts_output_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "stage6_facts_output_mode"):
            self.module.run_mixed_caption_pipeline(
                input_paths=[self.tmp_path / "missing.jsonl"],
                output_dir=self.tmp_path / "out",
                object_inventory=self.tmp_path / "object.tsv",
                attribute_inventory=self.tmp_path / "attribute.tsv",
                action_inventory=None,
                stage6_facts_output_mode="bogus",
            )

    def test_formal_pipeline_rejects_md_report_when_stage6_facts_are_discarded(self) -> None:
        with self.assertRaisesRegex(ValueError, "md_report requires"):
            self.module.run_mixed_caption_pipeline(
                input_paths=[self.tmp_path / "missing.jsonl"],
                output_dir=self.tmp_path / "out",
                object_inventory=self.tmp_path / "object.tsv",
                attribute_inventory=self.tmp_path / "attribute.tsv",
                action_inventory=None,
                md_report=self.tmp_path / "report.md",
                stage6_facts_output_mode="discard",
            )

    def test_formal_pipeline_requires_stage5_lexicon_bundle_state_before_running(self) -> None:
        object_inventory = self.tmp_path / "object.tsv"
        action_inventory = self.tmp_path / "action.tsv"
        lexicon_dir = self.tmp_path / "lexicons"
        _write_inventory(object_inventory, [])
        _write_inventory(
            action_inventory,
            [
                {
                    "span_key": "walk",
                    "observed_surface": "walk",
                    "decision_status": "raw_fallback",
                    "decision_reason": "no_oewn_verb_synset",
                    "selected_oewn_synset": "",
                }
            ],
        )
        _write_action_state(action_inventory)
        lexicon_dir.mkdir()

        with self.assertRaisesRegex(ValueError, "lexicon_dir is not ready"):
            self.module.run_mixed_caption_pipeline(
                input_paths=[self.tmp_path / "missing.jsonl"],
                output_dir=self.tmp_path / "out",
                object_inventory=object_inventory,
                attribute_inventory=self.tmp_path / "attribute.tsv",
                action_inventory=action_inventory,
                lexicon_dir=lexicon_dir,
            )

    def test_inventory_bundle_supplies_formal_inventory_inputs(self) -> None:
        bundle = self.tmp_path / "inventory_bundle.json"
        object_inventory = self.tmp_path / "object.tsv"
        attribute_inventory = self.tmp_path / "attribute.tsv"
        action_inventory = self.tmp_path / "action.tsv"
        lexicon_dir = self.tmp_path / "lexicons"
        bundle.write_text(
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

        inputs = self.module.inventory_inputs_from_args(
            SimpleNamespace(
                inventory_bundle=str(bundle),
                object_inventory=None,
                attribute_inventory=None,
                action_inventory=None,
                lexicon_dir=None,
            )
        )

        self.assertEqual(inputs, (object_inventory, attribute_inventory, action_inventory, lexicon_dir))

    def test_inventory_bundle_resolves_bundle_relative_paths(self) -> None:
        bundle_dir = self.tmp_path / "portable"
        bundle_dir.mkdir()
        bundle = bundle_dir / "inventory_bundle.json"
        bundle.write_text(
            json.dumps(
                {
                    "artifact_type": "gpic_inventory_bundle",
                    "status": "complete",
                    "path_base": "bundle_dir",
                    "object_inventory": "inventory/object.tsv",
                    "attribute_inventory": "inventory/attribute.tsv",
                    "action_inventory": "inventory/action.tsv",
                    "lexicon_dir": "lexicons",
                }
            ),
            encoding="utf-8",
        )

        inputs = self.module.inventory_inputs_from_args(
            SimpleNamespace(
                inventory_bundle=str(bundle),
                object_inventory=None,
                attribute_inventory=None,
                action_inventory=None,
                lexicon_dir=None,
            )
        )

        self.assertEqual(
            inputs,
            (
                bundle_dir / "inventory" / "object.tsv",
                bundle_dir / "inventory" / "attribute.tsv",
                bundle_dir / "inventory" / "action.tsv",
                bundle_dir / "lexicons",
            ),
        )

    def test_inventory_bundle_rejects_mismatched_explicit_path(self) -> None:
        bundle = self.tmp_path / "inventory_bundle.json"
        object_inventory = self.tmp_path / "object.tsv"
        bundle.write_text(
            json.dumps(
                {
                    "artifact_type": "gpic_inventory_bundle",
                    "status": "complete",
                    "object_inventory": str(object_inventory),
                    "attribute_inventory": str(self.tmp_path / "attribute.tsv"),
                    "action_inventory": str(self.tmp_path / "action.tsv"),
                    "lexicon_dir": str(self.tmp_path / "lexicons"),
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "inventory_bundle_path_mismatch"):
            self.module.inventory_inputs_from_args(
                SimpleNamespace(
                    inventory_bundle=str(bundle),
                    object_inventory=str(self.tmp_path / "other_object.tsv"),
                    attribute_inventory=None,
                    action_inventory=None,
                    lexicon_dir=None,
                )
            )

    def test_progress_writer_records_stage3_counts(self) -> None:
        progress_path = self.tmp_path / "progress.json"
        writer = self.module.MixedPipelineProgressWriter(progress_path, interval_records=1)

        writer.maybe_write_stage3(
            phase="stage3_sentence",
            total=1,
            caption_shape="sentence",
            token_total=8,
            noun_chunk_total=2,
            tag_segment_total=0,
        )

        progress = json.loads(progress_path.read_text(encoding="utf-8"))
        self.assertEqual(progress["status"], "running")
        self.assertEqual(progress["phase"], "stage3_sentence")
        self.assertEqual(progress["stage3_records_written"], 1)
        self.assertEqual(progress["token_total"], 8)

    def test_stage3_gpu_devices_parser(self) -> None:
        self.assertEqual(self.module._parse_stage3_gpu_devices("0, 1,,2"), ["0", "1", "2"])
        self.assertEqual(self.module._parse_stage3_gpu_devices(""), [])

    def test_parse_args_tracks_explicit_resource_overrides(self) -> None:
        args = self.module.parse_args(
            [
                "--input",
                "input.jsonl.gz",
                "--output-dir",
                "out",
                "--object-inventory",
                "object.tsv",
                "--attribute-inventory",
                "attribute.tsv",
                "--action-inventory",
                "action.tsv",
                "--auto-resources",
                "--stage3-gpu-devices",
                "0,1",
                "--stage456-jobs=7",
                "--stage456-merge-jobs=5",
            ]
        )

        self.assertTrue(args.auto_resources)
        self.assertEqual(
            args._explicit_resource_options,
            {"stage3_gpu_devices", "stage456_jobs", "stage456_merge_jobs"},
        )

    def test_dry_run_auto_resources_does_not_require_inventory_inputs(self) -> None:
        args = self.module.parse_args(
            [
                "--input",
                "input.jsonl.gz",
                "--output-dir",
                "out",
                "--dry-run",
                "--auto-resources",
                "--require-gpu",
                "--stage6-facts-output-mode",
                "discard",
                "--auto-resource-cpu-fraction",
                "0.5",
            ]
        )
        gpu_mode, stage3_gpu_devices, resource_plan = self.module.apply_runtime_resource_plan(
            args,
            hardware=_hardware(cpu_cores=28, gpu_devices=("0", "1")),
        )
        summary = self.module.build_mixed_pipeline_dry_run_summary(
            args,
            gpu_mode=gpu_mode,
            stage3_gpu_devices=stage3_gpu_devices,
            runtime_resource_plan=resource_plan,
        )

        self.assertEqual(summary["status"], "dry_run")
        self.assertEqual(summary["projected_args"]["stage3_sentence_shards"], 2)
        self.assertEqual(summary["projected_args"]["stage3_gpu_devices"], ["0", "1"])
        self.assertEqual(summary["projected_args"]["stage456_jobs"], 14)
        self.assertEqual(summary["projected_args"]["stage456_merge_jobs"], 14)
        self.assertEqual(summary["validation_warnings"], [])

    def test_dry_run_without_auto_resources_reports_current_args_and_hardware(self) -> None:
        args = self.module.parse_args(
            [
                "--input",
                "input.jsonl.gz",
                "--output-dir",
                "out",
                "--dry-run",
                "--stage456-shards",
                "4",
            ]
        )
        gpu_mode, stage3_gpu_devices, resource_plan = self.module.apply_runtime_resource_plan(
            args,
            hardware=_hardware(cpu_cores=28, gpu_devices=("0", "1")),
        )
        summary = self.module.build_mixed_pipeline_dry_run_summary(
            args,
            gpu_mode=gpu_mode,
            stage3_gpu_devices=stage3_gpu_devices,
            runtime_resource_plan=resource_plan,
            hardware=_hardware(cpu_cores=28, gpu_devices=("0", "1")),
        )

        self.assertFalse(summary["runtime_resource_plan"]["auto_resources_enabled"])
        self.assertEqual(summary["projected_args"]["stage456_shards"], 4)
        self.assertIn(
            "stage456_shards > 1 requires stage6_facts_output_mode='discard'",
            summary["validation_warnings"],
        )

    def test_formal_pipeline_rejects_invalid_stage3_shard_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "stage3_sentence_shards"):
            self.module.run_mixed_caption_pipeline(
                input_paths=[self.tmp_path / "missing.jsonl"],
                output_dir=self.tmp_path / "out",
                object_inventory=self.tmp_path / "object.tsv",
                attribute_inventory=self.tmp_path / "attribute.tsv",
                action_inventory=self.tmp_path / "action.tsv",
                stage3_sentence_shards=0,
                stage6_facts_output_mode="discard",
            )

    def test_standardizes_sharded_stage3_outputs(self) -> None:
        source_dir = self.tmp_path / "stage3_sharded"
        source_dir.mkdir()
        write_jsonl(
            source_dir / "sentence_stage3_records.jsonl",
            [
                {"caption_id": "s1", "tokens": [1]},
                {"caption_id": "s2", "tokens": [1, 2]},
            ],
        )
        write_jsonl(source_dir / "tag_list_stage3_records.jsonl", [{"caption_id": "t1", "tokens": []}])
        write_jsonl(
            source_dir / "stage3_records.jsonl",
            [
                {"caption_id": "s1", "tokens": [1]},
                {"caption_id": "t1", "tokens": []},
                {"caption_id": "s2", "tokens": [1, 2]},
            ],
        )
        (source_dir / "summary.json").write_text("{}", encoding="utf-8")
        sharded_summary = {
            "output_dir": str(source_dir),
            "model": "en_core_web_trf",
            "batch_size": 192,
            "gpu_mode": "require",
            "gpu_devices": ["0", "1"],
            "shards": [
                {
                    "caption_shape": "sentence",
                    "shard_index": 0,
                    "worker_summary": {
                        "total": 1,
                        "written": 1,
                        "token_total": 1,
                        "noun_chunk_total": 2,
                        "tag_segment_total": 0,
                        "protected_span_counts": {"quote": 1},
                        "gpu_enabled": True,
                    },
                },
                {
                    "caption_shape": "sentence",
                    "shard_index": 1,
                    "worker_summary": {
                        "total": 1,
                        "written": 1,
                        "token_total": 2,
                        "noun_chunk_total": 3,
                        "tag_segment_total": 0,
                        "protected_span_counts": {"quote": 2, "hyphen": 1},
                        "gpu_enabled": True,
                    },
                },
                {
                    "caption_shape": "tag_list",
                    "shard_index": 0,
                    "worker_summary": {
                        "total": 1,
                        "written": 1,
                        "token_total": 0,
                        "noun_chunk_total": 0,
                        "tag_segment_total": 4,
                        "protected_span_counts": {},
                        "gpu_enabled": True,
                    },
                },
            ],
            "stage3_combined": {
                "total": 3,
                "written": 3,
                "caption_shape_counts": {"sentence": 2, "tag_list": 1},
                "output_path": str(source_dir / "stage3_records.jsonl"),
            },
        }

        sentence_summary, tag_summary, combined_summary = self.module._standardize_sharded_stage3_outputs(
            sharded_summary,
            standard_stage3_dir=self.tmp_path / "stage3",
        )

        self.assertEqual(sentence_summary["total"], 2)
        self.assertEqual(sentence_summary["token_total"], 3)
        self.assertEqual(sentence_summary["noun_chunk_total"], 5)
        self.assertEqual(sentence_summary["protected_span_counts"], {"hyphen": 1, "quote": 3})
        self.assertEqual(tag_summary["total"], 1)
        self.assertEqual(tag_summary["tag_segment_total"], 4)
        self.assertTrue(combined_summary["sharded"])
        self.assertEqual(
            [row["caption_id"] for row in iter_jsonl(self.tmp_path / "stage3" / "stage3_records.jsonl")],
            ["s1", "t1", "s2"],
        )

    def test_monolithic_stage456_guard_blocks_large_caption_count(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "monolithic Stage 4/5/6 is not safe"):
            self.module._raise_if_monolithic_stage456_too_large(
                caption_total=1_000_000,
                max_captions=250_000,
                output_dir=self.tmp_path / "out",
            )

    def test_monolithic_stage456_guard_can_be_disabled_explicitly(self) -> None:
        self.module._raise_if_monolithic_stage456_too_large(
            caption_total=1_000_000,
            max_captions=0,
            output_dir=self.tmp_path / "out",
        )

    def test_stage456_safety_guard_allows_large_sharded_run(self) -> None:
        self.module._raise_if_stage456_execution_not_safe(
            caption_total=1_000_000,
            max_monolithic_captions=250_000,
            stage456_shards=4,
            output_dir=self.tmp_path / "out",
        )

    def test_formal_pipeline_rejects_sharded_stage456_when_facts_are_written(self) -> None:
        with self.assertRaisesRegex(ValueError, "stage456_shards > 1"):
            self.module.run_mixed_caption_pipeline(
                input_paths=[self.tmp_path / "missing.jsonl"],
                output_dir=self.tmp_path / "out",
                object_inventory=self.tmp_path / "object.tsv",
                attribute_inventory=self.tmp_path / "attribute.tsv",
                action_inventory=self.tmp_path / "action.tsv",
                stage456_shards=4,
                stage6_facts_output_mode="write",
            )


def _temp_base() -> Path:
    roots = [
        os.environ.get("GPIC_TEST_TEMP_ROOT"),
        str(Path.cwd() / ".tmp_tests"),
        r"C:\Users\Public\Documents\ESTsoft\CreatorTemp",
        tempfile.gettempdir(),
    ]
    for root in roots:
        if not root:
            continue
        base = Path(root) / "mixed_caption_pipeline"
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / f"{uuid.uuid4().hex}.tmp"
            probe.write_text("", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return base
        except PermissionError:
            continue
    raise PermissionError("no writable temp directory for mixed caption pipeline tests")


def _write_inventory(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "span_key",
        "observed_surface",
        "decision_status",
        "decision_reason",
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


def _hardware(*, cpu_cores: int, gpu_devices: tuple[str, ...]) -> HardwareResources:
    return HardwareResources(
        cpu_cores=cpu_cores,
        cpu_source="test",
        cpu_quota_cores=float(cpu_cores),
        affinity_cores=cpu_cores,
        os_cpu_count=cpu_cores,
        memory_limit_gib=480.0,
        memory_limit_source="test",
        gpu_devices=gpu_devices,
        gpu_source="test",
        gpu_metadata=tuple({"index": device} for device in gpu_devices),
    )


if __name__ == "__main__":
    unittest.main()
