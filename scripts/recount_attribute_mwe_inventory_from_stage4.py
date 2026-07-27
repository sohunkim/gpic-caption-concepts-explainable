from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from gpic_concepts_v1.atomic_io import atomic_text_writer
from gpic_concepts_v1.attribute_units import (
    ATTRIBUTE_MWE_RULE_VERSION,
    ATTRIBUTE_UNIT_MWE,
    normalize_attribute_surface,
)
from gpic_concepts_v1.io_jsonl import iter_jsonl
from incident_gate import guarded_entrypoint


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replace chosen Attribute MWE count/evidence with the exact mentions "
            "emitted by a resolved Stage 4 prefix run."
        )
    )
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--stage4-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows, summary = recount_attribute_mwe_inventory_rows(
        _read_tsv(Path(args.inventory)),
        _stage4_mention_paths(Path(args.stage4_root)),
    )
    _write_tsv(Path(args.output), rows)
    if args.summary:
        with atomic_text_writer(Path(args.summary)) as handle:
            json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def recount_attribute_mwe_inventory_rows(
    rows: list[dict[str, str]],
    mention_paths: Iterable[Path],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    chosen_rows: dict[str, dict[str, str]] = {}
    for row in rows:
        if row.get("attribute_unit_type", "").strip() != ATTRIBUTE_UNIT_MWE:
            continue
        if row.get("attribute_mwe_rule_version", "") != ATTRIBUTE_MWE_RULE_VERSION:
            raise ValueError(
                "Attribute MWE rule version mismatch: "
                f"{row.get('span_key', '')!r}"
            )
        if row.get("decision_status", "").strip() != "chosen":
            continue
        key = normalize_attribute_surface(row.get("span_key", ""))
        if not key:
            raise ValueError("chosen Attribute MWE row is missing span_key")
        if key in chosen_rows:
            raise ValueError(f"duplicate chosen Attribute MWE span_key: {key}")
        chosen_rows[key] = row

    counts: Counter[str] = Counter()
    captions: dict[str, set[str]] = defaultdict(set)
    surfaces: dict[str, Counter[str]] = defaultdict(Counter)
    mention_keys: set[tuple[str, str]] = set()
    unknown_keys: Counter[str] = Counter()

    for path in mention_paths:
        for mention in iter_jsonl(path):
            detail = _source_detail(mention)
            if (
                mention.get("mention_type") != "attribute"
                or detail.get("attribute_unit_type") != ATTRIBUTE_UNIT_MWE
            ):
                continue
            caption_id = str(mention.get("caption_id", ""))
            mention_id = str(mention.get("mention_id", ""))
            mention_key = (caption_id, mention_id)
            if mention_key in mention_keys:
                raise ValueError(f"duplicate Stage 4 MWE mention key: {mention_key}")
            mention_keys.add(mention_key)

            inventory_key = normalize_attribute_surface(
                str(detail.get("inventory_span_key", ""))
            )
            if inventory_key not in chosen_rows:
                unknown_keys[inventory_key or "<empty>"] += 1
                continue
            counts[inventory_key] += 1
            if caption_id:
                captions[inventory_key].add(caption_id)
            surface = str(mention.get("text", "")).strip()
            if surface:
                surfaces[inventory_key][surface] += 1

    if unknown_keys:
        raise ValueError(
            "Stage 4 emitted Attribute MWE keys absent from chosen inventory: "
            + ", ".join(
                f"{key}={count}" for key, count in unknown_keys.most_common(20)
            )
        )

    zero_mention_keys = sorted(set(chosen_rows) - set(counts))
    if zero_mention_keys:
        raise ValueError(
            "chosen Attribute MWE rows have no Stage 4 mentions: "
            + ", ".join(zero_mention_keys[:20])
        )

    changed_rows = 0
    old_total = 0
    for key, row in chosen_rows.items():
        old_count = int(row.get("count", "") or 0)
        old_total += old_count
        surface_counts = surfaces[key]
        new_values = {
            "count": str(counts[key]),
            "caption_count": str(len(captions[key])),
            "example_caption_ids": "|".join(sorted(captions[key])[:5]),
            "observed_surface": (
                surface_counts.most_common(1)[0][0]
                if surface_counts
                else row.get("observed_surface", "")
            ),
            "example_surfaces": "|".join(
                surface for surface, _ in surface_counts.most_common(5)
            ),
        }
        if any(row.get(field, "") != value for field, value in new_values.items()):
            changed_rows += 1
        row.update(new_values)

    return rows, {
        "status": "ok",
        "chosen_mwe_rows": len(chosen_rows),
        "changed_chosen_mwe_rows": changed_rows,
        "old_chosen_mwe_count_total": old_total,
        "stage4_mwe_mention_total": sum(counts.values()),
        "count_total_delta": sum(counts.values()) - old_total,
        "stage4_mwe_caption_total": len(
            {caption_id for values in captions.values() for caption_id in values}
        ),
        "unknown_stage4_mwe_keys": 0,
        "zero_mention_chosen_mwe_rows": 0,
    }


def _stage4_mention_paths(root: Path) -> tuple[Path, ...]:
    for direct in (root / "raw_mentions.jsonl", root / "stage4" / "raw_mentions.jsonl"):
        if direct.is_file():
            return (direct,)
    sharded = tuple(
        sorted((root / "shards").glob("shard_*/stage4/raw_mentions.jsonl"))
    )
    if sharded:
        return sharded
    raise FileNotFoundError(f"missing direct or sharded Stage 4 raw_mentions.jsonl: {root}")


def _source_detail(row: Mapping[str, Any]) -> Mapping[str, Any]:
    detail = row.get("source_detail")
    return detail if isinstance(detail, Mapping) else {}


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        raise ValueError("attribute inventory is empty")
    fieldnames = list(rows[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_text_writer(path, newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(
        guarded_entrypoint("recount_attribute_mwe_inventory_from_stage4", main)
    )
