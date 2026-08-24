from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SRC = ROOT / "src"
import sys

for path in (str(SCRIPTS), str(SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)

from run_stage3_sharded import (  # noqa: E402
    Stage3Shard,
    build_stage3_worker_command,
    contiguous_shard_sizes,
    materialize_empty_stage3_shard,
    merge_shard_jsonl_outputs,
    parse_gpu_devices,
    run_stage3_sharded,
    split_jsonl_contiguous,
    split_jsonl_contiguous_parsed_reference,
)
from gpic_concepts_v1.io_jsonl import iter_jsonl  # noqa: E402


class Stage3ShardedTests(unittest.TestCase):
    def test_contiguous_shard_sizes_preserve_total(self) -> None:
        self.assertEqual(contiguous_shard_sizes(10, 3), [4, 3, 3])
        self.assertEqual(contiguous_shard_sizes(2, 4), [1, 1, 0, 0])
        self.assertEqual(sum(contiguous_shard_sizes(50_000, 2)), 50_000)

    def test_split_jsonl_contiguous_preserves_row_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "rows.jsonl"
            _write_jsonl(source, [{"key": f"c{index}"} for index in range(7)])

            summary = split_jsonl_contiguous(
                source,
                root / "shards",
                shard_count=3,
                file_prefix="rows_shard",
            )

            self.assertEqual(summary.row_count, 7)
            self.assertEqual(summary.shard_row_counts, [3, 2, 2])
            self.assertEqual(
                _read_keys(root / "shards" / "rows_shard_0000.jsonl"),
                ["c0", "c1", "c2"],
            )
            self.assertEqual(
                _read_keys(root / "shards" / "rows_shard_0001.jsonl"),
                ["c3", "c4"],
            )
            self.assertEqual(
                _read_keys(root / "shards" / "rows_shard_0002.jsonl"),
                ["c5", "c6"],
            )

    def test_split_jsonl_contiguous_matches_parsed_reference_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "rows.jsonl"
            source.write_text(
                '{"key":"c0","value":1}\n'
                '\n'
                '{"value":2,"key":"c1"}\n'
                '  {"key":"c2","nested":{"a":3}}\n',
                encoding="utf-8",
            )

            raw_summary = split_jsonl_contiguous(
                source,
                root / "raw",
                shard_count=2,
                file_prefix="rows_shard",
            )
            reference_summary = split_jsonl_contiguous_parsed_reference(
                source,
                root / "reference",
                shard_count=2,
                file_prefix="rows_shard",
            )

            self.assertEqual(raw_summary.row_count, reference_summary.row_count)
            self.assertEqual(raw_summary.shard_row_counts, reference_summary.shard_row_counts)
            raw_rows = [
                row
                for shard_index in range(2)
                for row in iter_jsonl(root / "raw" / f"rows_shard_{shard_index:04d}.jsonl")
            ]
            reference_rows = [
                row
                for shard_index in range(2)
                for row in iter_jsonl(root / "reference" / f"rows_shard_{shard_index:04d}.jsonl")
            ]
            self.assertEqual(raw_rows, reference_rows)

    def test_split_jsonl_contiguous_preserves_nonblank_line_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "rows.jsonl"
            source.write_text(
                '{"z": 1, "a": 2}\n\n{"a": 3, "z": 4}\n',
                encoding="utf-8",
            )

            split_jsonl_contiguous(
                source,
                root / "shards",
                shard_count=1,
                file_prefix="rows_shard",
            )

            self.assertEqual(
                (root / "shards" / "rows_shard_0000.jsonl").read_text(encoding="utf-8"),
                '{"z": 1, "a": 2}\n{"a": 3, "z": 4}\n',
            )

    def test_merge_shard_jsonl_outputs_concatenates_by_shard_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shard_a = root / "a.jsonl"
            shard_b = root / "b.jsonl"
            merged = root / "merged.jsonl"
            _write_jsonl(shard_a, [{"caption_id": "c0"}, {"caption_id": "c1"}])
            _write_jsonl(shard_b, [{"caption_id": "c2"}])

            summary = merge_shard_jsonl_outputs([shard_a, shard_b], merged)

            self.assertEqual(summary["total"], 3)
            self.assertEqual(
                [row["caption_id"] for row in iter_jsonl(merged)],
                ["c0", "c1", "c2"],
            )

    def test_build_stage3_worker_command_forwards_gpu_mode_and_shape(self) -> None:
        shard = Stage3Shard(
            caption_shape="tag_list",
            shard_index=0,
            input_path=Path("in.jsonl"),
            output_path=Path("out.jsonl"),
            summary_path=Path("summary.jsonl"),
            progress_path=Path("progress.json"),
            stdout_path=Path("stdout.log"),
            stderr_path=Path("stderr.log"),
            row_count=1,
            gpu_device="1",
        )

        command = build_stage3_worker_command(
            shard,
            model="en_core_web_trf",
            batch_size=192,
            gpu_mode="require",
            progress_interval_records=1000,
        )

        self.assertIn("--require-gpu", command)
        self.assertIn("--disable-components", command)
        self.assertEqual(command[command.index("--disable-components") + 1], "ner")
        self.assertEqual(command[command.index("--caption-shape") + 1], "tag_list")
        self.assertEqual(command[command.index("--batch-size") + 1], "192")

    def test_materialize_empty_stage3_shard_writes_mergeable_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shard = Stage3Shard(
                caption_shape="tag_list",
                shard_index=1,
                input_path=root / "input.jsonl",
                output_path=root / "stage3" / "tag_list_shard_0001.jsonl",
                summary_path=root / "summaries" / "tag_list_shard_0001.jsonl",
                progress_path=root / "progress" / "tag_list_shard_0001.json",
                stdout_path=root / "logs" / "tag_list_shard_0001.stdout.log",
                stderr_path=root / "logs" / "tag_list_shard_0001.stderr.log",
                row_count=0,
                gpu_device=None,
            )

            summary = materialize_empty_stage3_shard(shard)

            self.assertEqual(summary["row_count"], 0)
            self.assertEqual(shard.output_path.read_text(encoding="utf-8"), "")
            self.assertEqual(list(iter_jsonl(shard.summary_path))[0]["total"], 0)
            merged = root / "merged.jsonl"
            self.assertEqual(
                merge_shard_jsonl_outputs([shard.output_path], merged)["total"],
                0,
            )

    def test_run_stage3_sharded_completes_when_both_shapes_are_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            caption_records = root / "caption_records.jsonl"
            sentence_rows = root / "sentence_rows.jsonl"
            tag_rows = root / "tag_rows.jsonl"
            for path in (caption_records, sentence_rows, tag_rows):
                path.write_text("", encoding="utf-8")

            summary = run_stage3_sharded(
                caption_records=caption_records,
                sentence_rows=sentence_rows,
                tag_rows=tag_rows,
                output_dir=root / "out",
                sentence_shards=2,
                tag_shards=2,
                gpu_mode="require",
                gpu_devices=["0", "1"],
            )

            self.assertEqual(summary["stage3_combined"]["total"], 0)
            self.assertEqual(len(summary["shards"]), 4)
            self.assertTrue(all(row["row_count"] == 0 for row in summary["shards"]))
            self.assertEqual(
                (root / "out" / "stage3_records.jsonl").read_text(encoding="utf-8"),
                "",
            )

    def test_parse_gpu_devices(self) -> None:
        self.assertEqual(parse_gpu_devices("0, 1,,2 "), ["0", "1", "2"])


def _write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def _read_keys(path: Path) -> list[str]:
    return [row["key"] for row in iter_jsonl(path)]


if __name__ == "__main__":
    unittest.main()
