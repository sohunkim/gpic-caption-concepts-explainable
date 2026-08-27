from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SRC = ROOT / "src"
import sys

for path in (str(SCRIPTS), str(SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)

from run_stage456_sharded import (  # noqa: E402
    extract_stage3_caption_id_from_line,
    merge_stage6_count_dirs,
    merge_count_table_shards,
    merge_count_table_shards_partitioned,
    run_stage456_sharded,
    split_stage3_records,
    stage_function_memory_kwargs,
)
import run_stage456_sharded as stage456_module  # noqa: E402
from gpic_concepts_v1.stage6_export_counts import COUNT_TABLE_SPECS  # noqa: E402


class Stage456ShardedTests(unittest.TestCase):
    def test_stage_function_memory_kwargs_drops_cli_progress_path(self) -> None:
        self.assertEqual(
            stage_function_memory_kwargs(
                {
                    "max_rss_gib": None,
                    "memory_limit_gib": 120.0,
                    "rss_limit_fraction": 0.75,
                    "rss_reserve_gib": 16.0,
                    "progress_path": Path("global_progress.json"),
                }
            ),
            {
                "max_rss_gib": None,
                "memory_limit_gib": 120.0,
                "rss_limit_fraction": 0.75,
                "rss_reserve_gib": 16.0,
            },
        )

    def test_run_stage456_sharded_records_split_run_and_merge_timing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage3 = root / "stage3.jsonl"
            stage3.write_text("", encoding="utf-8")
            output_dir = root / "out"
            object_inventory = root / "object.tsv"
            attribute_inventory = root / "attribute.tsv"
            action_inventory = root / "action.tsv"
            lexicon_dir = root / "lexicons"

            for path in (object_inventory, attribute_inventory, action_inventory):
                path.write_text("span_key\tdecision_status\n", encoding="utf-8")

            with (
                patch.object(stage456_module, "_raise_if_object_inventory_not_ready"),
                patch.object(stage456_module, "_raise_if_attribute_inventory_not_ready"),
                patch.object(stage456_module, "_raise_if_action_inventory_not_ready"),
                patch.object(stage456_module, "split_stage3_records", return_value={"total": 0}),
                patch.object(
                    stage456_module,
                    "run_shards",
                    return_value=[
                        {
                            "shard_index": 0,
                            "stage6": {"output_dir": str(root / "stage6_a")},
                            "timing_seconds": {"total": 1.25},
                        },
                        {
                            "shard_index": 1,
                            "stage6": {"output_dir": str(root / "stage6_b")},
                            "timing_seconds": {"total": 2.5},
                        },
                    ],
                ) as shard_mock,
                patch.object(
                    stage456_module,
                    "merge_stage6_count_dirs",
                    return_value={"output_dir": str(output_dir / "stage6_merged")},
                ) as merge_mock,
            ):
                summary = run_stage456_sharded(
                    stage3_records=stage3,
                    output_dir=output_dir,
                    object_inventory=object_inventory,
                    attribute_inventory=attribute_inventory,
                    action_inventory=action_inventory,
                    lexicon_dir=lexicon_dir,
                    shards=2,
                    jobs=1,
                    merge_jobs=3,
                    memory_kwargs={"max_rss_gib": 12},
                )

            self.assertIn("split_stage3_records", summary["timing_seconds"])
            self.assertIn("run_shards_wall", summary["timing_seconds"])
            self.assertIn("merge_stage6_counts", summary["timing_seconds"])
            self.assertEqual(summary["merge_jobs"], 3)
            self.assertEqual(merge_mock.call_args.kwargs["merge_jobs"], 3)
            self.assertEqual(merge_mock.call_args.kwargs["memory_kwargs"], {"max_rss_gib": 12})
            self.assertEqual(shard_mock.call_args.args[0][0].memory_kwargs["max_rss_gib"], 12)
            self.assertEqual(summary["timing_seconds"]["shards_total_max"], 2.5)
            self.assertEqual(summary["timing_seconds"]["shards_total_sum"], 3.75)

    def test_split_stage3_records_preserves_raw_jsonl_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage3 = root / "stage3.jsonl"
            raw_lines = [
                '{"caption_id":"c0","caption":"A dog.","tokens":[]}',
                '{"caption_id":"c1","caption":"A cat.","tokens":[]}',
                '{"caption_id":"c2","caption":"A bird.","tokens":[]}',
            ]
            stage3.write_text("\n".join(raw_lines) + "\n", encoding="utf-8")
            summary = split_stage3_records(
                stage3,
                shard_records_dir=root / "shards",
                shard_count=2,
            )

            self.assertEqual(summary["record_count"], 3)
            self.assertEqual(
                (root / "shards" / "stage3_shard_0000.jsonl").read_text(encoding="utf-8"),
                raw_lines[0] + "\n" + raw_lines[2] + "\n",
            )
            self.assertEqual(
                (root / "shards" / "stage3_shard_0001.jsonl").read_text(encoding="utf-8"),
                raw_lines[1] + "\n",
            )

    def test_extract_stage3_caption_id_falls_back_when_caption_id_is_not_first(self) -> None:
        caption_id = extract_stage3_caption_id_from_line(
            '{"tokens":[],"caption_id":"c-late"}',
            record_index=0,
        )

        self.assertEqual(caption_id, "c-late")

    def test_merge_count_table_shards_sums_counts_and_merges_evidence(self) -> None:
        spec = next(item for item in COUNT_TABLE_SPECS if item.file_name == "object_counts.tsv")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shard_a = root / "a.tsv"
            shard_b = root / "b.tsv"
            output = root / "merged.tsv"
            _write_rows(
                shard_a,
                [
                    {
                        "count_key": "object:dog",
                        "object": "dog",
                        "parent_concepts": "animal",
                        "parent_synset_ids": "oewn-parent-a",
                        "count": "2",
                        "caption_count": "2",
                        "example_caption_ids": "c1|c3",
                        "raw_variants": "dog|dogs",
                        "rule_ids": "R12|R19",
                    }
                ],
            )
            _write_rows(
                shard_b,
                [
                    {
                        "count_key": "object:cat",
                        "object": "cat",
                        "parent_concepts": "animal",
                        "parent_synset_ids": "oewn-parent-b",
                        "count": "3",
                        "caption_count": "2",
                        "example_caption_ids": "c2|c4",
                        "raw_variants": "cat|cats",
                        "rule_ids": "R12|R19",
                    },
                    {
                        "count_key": "object:dog",
                        "object": "dog",
                        "parent_concepts": "animal|canine",
                        "parent_synset_ids": "oewn-parent-a|oewn-parent-c",
                        "count": "1",
                        "caption_count": "1",
                        "example_caption_ids": "c5",
                        "raw_variants": "dog",
                        "rule_ids": "R12",
                    },
                ],
            )

            summary = merge_count_table_shards(spec, [shard_a, shard_b], output)

            self.assertEqual(summary["row_count"], 2)
            self.assertEqual(summary["count_sum"], 6)
            with output.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(rows[0]["count_key"], "object:cat")
            self.assertEqual(rows[0]["count"], "3")
            self.assertEqual(rows[1]["count_key"], "object:dog")
            self.assertEqual(rows[1]["count"], "3")
            self.assertEqual(rows[1]["caption_count"], "3")
            self.assertEqual(rows[1]["example_caption_ids"], "c1|c3|c5")
            self.assertEqual(rows[1]["parent_concepts"], "animal|canine")
            self.assertEqual(rows[1]["parent_synset_ids"], "oewn-parent-a|oewn-parent-c")

    def test_merge_preserves_single_shard_raw_variant_containing_pipe(self) -> None:
        spec = next(item for item in COUNT_TABLE_SPECS if item.file_name == "object_counts.tsv")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shard = root / "shard.tsv"
            output = root / "merged.tsv"
            raw = '"raw caption text with | literal separator"'
            _write_rows(
                shard,
                [
                    {
                        "count_key": f"object:{raw}",
                        "object": raw,
                        "parent_concepts": "",
                        "parent_synset_ids": "",
                        "count": "1",
                        "caption_count": "1",
                        "example_caption_ids": "c1",
                        "raw_variants": raw,
                        "rule_ids": "R12|R19|R25",
                    }
                ],
            )

            merge_count_table_shards(spec, [shard], output)

            with output.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(rows[0]["raw_variants"], raw)

    def test_merge_preserves_repeated_raw_variant_containing_literal_pipe(self) -> None:
        spec = next(item for item in COUNT_TABLE_SPECS if item.file_name == "object_counts.tsv")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shard_a = root / "a.tsv"
            shard_b = root / "b.tsv"
            output = root / "merged.tsv"
            raw = '"i.c.stars | milwaukee."'
            base_row = {
                "count_key": f"object:{raw}",
                "object": raw,
                "parent_concepts": "",
                "parent_synset_ids": "",
                "count": "1",
                "caption_count": "1",
                "raw_variants": raw,
                "rule_ids": "R12",
            }
            _write_rows(
                shard_a,
                [{**base_row, "example_caption_ids": "c1"}],
            )
            _write_rows(
                shard_b,
                [{**base_row, "example_caption_ids": "c2"}],
            )

            merge_count_table_shards(spec, [shard_a, shard_b], output)

            with output.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(rows[0]["raw_variants"], raw)
            self.assertEqual(rows[0]["count"], "2")
            self.assertEqual(rows[0]["caption_count"], "2")

    def test_partitioned_merge_matches_single_pass_output_bytes(self) -> None:
        spec = next(
            item
            for item in COUNT_TABLE_SPECS
            if item.file_name == "object_cooccurrence_pair_counts.tsv"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shard_a = root / "a.tsv"
            shard_b = root / "b.tsv"
            single_output = root / "single.tsv"
            partitioned_output = root / "partitioned.tsv"
            _write_count_rows(
                spec,
                shard_a,
                [
                    {
                        "count_key": "object_pair_in_caption:dog\tcat",
                        "source_object": "dog",
                        "target_object": "cat",
                        "source_parent_concepts": "animal",
                        "source_parent_synset_ids": "oewn-parent-dog",
                        "target_parent_concepts": "animal",
                        "target_parent_synset_ids": "oewn-parent-cat",
                        "count": "2",
                        "caption_count": "2",
                        "example_caption_ids": "c1|c3",
                        "raw_variants": "dog -> cat",
                        "rule_ids": "R25",
                    },
                    {
                        "count_key": "object_pair_in_caption:apple\tbanana",
                        "source_object": "apple",
                        "target_object": "banana",
                        "source_parent_concepts": "fruit",
                        "source_parent_synset_ids": "oewn-parent-apple",
                        "target_parent_concepts": "fruit",
                        "target_parent_synset_ids": "oewn-parent-banana",
                        "count": "5",
                        "caption_count": "4",
                        "example_caption_ids": "c2|c4|c6|c8",
                        "raw_variants": "apple -> banana",
                        "rule_ids": "R25",
                    },
                ],
            )
            _write_count_rows(
                spec,
                shard_b,
                [
                    {
                        "count_key": "object_pair_in_caption:dog\tcat",
                        "source_object": "dog",
                        "target_object": "cat",
                        "source_parent_concepts": "animal|canine",
                        "source_parent_synset_ids": "oewn-parent-dog|oewn-parent-canine",
                        "target_parent_concepts": "animal",
                        "target_parent_synset_ids": "oewn-parent-cat",
                        "count": "3",
                        "caption_count": "2",
                        "example_caption_ids": "c5|c7",
                        "raw_variants": "dogs -> cats",
                        "rule_ids": "R25|R99",
                    },
                    {
                        "count_key": "object_pair_in_caption:boat\twater",
                        "source_object": "boat",
                        "target_object": "water",
                        "source_parent_concepts": "craft",
                        "source_parent_synset_ids": "oewn-parent-boat",
                        "target_parent_concepts": "liquid",
                        "target_parent_synset_ids": "oewn-parent-water",
                        "count": "1",
                        "caption_count": "1",
                        "example_caption_ids": "c9",
                        "raw_variants": "boat -> water",
                        "rule_ids": "R25",
                    },
                ],
            )

            single_summary = merge_count_table_shards(spec, [shard_a, shard_b], single_output)
            partitioned_summary = merge_count_table_shards_partitioned(
                spec,
                [shard_a, shard_b],
                partitioned_output,
                partition_count=3,
                jobs=2,
            )

            self.assertEqual(partitioned_summary["row_count"], single_summary["row_count"])
            self.assertEqual(partitioned_summary["count_sum"], single_summary["count_sum"])
            self.assertEqual(partitioned_summary["partition_count"], 3)
            self.assertEqual(single_output.read_bytes(), partitioned_output.read_bytes())

    def test_merge_stage6_count_dirs_parallel_records_table_timings(self) -> None:
        object_spec = next(item for item in COUNT_TABLE_SPECS if item.file_name == "object_counts.tsv")
        attribute_spec = next(item for item in COUNT_TABLE_SPECS if item.file_name == "attribute_counts.tsv")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shard_a = root / "shard_a"
            shard_b = root / "shard_b"
            output = root / "merged"
            shard_a.mkdir()
            shard_b.mkdir()
            _write_stage6_summary(
                shard_a,
                fact_type_counts={"entity_exists": 2, "attribute_exists": 1},
                fact_total=3,
            )
            _write_stage6_summary(
                shard_b,
                fact_type_counts={"entity_exists": 1, "attribute_exists": 1},
                fact_total=2,
            )
            _write_rows(
                shard_a / "object_counts.tsv",
                [
                    {
                        "count_key": "object:dog",
                        "object": "dog",
                        "parent_concepts": "animal",
                        "parent_synset_ids": "oewn-parent-a",
                        "count": "2",
                        "caption_count": "2",
                        "example_caption_ids": "c1|c3",
                        "raw_variants": "dog",
                        "rule_ids": "R12",
                    }
                ],
            )
            _write_rows(
                shard_b / "object_counts.tsv",
                [
                    {
                        "count_key": "object:dog",
                        "object": "dog",
                        "parent_concepts": "animal",
                        "parent_synset_ids": "oewn-parent-a",
                        "count": "1",
                        "caption_count": "1",
                        "example_caption_ids": "c5",
                        "raw_variants": "dogs",
                        "rule_ids": "R12|R25",
                    }
                ],
            )
            _write_count_rows(
                attribute_spec,
                shard_a / "attribute_counts.tsv",
                [
                    {
                        "count_key": "attribute:black",
                        "attribute": "black",
                        "count": "1",
                        "caption_count": "1",
                        "example_caption_ids": "c1",
                        "raw_variants": "black",
                        "rule_ids": "R13",
                    }
                ],
            )
            _write_count_rows(
                attribute_spec,
                shard_b / "attribute_counts.tsv",
                [
                    {
                        "count_key": "attribute:black",
                        "attribute": "black",
                        "count": "1",
                        "caption_count": "1",
                        "example_caption_ids": "c5",
                        "raw_variants": "Black",
                        "rule_ids": "R13|R25",
                    }
                ],
            )

            with patch.object(stage456_module, "COUNT_TABLE_SPECS", (object_spec, attribute_spec)):
                summary = merge_stage6_count_dirs([shard_a, shard_b], output, merge_jobs=2)

            self.assertEqual(summary["merge_jobs"], 2)
            self.assertEqual(summary["fact_total"], 5)
            self.assertEqual(summary["table_row_counts"], {"attribute_counts.tsv": 1, "object_counts.tsv": 1})
            self.assertIn("attribute_counts.tsv", summary["table_merge_seconds"])
            self.assertIn("object_counts.tsv", summary["table_merge_seconds"])
            with (output / "object_counts.tsv").open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(rows[0]["count"], "3")
            self.assertEqual(rows[0]["raw_variants"], "dog|dogs")

    def test_split_stage3_records_rejects_duplicate_caption_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage3 = root / "stage3.jsonl"
            stage3.write_text(
                "\n".join(
                    [
                        json.dumps({"caption_id": "c1", "tokens": []}),
                        json.dumps({"caption_id": "c1", "tokens": []}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "globally unique caption_id"):
                split_stage3_records(stage3, shard_records_dir=root / "shards", shard_count=2)


def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    spec = next(item for item in COUNT_TABLE_SPECS if item.file_name == "object_counts.tsv")
    _write_count_rows(spec, path, rows)


def _write_count_rows(spec: Any, path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "count_key",
        *spec.value_fields,
        *spec.extra_value_fields,
        "count",
        "caption_count",
        "example_caption_ids",
        "raw_variants",
        "rule_ids",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_stage6_summary(
    path: Path,
    *,
    fact_type_counts: dict[str, int],
    fact_total: int,
) -> None:
    payload = {
        "fact_total": fact_total,
        "fact_type_counts": fact_type_counts,
    }
    (path / "summary.jsonl").write_text(json.dumps(payload) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
