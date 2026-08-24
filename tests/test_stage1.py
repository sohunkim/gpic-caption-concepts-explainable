import unittest

from gpic_concepts_v1.stage1 import (
    TAG_LIST_ROUTE_RULE_ID,
    CaptionShapeError,
    caption_id_from_gpic_row,
    caption_shape_from_gpic_caption_type,
    caption_shape_from_gpic_row,
    determine_caption_shape,
    make_caption_record_from_gpic_row,
    make_caption_record,
    normalize_caption_shape,
)


class Stage1Test(unittest.TestCase):
    def test_normalize_caption_shape_accepts_only_v1_labels(self) -> None:
        self.assertEqual(normalize_caption_shape("sentence"), "sentence")
        self.assertEqual(normalize_caption_shape("tag_list"), "tag_list")
        with self.assertRaises(CaptionShapeError):
            normalize_caption_shape("tag-list")

    def test_explicit_sentence_record(self) -> None:
        record = make_caption_record(
            "c1",
            "A dog sits on a wooden bench.",
            caption_shape="sentence",
        )

        self.assertEqual(record.caption_shape, "sentence")
        self.assertFalse(record.skipped)
        self.assertIsNone(record.skip_reason)
        self.assertEqual(record.rule_ids, ["R1"])

    def test_explicit_tag_list_record_is_routed(self) -> None:
        record = make_caption_record(
            "c2",
            "brown boot, brick wall, display",
            caption_shape="tag_list",
        )

        self.assertEqual(record.caption_shape, "tag_list")
        self.assertFalse(record.skipped)
        self.assertIsNone(record.skip_reason)
        self.assertEqual(record.rule_ids, ["R1", TAG_LIST_ROUTE_RULE_ID])

    def test_meta_is_preserved_but_not_used_for_shape_detection(self) -> None:
        record = make_caption_record(
            "c3",
            "A person riding a bicycle.",
            caption_shape="sentence",
            meta={"source_caption_type": "unknown", "split": "val"},
        )

        self.assertEqual(record.caption_shape, "sentence")
        self.assertEqual(record.meta["split"], "val")

    def test_missing_shape_does_not_use_heuristic(self) -> None:
        with self.assertRaises(CaptionShapeError):
            determine_caption_shape("brown boot, brick wall, display")

    def test_gpic_caption_type_mapping(self) -> None:
        self.assertEqual(caption_shape_from_gpic_caption_type("short"), "sentence")
        self.assertEqual(caption_shape_from_gpic_caption_type("medium"), "sentence")
        self.assertEqual(caption_shape_from_gpic_caption_type("long"), "sentence")
        self.assertEqual(caption_shape_from_gpic_caption_type("tag"), "tag_list")
        with self.assertRaises(CaptionShapeError):
            caption_shape_from_gpic_caption_type("unknown")

    def test_gpic_row_sentence_record(self) -> None:
        record = make_caption_record_from_gpic_row(
            {
                "key": "abc",
                "caption": "A dog sits on a wooden bench.",
                "caption_type": "short",
                "dataset_split": ["val"],
            }
        )

        self.assertEqual(record.caption_id, "abc")
        self.assertEqual(record.caption_shape, "sentence")
        self.assertFalse(record.skipped)
        self.assertEqual(record.meta["caption_type"], "short")

    def test_gpic_row_tag_record(self) -> None:
        record = make_caption_record_from_gpic_row(
            {
                "key": "def",
                "caption": "brown boot, brick wall, display",
                "caption_type": "tag",
                "dataset_split": ["val"],
            }
        )

        self.assertEqual(record.caption_id, "def")
        self.assertEqual(record.caption_shape, "tag_list")
        self.assertFalse(record.skipped)
        self.assertIsNone(record.skip_reason)

    def test_gpic_row_accepts_lite_id_alias(self) -> None:
        record = make_caption_record_from_gpic_row(
            {
                "id": "lite-sha",
                "caption": "A dog sits on a wooden bench.",
                "caption_type": "short",
                "global_index": 7,
            }
        )

        self.assertEqual(record.caption_id, "lite-sha")
        self.assertEqual(record.meta["id"], "lite-sha")

    def test_gpic_row_accepts_matching_key_and_id(self) -> None:
        self.assertEqual(
            caption_id_from_gpic_row({"key": "same-sha", "id": "same-sha"}),
            "same-sha",
        )

    def test_gpic_row_rejects_conflicting_key_and_id(self) -> None:
        with self.assertRaisesRegex(CaptionShapeError, "conflicting caption identifiers"):
            caption_id_from_gpic_row({"key": "key-sha", "id": "id-sha"})

    def test_gpic_row_requires_key_or_id(self) -> None:
        with self.assertRaisesRegex(CaptionShapeError, "key or id"):
            caption_id_from_gpic_row({"caption": "A caption."})

    def test_gpic_row_requires_caption_type(self) -> None:
        with self.assertRaises(CaptionShapeError):
            caption_shape_from_gpic_row({"key": "abc", "caption": "A caption."})


if __name__ == "__main__":
    unittest.main()
