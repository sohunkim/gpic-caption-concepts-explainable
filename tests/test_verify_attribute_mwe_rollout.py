import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


def _load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_attribute_mwe_rollout.py"
    spec = importlib.util.spec_from_file_location("verify_attribute_mwe_rollout", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load_script()


class VerifyAttributeMweRolloutTest(unittest.TestCase):
    def test_detects_internal_single_overlap(self) -> None:
        failures: list[str] = []
        mwe = {
            "caption_id": "c1",
            "mention_id": "m1",
            "mention_type": "attribute",
            "text": "light brown",
            "token_start": 2,
            "token_end": 4,
        }
        single = {
            "caption_id": "c1",
            "mention_id": "m2",
            "mention_type": "attribute",
            "text": "brown",
            "token_start": 3,
            "token_end": 4,
        }

        script._check_no_internal_single(
            mwe,
            candidate_mentions=[mwe, single],
            failures=failures,
        )

        self.assertEqual(len(failures), 1)
        self.assertIn("overlaps selected MWE", failures[0])

    def test_quantity_rows_normalize_unified_and_legacy_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unified = root / "attribute_counts.tsv"
            legacy = root / "quantity_counts.tsv"
            unused_legacy = root / "unused_quantity_counts.tsv"
            unified.write_text(
                "attribute\tattribute_kind\tcount\n"
                "two\tquantity\t3\n"
                "blue\tattribute\t9\n",
                encoding="utf-8",
            )
            legacy.write_text(
                "quantity\tcount\n"
                "two\t3\n",
                encoding="utf-8",
            )
            legacy_style_attribute = root / "legacy_attribute_counts.tsv"
            legacy_style_attribute.write_text(
                "attribute\tcount\n"
                "blue\t9\n",
                encoding="utf-8",
            )

            unified_rows = script._normalized_quantity_rows(
                unified_path=unified,
                legacy_path=unused_legacy,
                pair=False,
            )
            legacy_rows = script._normalized_quantity_rows(
                unified_path=legacy_style_attribute,
                legacy_path=legacy,
                pair=False,
            )

        self.assertEqual(unified_rows, legacy_rows)
        self.assertIn(("attribute", "two"), unified_rows[0])

    def test_stage_jsonl_paths_resolves_sharded_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = []
            for shard_index in (0, 1):
                path = (
                    root
                    / "shards"
                    / f"shard_{shard_index:04d}"
                    / "stage4"
                    / "raw_mentions.jsonl"
                )
                path.parent.mkdir(parents=True)
                path.write_text("", encoding="utf-8")
                expected.append(path)

            paths = script._stage_jsonl_paths(
                root,
                stage_name="stage4",
                file_name="raw_mentions.jsonl",
            )

        self.assertEqual(paths, tuple(expected))

    def test_collect_candidate_mwe_mentions_streams_caption_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw_mentions.jsonl"
            rows = [
                {
                    "caption_id": "c1",
                    "mention_id": "m1",
                    "mention_type": "attribute",
                    "text": "light brown",
                    "token_start": 2,
                    "token_end": 4,
                    "source_detail": {
                        "attribute_unit_type": "mwe",
                        "inventory_span_key": "light brown",
                    },
                },
                {
                    "caption_id": "c1",
                    "mention_id": "m2",
                    "mention_type": "object",
                    "text": "dog",
                    "token_start": 4,
                    "token_end": 5,
                },
                {
                    "caption_id": "c2",
                    "mention_id": "m3",
                    "mention_type": "attribute",
                    "text": "dark blue",
                    "token_start": 0,
                    "token_end": 2,
                    "source_detail": {
                        "attribute_unit_type": "mwe",
                        "inventory_span_key": "dark blue",
                    },
                },
            ]
            path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            failures: list[str] = []

            type_counts, mentions, mwe_counts = (
                script._collect_candidate_mwe_mentions(
                    (path,),
                    failures=failures,
                )
            )

        self.assertEqual(failures, [])
        self.assertEqual(type_counts, {"attribute": 2, "object": 1})
        self.assertEqual(set(mentions), {("c1", "m1"), ("c2", "m3")})
        self.assertEqual(mwe_counts, {"light brown": 1, "dark blue": 1})


if __name__ == "__main__":
    unittest.main()
