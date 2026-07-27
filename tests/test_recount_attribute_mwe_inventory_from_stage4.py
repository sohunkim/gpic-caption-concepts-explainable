import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


def _load_script():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "recount_attribute_mwe_inventory_from_stage4.py"
    )
    spec = importlib.util.spec_from_file_location(
        "recount_attribute_mwe_inventory_from_stage4",
        path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load_script()


class RecountAttributeMweInventoryFromStage4Test(unittest.TestCase):
    def test_replaces_chosen_mwe_evidence_by_inventory_span_key(self) -> None:
        rows = [
            _mwe_row("stained-glass window", count="1"),
            {
                "span_key": "blue",
                "attribute_unit_type": "single_token",
                "count": "9",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw_mentions.jsonl"
            mentions = [
                _mention("c1", "m1", "stained glass window", "stained-glass window"),
                _mention("c2", "m2", "stained-glass window", "stained-glass window"),
            ]
            path.write_text(
                "".join(json.dumps(row) + "\n" for row in mentions),
                encoding="utf-8",
            )

            updated, summary = script.recount_attribute_mwe_inventory_rows(
                rows,
                (path,),
            )

        self.assertEqual(updated[0]["count"], "2")
        self.assertEqual(updated[0]["caption_count"], "2")
        self.assertEqual(updated[0]["observed_surface"], "stained glass window")
        self.assertEqual(
            updated[0]["example_surfaces"],
            "stained glass window|stained-glass window",
        )
        self.assertEqual(updated[1]["count"], "9")
        self.assertEqual(summary["count_total_delta"], 1)

    def test_unknown_stage4_inventory_key_blocks_recount(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw_mentions.jsonl"
            path.write_text(
                json.dumps(_mention("c1", "m1", "dark blue", "dark blue")) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "absent from chosen inventory"):
                script.recount_attribute_mwe_inventory_rows(
                    [_mwe_row("light blue")],
                    (path,),
                )


def _mwe_row(surface: str, *, count: str = "1") -> dict[str, str]:
    return {
        "span_key": surface,
        "attribute_unit_type": "mwe",
        "span_token_count": "2",
        "anchor_token_offset": "1",
        "attribute_mwe_rule_version": script.ATTRIBUTE_MWE_RULE_VERSION,
        "decision_status": "chosen",
        "count": count,
        "caption_count": count,
        "observed_surface": surface,
        "example_surfaces": surface,
    }


def _mention(
    caption_id: str,
    mention_id: str,
    text: str,
    inventory_span_key: str,
) -> dict[str, object]:
    return {
        "caption_id": caption_id,
        "mention_id": mention_id,
        "mention_type": "attribute",
        "text": text,
        "source_detail": {
            "attribute_unit_type": "mwe",
            "inventory_span_key": inventory_span_key,
        },
    }


if __name__ == "__main__":
    unittest.main()
