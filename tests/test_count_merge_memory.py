from __future__ import annotations

import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import run_stage456_sharded as runner
from gpic_concepts_v1.count_merge_store import CountMergeStore
from gpic_concepts_v1.runtime_memory import (
    MemorySafetyConfig, child_memory_kwargs, current_rss_kib,
)
from gpic_concepts_v1.stage6_export_counts import COUNT_TABLE_SPECS


def fixture(spec, path: Path, shard: int, *, conflict: bool = False) -> None:
    fields = runner._count_table_fieldnames(spec)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fields, delimiter="\t")
        writer.writeheader()
        for index in range(11):
            row = dict.fromkeys(fields, "")
            row.update({name: f"value-{index}" for name in spec.value_fields})
            if conflict and index == 0:
                row[spec.value_fields[0]] = "different"
            row.update({name: ("z|a" if index % 2 else f"z|a{shard}")
                        for name in spec.extra_value_fields})
            row.update(count_key=f"key:\t{index}", count=3 + shard, caption_count=1,
                       example_caption_ids=f"c{shard}|a{shard}",
                       raw_variants="z|a" if index % 2 else f"z|a{shard}",
                       rule_ids=f"R1|R{shard + 2}")
            writer.writerow(row)


class CountMergeMemoryTests(unittest.TestCase):
    def test_every_table_repeated_spills_equal_memory_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for spec in COUNT_TABLE_SPECS:
                with self.subTest(table=spec.file_name):
                    inputs = [root / f"shard-{i}.tsv" for i in range(7)]
                    for i, path in enumerate(inputs):
                        fixture(spec, path, i)
                    memory = root / "memory.tsv"
                    disk = root / "disk.tsv"
                    with patch.object(CountMergeStore, "should_spill", return_value=False):
                        baseline = runner.merge_count_table_shards(spec, inputs, memory)
                    with patch.object(CountMergeStore, "should_spill", return_value=True):
                        spilled = runner.merge_count_table_shards(
                            spec, inputs, disk, memory_kwargs={"memory_check_min_interval_seconds": 0},
                        )
                    self.assertEqual(memory.read_bytes(), disk.read_bytes())
                    self.assertEqual(spilled["count_sum"], baseline["count_sum"])
                    self.assertEqual(spilled["row_count"], 11)
                    self.assertEqual(spilled["memory"]["spill_count"], 77)
                    self.assertEqual(spilled["memory"]["max_cached_keys"], 1)
                    self.assertEqual(spilled["memory"]["backend"], "sqlite_spill")
                    self.assertFalse(list(root.glob(".*_spill_*")))
                    progress = json.loads(disk.with_suffix(".merge_progress.json").read_text())
                    self.assertEqual(progress["status"], "completed")

    def test_conflict_after_spill_preserves_existing_output_and_cleans_temp(self):
        spec = COUNT_TABLE_SPECS[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / "a.tsv", root / "b.tsv"]
            fixture(spec, paths[0], 0)
            fixture(spec, paths[1], 1, conflict=True)
            output = root / "output.tsv"
            output.write_text("previous validated result", encoding="utf-8")
            with patch.object(CountMergeStore, "should_spill", return_value=True):
                with self.assertRaisesRegex(ValueError, "value field conflict"):
                    runner.merge_count_table_shards(spec, paths, output,
                        memory_kwargs={"memory_check_min_interval_seconds": 0})
            self.assertEqual(output.read_text(), "previous validated result")
            self.assertFalse(list(root.glob(".*_spill_*")))
            progress = json.loads(output.with_suffix(".merge_progress.json").read_text())
            self.assertEqual(progress["status"], "failed")
            fixture(spec, paths[1], 1)
            result = runner.merge_count_table_shards(spec, paths, output)
            self.assertEqual(result["count_sum"], 77)

    def test_partitioned_and_parallel_path_propagates_budget(self):
        spec = COUNT_TABLE_SPECS[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.tsv"
            fixture(spec, source, 0)
            single = root / "single.tsv"
            runner.merge_count_table_shards(spec, [source], single)
            result = runner.merge_count_table_shards_partitioned(
                spec, [source], root / "partitioned.tsv", partition_count=3, jobs=2,
                memory_kwargs={"max_rss_gib": 4},
            )
            self.assertLess(result["worker_max_rss_gib"], 2)
            self.assertEqual(len(result["partition_memory"]), 3)
            for item in result["partition_memory"]:
                self.assertEqual(item["max_rss_gib"], result["worker_max_rss_gib"])
            self.assertEqual(single.read_bytes(), (root / "partitioned.tsv").read_bytes())

    def test_spill_threshold_leaves_sort_and_serialization_headroom(self):
        store = CountMergeStore(Path("unused.tsv"), value_fields=("object",),
                                memory_config=MemorySafetyConfig(max_rss_gib=10))
        with patch("gpic_concepts_v1.count_merge_store.current_rss_kib", return_value=8 * 1024**2):
            self.assertTrue(store.should_spill())
        with patch("gpic_concepts_v1.count_merge_store.current_rss_kib", return_value=7 * 1024**2):
            self.assertFalse(store.should_spill())
        with patch("gpic_concepts_v1.count_merge_store.current_rss_kib", return_value=None):
            self.assertTrue(store.should_spill())

    def test_nested_children_share_budget_and_reserve_parent(self):
        with patch("gpic_concepts_v1.runtime_memory.current_rss_kib", return_value=2 * 1024**2):
            units = child_memory_kwargs({"memory_limit_gib": 480}, 2)
            workers = child_memory_kwargs(units, 7)
        self.assertEqual(units["max_rss_gib"], 179)
        self.assertAlmostEqual(workers["max_rss_gib"], 177 / 7)
        self.assertLess(14 * workers["max_rss_gib"], 360)

    def test_unknown_budget_fails_closed(self):
        with patch("gpic_concepts_v1.runtime_memory.detect_cgroup_memory_limit_gib", return_value=None), \
             patch("gpic_concepts_v1.runtime_memory.detect_system_memory_gib", return_value=None):
            with self.assertRaisesRegex(ValueError, "memory budget unavailable"):
                child_memory_kwargs({}, 4)

    def test_duplicate_inputs_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.tsv"
            fixture(COUNT_TABLE_SPECS[0], source, 0)
            with self.assertRaisesRegex(ValueError, "duplicate count table input"):
                runner.merge_count_table_shards(COUNT_TABLE_SPECS[0], [source, source], root / "out.tsv")
            with self.assertRaisesRegex(ValueError, "duplicate Stage 6 input directory"):
                runner.merge_stage6_count_dirs([root, root], root / "merged")

    def test_sqlite_fact_store_checks_repeated_keys_below_hard_limit(self):
        from gpic_concepts_v1.stage6_export_counts import _SqliteCountStore
        from gpic_concepts_v1.schema import FactRow
        with tempfile.TemporaryDirectory() as directory:
            store = _SqliteCountStore(Path(directory) / "counts.sqlite", cache_rows=None,
                memory_config=MemorySafetyConfig(max_rss_gib=10, memory_check_min_interval_seconds=0))
            try:
                fact = FactRow(caption_id="c1", fact_id="f000001", fact_type="entity_exists",
                               count_key="object:dog", values={"object": "dog"}, rule_ids=["R1"],
                               source_mention_ids=[], source_edge_ids=[])
                with patch.object(store, "_should_flush_cache", return_value=False) as check:
                    store.accumulate(fact)
                    store.accumulate(fact)
                self.assertEqual(check.call_count, 2)
                self.assertEqual(store._cache_flush_rss_gib, 8)
            finally:
                store.close()

    def test_parent_exhaustion_fails_before_children(self):
        with patch("gpic_concepts_v1.runtime_memory.current_rss_kib", return_value=3 * 1024**2):
            with self.assertRaisesRegex(MemoryError, "parent already exhausts"):
                child_memory_kwargs({"max_rss_gib": 2}, 2)

    def test_current_host_rss_is_measured(self):
        self.assertGreater(current_rss_kib(), 0)


if __name__ == "__main__":
    unittest.main()
