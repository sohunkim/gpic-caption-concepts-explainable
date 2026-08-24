"""Stage 1 caption shape routing.

Stage 1 only decides whether a caption is a sentence caption or a tag-list
caption. It does not tokenize, parse, or infer concepts.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gpic_concepts_v1.schema import CaptionRecord, CaptionShape


TAG_LIST_ROUTE_RULE_ID = "R1.1"
GPIC_SENTENCE_CAPTION_TYPES = frozenset(("short", "medium", "long"))
GPIC_TAG_LIST_CAPTION_TYPES = frozenset(("tag",))


class CaptionShapeError(ValueError):
    """Raised when v1 cannot determine caption shape without guessing."""


def normalize_caption_shape(value: object) -> CaptionShape:
    """Validate and return a v1 caption shape label."""
    if value == "sentence":
        return "sentence"
    if value == "tag_list":
        return "tag_list"
    raise CaptionShapeError(
        "caption_shape must be exactly 'sentence' or 'tag_list'; "
        f"got {value!r}"
    )


def determine_caption_shape(
    caption: str,
    *,
    caption_shape: object | None = None,
) -> CaptionShape:
    """Return caption shape using explicit loader-provided input only.

    v1 intentionally does not infer tag-list captions from punctuation or comma
    patterns. It also does not assume GPIC metadata field names before the row
    schema is inspected.
    """
    if not isinstance(caption, str):
        raise CaptionShapeError("caption must be a string")

    if caption_shape is not None:
        return normalize_caption_shape(caption_shape)

    raise CaptionShapeError(
        "v1 requires explicit loader-provided caption_shape; inspect GPIC row "
        "schema and pass 'sentence' or 'tag_list'"
    )


def caption_shape_from_gpic_caption_type(caption_type: object) -> CaptionShape:
    """Map confirmed GPIC caption_type values to v1 internal shape labels."""
    if caption_type in GPIC_SENTENCE_CAPTION_TYPES:
        return "sentence"
    if caption_type in GPIC_TAG_LIST_CAPTION_TYPES:
        return "tag_list"
    raise CaptionShapeError(
        "unsupported GPIC caption_type; expected one of "
        f"{sorted(GPIC_SENTENCE_CAPTION_TYPES | GPIC_TAG_LIST_CAPTION_TYPES)}, "
        f"got {caption_type!r}"
    )


def caption_shape_from_gpic_row(row: Mapping[str, Any]) -> CaptionShape:
    """Return v1 caption shape from the confirmed GPIC row field."""
    if "caption_type" not in row:
        raise CaptionShapeError("GPIC row is missing required field: caption_type")
    return caption_shape_from_gpic_caption_type(row["caption_type"])


def caption_id_from_gpic_row(row: Mapping[str, Any]) -> str:
    """Return the caption identifier from supported pipeline row schemas.

    Nano-era caption exports use ``key`` while the immutable Lite/Full shard
    manifests use ``id`` for the same caption SHA. Downstream records use
    ``caption_id``. Accept all three spellings, but never guess when a row
    supplies conflicting values.
    """
    present: list[tuple[str, str]] = []
    for field in ("key", "id", "caption_id"):
        if field not in row:
            continue
        value = row[field]
        if not isinstance(value, str) or not value:
            raise CaptionShapeError(
                f"GPIC row identifier {field} must be a non-empty string"
            )
        present.append((field, value))
    if not present:
        raise CaptionShapeError(
            "GPIC row is missing required identifier: key, id, or caption_id"
        )
    values = {value for _field, value in present}
    if len(values) != 1:
        raise CaptionShapeError(
            "GPIC row has conflicting caption identifiers: "
            + ", ".join(f"{field}={value!r}" for field, value in present)
        )
    return present[0][1]


def make_caption_record(
    caption_id: str,
    caption: str,
    *,
    meta: Mapping[str, Any] | None = None,
    caption_shape: object | None = None,
) -> CaptionRecord:
    """Build the Stage 1 CaptionRecord for one input caption."""
    shape = determine_caption_shape(
        caption,
        caption_shape=caption_shape,
    )
    record_meta = dict(meta or {})

    if shape == "tag_list":
        return CaptionRecord(
            caption_id=caption_id,
            caption=caption,
            caption_shape=shape,
            skipped=False,
            skip_reason=None,
            rule_ids=["R1", TAG_LIST_ROUTE_RULE_ID],
            meta=record_meta,
        )

    return CaptionRecord(
        caption_id=caption_id,
        caption=caption,
        caption_shape=shape,
        skipped=False,
        skip_reason=None,
        rule_ids=["R1"],
        meta=record_meta,
    )


def make_caption_record_from_gpic_row(row: Mapping[str, Any]) -> CaptionRecord:
    """Build the Stage 1 CaptionRecord from one confirmed GPIC caption row."""
    if "caption" not in row:
        raise CaptionShapeError("GPIC row is missing required field: caption")

    caption_id = caption_id_from_gpic_row(row)
    shape = caption_shape_from_gpic_row(row)
    meta = {key: value for key, value in row.items() if key != "caption"}
    return make_caption_record(
        caption_id=caption_id,
        caption=str(row["caption"]),
        meta=meta,
        caption_shape=shape,
    )
