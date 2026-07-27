from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan a T5 scene-graph JSONL and count caption-level entity, exact "
            "attribute-term, and entity-attribute pair occurrences for a fixed term set."
        ),
    )
    parser.add_argument("--terms-json", required=True, type=Path)
    parser.add_argument("--records-jsonl", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument(
        "--progress-json",
        type=Path,
        help="Optional single progress JSON updated by atomic replace.",
    )
    parser.add_argument(
        "--attribute-label-policy",
        choices=("exact_name", "surface_first"),
        default="exact_name",
        help=(
            "exact_name matches the 8767 annotator term key (attr.name); "
            "surface_first is available only for explicit raw-surface analyses."
        ),
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100_000,
        help="Emit a JSON progress record after this many input captions; 0 disables progress output.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    terms = json.loads(args.terms_json.read_text(encoding="utf-8"))
    entities = [str(item).strip().lower() for item in terms["entities"]]
    attributes = [str(item).strip().lower() for item in terms["attributes"]]
    pairs = [
        (str(item["entity"]).strip().lower(), str(item["attribute"]).strip().lower())
        for item in terms["pairs"]
    ]

    entity_need = set(entities)
    attribute_need = set(attributes)
    pair_need = set(pairs)

    entity_counts: Counter[str] = Counter()
    attribute_counts: Counter[str] = Counter()
    pair_counts: Counter[tuple[str, str]] = Counter()

    rows = 0
    start = time.time()
    _write_progress(
        args.progress_json,
        {
            "status": "running",
            "phase": "scan",
            "records_scanned": 0,
            "output_json": str(args.output_json),
        },
    )
    with args.records_jsonl.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            rows += 1
            rec = json.loads(line)
            sg = rec.get("scene_graph_v4") or rec.get("scene_graph") or rec.get("sg") or {}

            id_to_entity_norm: dict[Any, str] = {}
            seen_entities: set[str] = set()
            seen_attributes: set[str] = set()
            seen_pairs: set[tuple[str, str]] = set()

            for ent in sg.get("entities") or []:
                name = _entity_label(ent)
                if not name:
                    continue
                ent_id = ent.get("id")
                if ent_id is not None:
                    id_to_entity_norm[ent_id] = name
                if name in entity_need:
                    seen_entities.add(name)

            for attr in sg.get("attributes") or []:
                attr_label = _attribute_label(attr, policy=args.attribute_label_policy)
                if not attr_label:
                    continue
                if attr_label in attribute_need:
                    seen_attributes.add(attr_label)
                entity_label = id_to_entity_norm.get(attr.get("entity_id"))
                if entity_label is not None and (entity_label, attr_label) in pair_need:
                    seen_pairs.add((entity_label, attr_label))

            for entity in seen_entities:
                entity_counts[entity] += 1
            for attribute in seen_attributes:
                attribute_counts[attribute] += 1
            for pair in seen_pairs:
                pair_counts[pair] += 1
            if args.progress_every > 0 and rows % args.progress_every == 0:
                progress = {
                    "status": "running",
                    "phase": "scan",
                    "records_scanned": rows,
                    "elapsed_seconds": round(time.time() - start, 3),
                    "output_json": str(args.output_json),
                }
                _write_progress(args.progress_json, progress)
                print(json.dumps({"event": "progress", **progress}), flush=True)

    out = {
        "records_scanned": rows,
        "elapsed_seconds": time.time() - start,
        "count_basis": "unique_caption_id",
        "entity_label_policy": "canonical_name_exact_then_name_fallback",
        "attribute_label_policy": (
            "exact_t5_attribute_name"
            if args.attribute_label_policy == "exact_name"
            else "exact_visible_surface_first_then_name_fallback"
        ),
        "pair_label_policy": (
            "exact_entity_label_and_exact_t5_attribute_name"
            if args.attribute_label_policy == "exact_name"
            else "exact_entity_label_and_exact_visible_attribute_surface"
        ),
        "entity_caption_counts": {entity: int(entity_counts.get(entity, 0)) for entity in entities},
        "attribute_caption_counts": {
            attribute: int(attribute_counts.get(attribute, 0)) for attribute in attributes
        },
        "entity_attribute_pair_caption_counts": {
            f"{entity}\t{attribute}": int(pair_counts.get((entity, attribute), 0))
            for entity, attribute in pairs
        },
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    temp_path = args.output_json.with_suffix(args.output_json.suffix + ".tmp")
    temp_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, args.output_json)
    _write_progress(
        args.progress_json,
        {
            "status": "complete",
            "phase": "complete",
            "records_scanned": rows,
            "elapsed_seconds": round(out["elapsed_seconds"], 3),
            "output_json": str(args.output_json),
        },
    )
    print(
        json.dumps(
            {
                "event": "completed",
                "output_json": str(args.output_json),
                "records_scanned": rows,
                "elapsed_seconds": round(out["elapsed_seconds"], 3),
                "illuminated_attribute_count": attribute_counts.get("illuminated", 0),
                "cluster_attribute_count": attribute_counts.get("cluster", 0),
                "clustered_attribute_count": attribute_counts.get("clustered", 0),
                "clusters_attribute_count": attribute_counts.get("clusters", 0),
                "silhouette_attribute_count": attribute_counts.get("silhouette", 0),
                "silhouetted_attribute_count": attribute_counts.get("silhouetted", 0),
                "smaller_attribute_count": attribute_counts.get("smaller", 0),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


def _write_progress(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        **payload,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, path)


def _entity_label(ent: dict[str, Any]) -> str:
    value = ent.get("canonical_name") or ent.get("name") or ""
    return str(value).strip().lower()


def _attribute_label(attr: dict[str, Any], *, policy: str) -> str:
    if policy == "exact_name":
        value = attr.get("name") or attr.get("canonical_name") or ""
    else:
        value = attr.get("surface") or attr.get("name") or attr.get("canonical_name") or ""
    return str(value).strip().lower()


if __name__ == "__main__":
    raise SystemExit(main())
