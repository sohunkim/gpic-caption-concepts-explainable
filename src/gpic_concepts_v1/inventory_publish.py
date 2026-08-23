"""Shared invariants for publishing the active inventory bundle."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping

from gpic_concepts_v1.atomic_io import atomic_text_writer
from gpic_concepts_v1.pipeline_state import (
    output_dir_state_path,
    read_pipeline_state,
    require_stage5_lexicon_bundle_state,
    write_pipeline_state,
)


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def count_tsv_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        try:
            next(reader)
        except StopIteration:
            return 0
        return sum(1 for _ in reader)


def inventory_row_counts(
    *,
    object_inventory: Path,
    attribute_inventory: Path,
    action_inventory: Path,
    action_canonical_inventory: Path | None,
) -> dict[str, int | None]:
    return {
        "object_inventory": count_tsv_rows(object_inventory),
        "attribute_inventory": count_tsv_rows(attribute_inventory),
        "action_inventory": count_tsv_rows(action_inventory),
        "action_canonical_inventory": (
            count_tsv_rows(action_canonical_inventory)
            if action_canonical_inventory is not None
            else None
        ),
    }


def lexicon_row_counts(lexicon_dir: Path) -> dict[str, int]:
    rows: dict[str, int] = {}
    for name in (
        "object_synonyms.tsv",
        "object_parents.tsv",
        "attribute_synonyms.tsv",
        "action_synonyms.tsv",
    ):
        path = lexicon_dir / name
        if path.is_file():
            rows[name.removesuffix(".tsv")] = count_tsv_rows(path)
    return rows


def repoint_stage5_lexicon_state(
    *,
    lexicon_dir: Path,
    attribute_inventory: Path,
    action_canonical_inventory: Path | None,
    published_from_lexicon_dir: Path | None = None,
    published_at_utc: str | None = None,
) -> dict[str, Any]:
    state_path = output_dir_state_path(lexicon_dir)
    state = read_pipeline_state(state_path)
    state.update(
        {
            "path_base": "lexicon_dir",
            "attribute_inventory": _path_relative_to_lexicon_dir(
                attribute_inventory,
                lexicon_dir,
            ),
            "action_canonical_inventory": (
                _path_relative_to_lexicon_dir(
                    action_canonical_inventory,
                    lexicon_dir,
                )
                if action_canonical_inventory is not None
                else None
            ),
            "action_canonical_exported": action_canonical_inventory is not None,
            "output_dir": ".",
            "published_at_utc": published_at_utc or now_utc(),
        }
    )
    if published_from_lexicon_dir is not None:
        state["published_from_lexicon_dir"] = str(published_from_lexicon_dir)
    write_pipeline_state(state_path, state)
    return require_stage5_lexicon_bundle_state(lexicon_dir)


def _path_relative_to_lexicon_dir(path: Path, lexicon_dir: Path) -> str:
    return os.path.relpath(path.absolute(), lexicon_dir.absolute()).replace("\\", "/")


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    with atomic_text_writer(path, newline="\n") as handle:
        handle.write(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")
