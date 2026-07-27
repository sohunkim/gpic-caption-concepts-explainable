from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


VIEW_DEFINITIONS: dict[str, dict[str, Any]] = {
    "objects": {
        "label": "Objects",
        "default_sort": "count",
        "default_dir": "desc",
        "columns": [
            "canonical_object",
            "object_raw_surfaces",
            "object_parent_concepts",
            "count",
            "caption_count",
            "example_caption_ids",
        ],
    },
    "attributes": {
        "label": "Attributes",
        "default_sort": "count",
        "default_dir": "desc",
        "columns": [
            "canonical_attribute",
            "attribute_kind",
            "attribute_raw_surfaces",
            "count",
            "caption_count",
            "example_caption_ids",
        ],
    },
    "actions": {
        "label": "Actions",
        "default_sort": "count",
        "default_dir": "desc",
        "columns": [
            "canonical_action",
            "action_raw_surfaces",
            "count",
            "caption_count",
            "example_caption_ids",
        ],
    },
    "relations": {
        "label": "Relations",
        "default_sort": "count",
        "default_dir": "desc",
        "columns": [
            "source_object",
            "source_object_raw_surfaces",
            "source_parent_concepts",
            "relation",
            "target_object",
            "target_object_raw_surfaces",
            "target_parent_concepts",
            "count",
            "caption_count",
            "example_caption_ids",
        ],
    },
    "object_cooccurrence": {
        "label": "Object Co-occurrence",
        "default_sort": "count",
        "default_dir": "desc",
        "columns": [
            "source_object",
            "source_object_raw_surfaces",
            "source_parent_concepts",
            "target_object",
            "target_object_raw_surfaces",
            "target_parent_concepts",
            "count",
            "caption_count",
            "example_caption_ids",
        ],
    },
    "attribute_object_pairs": {
        "label": "Attribute-Object Pairs",
        "default_sort": "count",
        "default_dir": "desc",
        "columns": [
            "object",
            "object_raw_surfaces",
            "object_parent_concepts",
            "attribute",
            "attribute_kind",
            "attribute_raw_surfaces",
            "count",
            "caption_count",
            "example_caption_ids",
        ],
    },
    "patient_action_pairs": {
        "label": "Patient-Action Pairs",
        "default_sort": "count",
        "default_dir": "desc",
        "columns": [
            "patient_object",
            "patient_object_raw_surfaces",
            "patient_parent_concepts",
            "action",
            "action_raw_surfaces",
            "count",
            "caption_count",
            "example_caption_ids",
        ],
    },
    "agent_action_pairs": {
        "label": "Agent-Action Pairs",
        "default_sort": "count",
        "default_dir": "desc",
        "columns": [
            "agent_object",
            "agent_object_raw_surfaces",
            "agent_parent_concepts",
            "action",
            "action_raw_surfaces",
            "count",
            "caption_count",
            "example_caption_ids",
        ],
    },
    "patient_action_agent_triples": {
        "label": "Patient-Action-Agent Triples",
        "default_sort": "count",
        "default_dir": "desc",
        "columns": [
            "patient_object",
            "patient_object_raw_surfaces",
            "action",
            "action_raw_surfaces",
            "agent_object",
            "agent_object_raw_surfaces",
            "count",
            "caption_count",
            "example_caption_ids",
        ],
    },
    "relation_components": {
        "label": "Relation Components",
        "default_sort": "count",
        "default_dir": "desc",
        "columns": [
            "relation",
            "component_index",
            "component",
            "count",
            "caption_count",
            "example_caption_ids",
        ],
    },
}


def parse_args(argv: Iterable[str] = sys.argv[1:]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a small local interactive report from Stage 5/6 GPIC outputs.",
    )
    parser.add_argument(
        "--input-mode",
        choices=("stage5", "stage6-tsv"),
        default="stage5",
        help=(
            "stage5 aggregates canonical_mentions/edges in memory; "
            "stage6-tsv streams Stage 6 count TSVs and is suitable for large runs."
        ),
    )
    parser.add_argument("--stage5-dir", type=Path)
    parser.add_argument("--stage6-dir", required=True, type=Path)
    parser.add_argument("--caption-records", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--title", default="GPIC 10K Count Report")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] = sys.argv[1:]) -> int:
    args = parse_args(argv)
    output_dir = args.output_dir
    if output_dir.exists() and not args.overwrite:
        raise SystemExit(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.input_mode == "stage5":
        if args.stage5_dir is None:
            raise SystemExit("--stage5-dir is required when --input-mode=stage5")
        mentions_path = args.stage5_dir / "canonical_mentions.jsonl"
        edges_path = args.stage5_dir / "canonical_edges.jsonl"
        mentions = list(_iter_jsonl(mentions_path))
        edges = list(_iter_jsonl(edges_path))
        rows_by_view: Mapping[str, Iterable[dict[str, Any]]] = build_report_rows(
            mentions=mentions,
            edges=edges,
        )
        report_notes: list[str] = []
    else:
        rows_by_view = build_report_rows_from_stage6(args.stage6_dir)
        report_notes = [
            (
                "stage6-tsv mode uses Stage 6 aggregate TSVs. Attach a "
                "report_caption_index built from facts.jsonl to enable full row "
                "caption drill-down beyond Stage 6 example_caption_ids."
            ),
            (
                "patient_action_agent_triples is loaded from "
                "patient_action_agent_triple_counts.tsv when that Stage 6 helper "
                "table is available."
            ),
        ]

    db_path = output_dir / "report.db"
    tmp_db_path = db_path.with_suffix(".db.tmp")
    if tmp_db_path.exists():
        tmp_db_path.unlink()
    build_sqlite_db(
        tmp_db_path,
        rows_by_view,
        captions=_iter_jsonl(args.caption_records),
        title=args.title,
    )
    os.replace(tmp_db_path, db_path)

    write_static_report_files(output_dir, title=args.title)
    view_counts = _read_view_counts(db_path)
    summary = {
        "title": args.title,
        "input_mode": args.input_mode,
        "stage5_dir": str(args.stage5_dir),
        "stage6_dir": str(args.stage6_dir),
        "caption_records": str(args.caption_records),
        "output_dir": str(output_dir),
        "report_db": str(db_path),
        "report_notes": report_notes,
        "view_row_counts": view_counts,
    }
    _atomic_write_text(
        output_dir / "summary.json",
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def build_report_rows(
    *,
    mentions: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    mentions_by_key = {
        (mention["caption_id"], mention["mention_id"]): mention for mention in mentions
    }
    mentions_by_caption: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mention in mentions:
        mentions_by_caption[mention["caption_id"]].append(mention)

    rows_by_view: dict[str, list[dict[str, Any]]] = {
        "objects": _aggregate_mentions(
            mentions,
            mention_type="object",
            value_field="canonical_object",
            raw_field="object_raw_surfaces",
            parent_prefix="object",
        ),
        "attributes": _aggregate_attribute_family_mentions(mentions),
        "actions": _aggregate_mentions(
            mentions,
            mention_type="action",
            value_field="canonical_action",
            raw_field="action_raw_surfaces",
            parent_prefix=None,
        ),
    }
    rows_by_view["attribute_object_pairs"] = _aggregate_attribute_object_pairs(
        edges, mentions_by_key
    )
    rows_by_view["relations"] = _aggregate_relation_triples(edges, mentions_by_key)
    rows_by_view["relation_components"] = _aggregate_relation_components(edges)
    (
        rows_by_view["agent_action_pairs"],
        rows_by_view["patient_action_pairs"],
        rows_by_view["patient_action_agent_triples"],
    ) = _aggregate_event_roles(edges, mentions_by_key)
    rows_by_view["object_cooccurrence"] = _aggregate_object_cooccurrence(
        mentions_by_caption
    )
    return rows_by_view


def build_report_rows_from_stage6(
    stage6_dir: Path,
) -> dict[str, Iterable[dict[str, Any]]]:
    """Build report row iterables directly from Stage 6 count TSVs.

    This avoids loading Stage 5 canonical mention/edge JSONL files into memory.
    It is intentionally aggregate-first: row caption panels use
    example_caption_ids from the count tables.
    """
    _require_stage6_report_inputs(stage6_dir)
    lookups = _load_stage6_entity_lookups(stage6_dir)

    def object_rows() -> Iterable[dict[str, Any]]:
        yield from _stage6_tsv_rows(
            stage6_dir / "object_counts.tsv",
            lambda row: {
                "canonical_object": row.get("object", ""),
                "object_raw_surfaces": row.get("raw_variants", ""),
                "object_parent_concepts": row.get("parent_concepts", ""),
                "count": row.get("count", 0),
                "caption_count": row.get("caption_count", 0),
                "example_caption_ids": row.get("example_caption_ids", ""),
                "_caption_ids": row.get("example_caption_ids", ""),
            },
        )

    def attribute_rows() -> Iterable[dict[str, Any]]:
        yield from _stage6_tsv_rows(
            stage6_dir / "attribute_counts.tsv",
            lambda row: {
                "canonical_attribute": row.get("attribute", ""),
                "attribute_kind": row.get("attribute_kind", "attribute") or "attribute",
                "attribute_raw_surfaces": row.get("raw_variants", ""),
                "count": row.get("count", 0),
                "caption_count": row.get("caption_count", 0),
                "example_caption_ids": row.get("example_caption_ids", ""),
                "_caption_ids": row.get("example_caption_ids", ""),
            },
        )
        if _should_append_legacy_quantity_rows(stage6_dir):
            yield from _stage6_tsv_rows(
                stage6_dir / "quantity_counts.tsv",
                lambda row: {
                    "canonical_attribute": row.get("quantity", ""),
                    "attribute_kind": "quantity",
                    "attribute_raw_surfaces": row.get("raw_variants", ""),
                    "count": row.get("count", 0),
                    "caption_count": row.get("caption_count", 0),
                    "example_caption_ids": row.get("example_caption_ids", ""),
                    "_caption_ids": row.get("example_caption_ids", ""),
                },
            )

    def action_rows() -> Iterable[dict[str, Any]]:
        yield from _stage6_tsv_rows(
            stage6_dir / "action_counts.tsv",
            lambda row: {
                "canonical_action": row.get("action", ""),
                "action_raw_surfaces": row.get("raw_variants", ""),
                "count": row.get("count", 0),
                "caption_count": row.get("caption_count", 0),
                "example_caption_ids": row.get("example_caption_ids", ""),
                "_caption_ids": row.get("example_caption_ids", ""),
            },
        )

    def relation_rows() -> Iterable[dict[str, Any]]:
        yield from _stage6_tsv_rows(
            stage6_dir / "relation_triple_counts.tsv",
            lambda row: {
                "source_object": row.get("source", ""),
                "source_object_raw_surfaces": _lookup_raw_surface(lookups["objects"], row.get("source", "")),
                "source_parent_concepts": row.get("source_parent_concepts", ""),
                "relation": row.get("relation", ""),
                "target_object": row.get("target", ""),
                "target_object_raw_surfaces": _lookup_raw_surface(lookups["objects"], row.get("target", "")),
                "target_parent_concepts": row.get("target_parent_concepts", ""),
                "count": row.get("count", 0),
                "caption_count": row.get("caption_count", 0),
                "example_caption_ids": row.get("example_caption_ids", ""),
                "_caption_ids": row.get("example_caption_ids", ""),
            },
        )

    def object_cooccurrence_rows() -> Iterable[dict[str, Any]]:
        yield from _stage6_tsv_rows(
            stage6_dir / "object_cooccurrence_pair_counts.tsv",
            lambda row: {
                "source_object": row.get("source_object", ""),
                "source_object_raw_surfaces": _lookup_raw_surface(lookups["objects"], row.get("source_object", "")),
                "source_parent_concepts": row.get("source_parent_concepts", ""),
                "target_object": row.get("target_object", ""),
                "target_object_raw_surfaces": _lookup_raw_surface(lookups["objects"], row.get("target_object", "")),
                "target_parent_concepts": row.get("target_parent_concepts", ""),
                "count": row.get("count", 0),
                "caption_count": row.get("caption_count", 0),
                "example_caption_ids": row.get("example_caption_ids", ""),
                "_caption_ids": row.get("example_caption_ids", ""),
            },
        )

    def attribute_object_rows() -> Iterable[dict[str, Any]]:
        yield from _stage6_tsv_rows(
            stage6_dir / "object_attribute_pair_counts.tsv",
            lambda row: {
                "object": row.get("object", ""),
                "object_raw_surfaces": _lookup_raw_surface(lookups["objects"], row.get("object", "")),
                "object_parent_concepts": row.get("object_parent_concepts", ""),
                "attribute": row.get("attribute", ""),
                "attribute_kind": row.get("attribute_kind", "attribute") or "attribute",
                "attribute_raw_surfaces": _lookup_raw_surface(lookups["attributes"], row.get("attribute", "")),
                "count": row.get("count", 0),
                "caption_count": row.get("caption_count", 0),
                "example_caption_ids": row.get("example_caption_ids", ""),
                "_caption_ids": row.get("example_caption_ids", ""),
            },
        )
        if _should_append_legacy_quantity_pair_rows(stage6_dir):
            yield from _stage6_tsv_rows(
                stage6_dir / "object_quantity_pair_counts.tsv",
                lambda row: {
                    "object": row.get("object", ""),
                    "object_raw_surfaces": _lookup_raw_surface(lookups["objects"], row.get("object", "")),
                    "object_parent_concepts": row.get("object_parent_concepts", ""),
                    "attribute": row.get("quantity", ""),
                    "attribute_kind": "quantity",
                    "attribute_raw_surfaces": _lookup_raw_surface(lookups["attributes"], row.get("quantity", "")),
                    "count": row.get("count", 0),
                    "caption_count": row.get("caption_count", 0),
                    "example_caption_ids": row.get("example_caption_ids", ""),
                    "_caption_ids": row.get("example_caption_ids", ""),
                },
            )

    def role_rows(role: str, object_column: str, raw_column: str, parent_column: str) -> Iterable[dict[str, Any]]:
        yield from _stage6_tsv_rows(
            stage6_dir / "agent_patient_pair_counts.tsv",
            lambda row: _stage6_role_row(
                row,
                role=role,
                object_column=object_column,
                raw_column=raw_column,
                parent_column=parent_column,
                lookups=lookups,
            ),
        )

    def relation_component_rows() -> Iterable[dict[str, Any]]:
        yield from _stage6_tsv_rows(
            stage6_dir / "relation_component_counts.tsv",
            lambda row: {
                "relation": row.get("relation", ""),
                "component_index": row.get("component_index", ""),
                "component": row.get("component", ""),
                "count": row.get("count", 0),
                "caption_count": row.get("caption_count", 0),
                "example_caption_ids": row.get("example_caption_ids", ""),
                "_caption_ids": row.get("example_caption_ids", ""),
            },
        )

    def patient_action_agent_triple_rows() -> Iterable[dict[str, Any]]:
        yield from _stage6_tsv_rows(
            stage6_dir / "patient_action_agent_triple_counts.tsv",
            lambda row: {
                "patient_object": row.get("patient_object", ""),
                "patient_object_raw_surfaces": _lookup_raw_surface(lookups["objects"], row.get("patient_object", "")),
                "action": row.get("action", ""),
                "action_raw_surfaces": _lookup_raw_surface(lookups["actions"], row.get("action", "")),
                "agent_object": row.get("agent_object", ""),
                "agent_object_raw_surfaces": _lookup_raw_surface(lookups["objects"], row.get("agent_object", "")),
                "count": row.get("count", 0),
                "caption_count": row.get("caption_count", 0),
                "example_caption_ids": row.get("example_caption_ids", ""),
                "_caption_ids": row.get("example_caption_ids", ""),
            },
        )

    return {
        "objects": object_rows(),
        "attributes": attribute_rows(),
        "actions": action_rows(),
        "relations": relation_rows(),
        "object_cooccurrence": object_cooccurrence_rows(),
        "attribute_object_pairs": attribute_object_rows(),
        "patient_action_pairs": role_rows(
            "patient",
            "patient_object",
            "patient_object_raw_surfaces",
            "patient_parent_concepts",
        ),
        "agent_action_pairs": role_rows(
            "agent",
            "agent_object",
            "agent_object_raw_surfaces",
            "agent_parent_concepts",
        ),
        "patient_action_agent_triples": patient_action_agent_triple_rows(),
        "relation_components": relation_component_rows(),
    }


def _require_stage6_report_inputs(stage6_dir: Path) -> None:
    required_files = [
        "object_counts.tsv",
        "attribute_counts.tsv",
        "action_counts.tsv",
        "relation_triple_counts.tsv",
        "object_cooccurrence_pair_counts.tsv",
        "object_attribute_pair_counts.tsv",
        "agent_patient_pair_counts.tsv",
        "relation_component_counts.tsv",
        "patient_action_agent_triple_counts.tsv",
    ]
    missing = [name for name in required_files if not (stage6_dir / name).exists()]
    if missing:
        message = (
            "stage6-tsv report input is incomplete; refusing to build a report "
            "with silently empty views. Missing required TSV(s): "
            + ", ".join(missing)
        )
        if "patient_action_agent_triple_counts.tsv" in missing:
            message += (
                ". Build it first with "
                "scripts/build_patient_action_agent_triples_from_facts.py "
                "using the Stage 6 facts.jsonl."
            )
        raise FileNotFoundError(message)


def _load_stage6_entity_lookups(stage6_dir: Path) -> dict[str, dict[str, dict[str, str]]]:
    lookups: dict[str, dict[str, dict[str, str]]] = {
        "objects": {},
        "attributes": {},
        "actions": {},
    }
    for row in _iter_tsv(stage6_dir / "object_counts.tsv"):
        key = row.get("object", "")
        if key:
            lookups["objects"][key] = {
                "raw_surfaces": row.get("raw_variants", ""),
                "parent_concepts": row.get("parent_concepts", ""),
            }
    for row in _iter_tsv(stage6_dir / "attribute_counts.tsv"):
        key = row.get("attribute", "")
        if key:
            lookups["attributes"][key] = {"raw_surfaces": row.get("raw_variants", "")}
    if _should_append_legacy_quantity_rows(stage6_dir):
        for row in _iter_tsv(stage6_dir / "quantity_counts.tsv"):
            key = row.get("quantity", "")
            if key and key not in lookups["attributes"]:
                lookups["attributes"][key] = {"raw_surfaces": row.get("raw_variants", "")}
    for row in _iter_tsv(stage6_dir / "action_counts.tsv"):
        key = row.get("action", "")
        if key:
            lookups["actions"][key] = {"raw_surfaces": row.get("raw_variants", "")}
    return lookups


def _should_append_legacy_quantity_rows(stage6_dir: Path) -> bool:
    quantity_path = stage6_dir / "quantity_counts.tsv"
    if not quantity_path.exists():
        return False
    return "attribute_kind" not in _tsv_fieldnames(stage6_dir / "attribute_counts.tsv")


def _should_append_legacy_quantity_pair_rows(stage6_dir: Path) -> bool:
    quantity_pair_path = stage6_dir / "object_quantity_pair_counts.tsv"
    if not quantity_pair_path.exists():
        return False
    return "attribute_kind" not in _tsv_fieldnames(stage6_dir / "object_attribute_pair_counts.tsv")


def _tsv_fieldnames(path: Path) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [str(field) for field in (reader.fieldnames or [])]


def _stage6_role_row(
    row: Mapping[str, str],
    *,
    role: str,
    object_column: str,
    raw_column: str,
    parent_column: str,
    lookups: Mapping[str, Mapping[str, Mapping[str, str]]],
) -> dict[str, Any] | None:
    if row.get("role") != role:
        return None
    target = row.get("target", "")
    action = row.get("action", "")
    return {
        object_column: target,
        raw_column: _lookup_raw_surface(lookups["objects"], target),
        parent_column: row.get("target_parent_concepts", ""),
        "action": action,
        "action_raw_surfaces": _lookup_raw_surface(lookups["actions"], action),
        "count": row.get("count", 0),
        "caption_count": row.get("caption_count", 0),
        "example_caption_ids": row.get("example_caption_ids", ""),
        "_caption_ids": row.get("example_caption_ids", ""),
    }


def _lookup_raw_surface(
    lookup: Mapping[str, Mapping[str, str]],
    key: str | None,
) -> str:
    if not key:
        return ""
    return lookup.get(str(key), {}).get("raw_surfaces", "")


def _stage6_tsv_rows(
    path: Path,
    mapper: Any,
) -> Iterable[dict[str, Any]]:
    for row in _iter_tsv(path):
        mapped = mapper(row)
        if mapped is not None:
            yield mapped


def _iter_tsv(path: Path) -> Iterable[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"missing required TSV: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            yield {str(key): str(value or "") for key, value in row.items()}


def _aggregate_mentions(
    mentions: list[dict[str, Any]],
    *,
    mention_type: str,
    value_field: str,
    raw_field: str,
    parent_prefix: str | None,
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mention in mentions:
        if mention.get("mention_type") == mention_type:
            buckets[str(mention.get("canonical") or "")].append(mention)

    rows: list[dict[str, Any]] = []
    for canonical, bucket in buckets.items():
        row: dict[str, Any] = {
            value_field: canonical,
            raw_field: _join_sorted({_raw_surface(mention) for mention in bucket}),
            "count": len(bucket),
            "caption_count": _caption_count(bucket),
            "example_caption_ids": _example_caption_ids(bucket),
            "_caption_ids": _caption_ids(bucket),
        }
        if parent_prefix:
            row[f"{parent_prefix}_parent_concepts"] = _join_sorted(
                parent
                for mention in bucket
                for parent in mention.get("parent_concepts", [])
                if parent
            )
            row[f"{parent_prefix}_parent_synset_ids"] = _join_sorted(
                synset_id
                for mention in bucket
                for synset_id in _parent_synset_ids(mention)
                if synset_id
            )
        rows.append(row)
    return _sort_count_rows(rows)


def _aggregate_attribute_family_mentions(
    mentions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mention in mentions:
        if mention.get("mention_type") in {"attribute", "quantity"}:
            buckets[str(mention.get("canonical") or "")].append(mention)

    rows: list[dict[str, Any]] = []
    for canonical, bucket in buckets.items():
        rows.append(
            {
                "canonical_attribute": canonical,
                "attribute_kind": _join_sorted(
                    "quantity" if mention.get("mention_type") == "quantity" else "attribute"
                    for mention in bucket
                ),
                "attribute_raw_surfaces": _join_sorted({_raw_surface(mention) for mention in bucket}),
                "count": len(bucket),
                "caption_count": _caption_count(bucket),
                "example_caption_ids": _example_caption_ids(bucket),
                "_caption_ids": _caption_ids(bucket),
            },
        )
    return _sort_count_rows(rows)


def _aggregate_attribute_object_pairs(
    edges: list[dict[str, Any]],
    mentions_by_key: Mapping[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for edge in edges:
        if edge.get("edge_type") not in {"has_attribute", "has_quantity"}:
            continue
        source = _lookup_mention(mentions_by_key, edge, "source_mention_id")
        target = _lookup_mention(mentions_by_key, edge, "target_mention_id")
        buckets[(source["canonical"], target["canonical"])].append((edge, source, target))

    rows: list[dict[str, Any]] = []
    for (obj, attr), bucket in buckets.items():
        rows.append(
            {
                "object": obj,
                "object_raw_surfaces": _join_sorted(_raw_surface(source) for _, source, _ in bucket),
                "object_parent_concepts": _join_sorted(
                    parent
                    for _, source, _ in bucket
                    for parent in source.get("parent_concepts", [])
                    if parent
                ),
                "object_parent_synset_ids": _join_sorted(
                    synset_id
                    for _, source, _ in bucket
                    for synset_id in _parent_synset_ids(source)
                    if synset_id
                ),
                "attribute": attr,
                "attribute_kind": _join_sorted(
                    "quantity" if target.get("mention_type") == "quantity" else "attribute"
                    for _, _, target in bucket
                ),
                "attribute_raw_surfaces": _join_sorted(_raw_surface(target) for _, _, target in bucket),
                "count": len(bucket),
                "caption_count": _caption_count(edge for edge, _, _ in bucket),
                "example_caption_ids": _example_caption_ids(edge for edge, _, _ in bucket),
                "_caption_ids": _caption_ids(edge for edge, _, _ in bucket),
            }
        )
    return _sort_count_rows(rows)


def _aggregate_relation_triples(
    edges: list[dict[str, Any]],
    mentions_by_key: Mapping[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for edge in edges:
        if edge.get("edge_type") != "relation":
            continue
        source = _lookup_mention(mentions_by_key, edge, "source_mention_id")
        target = _lookup_mention(mentions_by_key, edge, "target_mention_id")
        relation = str(edge.get("canonical_label") or edge.get("label") or "")
        buckets[(source["canonical"], relation, target["canonical"])].append((edge, source, target))

    rows: list[dict[str, Any]] = []
    for (source_obj, relation, target_obj), bucket in buckets.items():
        rows.append(
            {
                "source_object": source_obj,
                "source_object_raw_surfaces": _join_sorted(_raw_surface(source) for _, source, _ in bucket),
                "source_parent_concepts": _join_sorted(
                    parent
                    for _, source, _ in bucket
                    for parent in source.get("parent_concepts", [])
                    if parent
                ),
                "source_parent_synset_ids": _join_sorted(
                    synset_id
                    for _, source, _ in bucket
                    for synset_id in _parent_synset_ids(source)
                    if synset_id
                ),
                "relation": relation,
                "relation_raw_surfaces": _join_sorted(_edge_raw_surface(edge) for edge, _, _ in bucket),
                "relation_components": _join_sorted(
                    component
                    for edge, _, _ in bucket
                    for component in _relation_components(edge)
                    if component
                ),
                "target_object": target_obj,
                "target_object_raw_surfaces": _join_sorted(_raw_surface(target) for _, _, target in bucket),
                "target_parent_concepts": _join_sorted(
                    parent
                    for _, _, target in bucket
                    for parent in target.get("parent_concepts", [])
                    if parent
                ),
                "target_parent_synset_ids": _join_sorted(
                    synset_id
                    for _, _, target in bucket
                    for synset_id in _parent_synset_ids(target)
                    if synset_id
                ),
                "count": len(bucket),
                "caption_count": _caption_count(edge for edge, _, _ in bucket),
                "example_caption_ids": _example_caption_ids(edge for edge, _, _ in bucket),
                "_caption_ids": _caption_ids(edge for edge, _, _ in bucket),
            }
        )
    return _sort_count_rows(rows)


def _aggregate_relation_components(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        if edge.get("edge_type") != "relation":
            continue
        relation = str(edge.get("canonical_label") or edge.get("label") or "")
        for index, component in enumerate(_relation_components(edge)):
            buckets[(relation, str(index), component)].append(edge)

    rows = []
    for (relation, index, component), bucket in buckets.items():
        rows.append(
            {
                "relation": relation,
                "component_index": index,
                "component": component,
                "relation_raw_surfaces": _join_sorted(_edge_raw_surface(edge) for edge in bucket),
                "count": len(bucket),
                "caption_count": _caption_count(bucket),
                "example_caption_ids": _example_caption_ids(bucket),
                "_caption_ids": _caption_ids(bucket),
            }
        )
    return _sort_count_rows(rows)


def _aggregate_event_roles(
    edges: list[dict[str, Any]],
    mentions_by_key: Mapping[tuple[str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    agent_buckets: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    patient_buckets: dict[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    roles_by_action_mention: dict[tuple[str, str], dict[str, list[tuple[dict[str, Any], dict[str, Any]]]]] = defaultdict(lambda: defaultdict(list))

    for edge in edges:
        if edge.get("edge_type") != "event_role":
            continue
        action = _lookup_mention(mentions_by_key, edge, "source_mention_id")
        target = _lookup_mention(mentions_by_key, edge, "target_mention_id")
        role = str(edge.get("canonical_label") or edge.get("label") or "")
        if role == "agent":
            agent_buckets[(target["canonical"], action["canonical"])].append((edge, action, target))
        elif role == "patient":
            patient_buckets[(target["canonical"], action["canonical"])].append((edge, action, target))
        roles_by_action_mention[(edge["caption_id"], edge["source_mention_id"])][role].append((edge, target))

    agent_rows = _role_pair_rows(
        agent_buckets,
        object_column="agent_object",
        raw_column="agent_object_raw_surfaces",
        parent_column="agent_parent_concepts",
        parent_synset_column="agent_parent_synset_ids",
    )
    patient_rows = _role_pair_rows(
        patient_buckets,
        object_column="patient_object",
        raw_column="patient_object_raw_surfaces",
        parent_column="patient_parent_concepts",
        parent_synset_column="patient_parent_synset_ids",
    )

    triple_buckets: dict[tuple[str, str, str], list[tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for action_key, role_map in roles_by_action_mention.items():
        agents = role_map.get("agent", [])
        patients = role_map.get("patient", [])
        if not agents or not patients:
            continue
        action = mentions_by_key[action_key]
        for _agent_edge, agent in agents:
            for patient_edge, patient in patients:
                triple_buckets[(patient["canonical"], action["canonical"], agent["canonical"])].append(
                    (patient_edge, patient, action, agent)
                )

    triple_rows = []
    for (patient, action_name, agent), bucket in triple_buckets.items():
        triple_rows.append(
            {
                "patient_object": patient,
                "patient_object_raw_surfaces": _join_sorted(_raw_surface(item[1]) for item in bucket),
                "action": action_name,
                "action_raw_surfaces": _join_sorted(_raw_surface(item[2]) for item in bucket),
                "agent_object": agent,
                "agent_object_raw_surfaces": _join_sorted(_raw_surface(item[3]) for item in bucket),
                "count": len(bucket),
                "caption_count": _caption_count(edge for edge, _, _, _ in bucket),
                "example_caption_ids": _example_caption_ids(edge for edge, _, _, _ in bucket),
                "_caption_ids": _caption_ids(edge for edge, _, _, _ in bucket),
            }
        )

    return _sort_count_rows(agent_rows), _sort_count_rows(patient_rows), _sort_count_rows(triple_rows)


def _role_pair_rows(
    buckets: Mapping[tuple[str, str], list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]],
    *,
    object_column: str,
    raw_column: str,
    parent_column: str,
    parent_synset_column: str,
) -> list[dict[str, Any]]:
    rows = []
    for (obj, action), bucket in buckets.items():
        rows.append(
            {
                object_column: obj,
                raw_column: _join_sorted(_raw_surface(target) for _, _, target in bucket),
                parent_column: _join_sorted(
                    parent
                    for _, _, target in bucket
                    for parent in target.get("parent_concepts", [])
                    if parent
                ),
                parent_synset_column: _join_sorted(
                    synset_id
                    for _, _, target in bucket
                    for synset_id in _parent_synset_ids(target)
                    if synset_id
                ),
                "action": action,
                "action_raw_surfaces": _join_sorted(_raw_surface(action_mention) for _, action_mention, _ in bucket),
                "count": len(bucket),
                "caption_count": _caption_count(edge for edge, _, _ in bucket),
                "example_caption_ids": _example_caption_ids(edge for edge, _, _ in bucket),
                "_caption_ids": _caption_ids(edge for edge, _, _ in bucket),
            }
        )
    return rows


def _aggregate_object_cooccurrence(
    mentions_by_caption: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]]] = defaultdict(list)
    for caption_id, caption_mentions in mentions_by_caption.items():
        objects_by_canonical: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for mention in caption_mentions:
            if mention.get("mention_type") == "object":
                objects_by_canonical[str(mention.get("canonical") or "")].append(mention)
        canonical_objects = sorted(objects_by_canonical)
        for source in canonical_objects:
            for target in canonical_objects:
                if source == target:
                    continue
                buckets[(source, target)].append(
                    (caption_id, objects_by_canonical[source], objects_by_canonical[target])
                )

    rows = []
    for (source, target), bucket in buckets.items():
        source_mentions = [mention for _, mentions, _ in bucket for mention in mentions]
        target_mentions = [mention for _, _, mentions in bucket for mention in mentions]
        rows.append(
            {
                "source_object": source,
                "source_object_raw_surfaces": _join_sorted(_raw_surface(mention) for mention in source_mentions),
                "source_parent_concepts": _join_sorted(
                    parent
                    for mention in source_mentions
                    for parent in mention.get("parent_concepts", [])
                    if parent
                ),
                "source_parent_synset_ids": _join_sorted(
                    synset_id
                    for mention in source_mentions
                    for synset_id in _parent_synset_ids(mention)
                    if synset_id
                ),
                "target_object": target,
                "target_object_raw_surfaces": _join_sorted(_raw_surface(mention) for mention in target_mentions),
                "target_parent_concepts": _join_sorted(
                    parent
                    for mention in target_mentions
                    for parent in mention.get("parent_concepts", [])
                    if parent
                ),
                "target_parent_synset_ids": _join_sorted(
                    synset_id
                    for mention in target_mentions
                    for synset_id in _parent_synset_ids(mention)
                    if synset_id
                ),
                "count": len(bucket),
                "caption_count": len({caption_id for caption_id, _, _ in bucket}),
                "example_caption_ids": "|".join(sorted({caption_id for caption_id, _, _ in bucket})[:5]),
                "_caption_ids": "|".join(sorted({caption_id for caption_id, _, _ in bucket})),
            }
        )
    return _sort_count_rows(rows)


def build_sqlite_db(
    db_path: Path,
    rows_by_view: Mapping[str, Iterable[dict[str, Any]]],
    *,
    captions: Iterable[dict[str, Any]],
    title: str,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute(
            "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        )
        conn.execute(
            "CREATE TABLE captions (caption_id TEXT PRIMARY KEY, caption_index INTEGER, caption_type TEXT, caption_shape TEXT, caption TEXT)",
        )
        _insert_captions(conn, captions)
        row_counts: dict[str, int] = {}
        for view in VIEW_DEFINITIONS:
            row_counts[view] = _create_view_table(conn, view, rows_by_view.get(view, ()))
        metadata = {
            "title": title,
            "views": json.dumps(_view_metadata_from_counts(row_counts), ensure_ascii=False),
        }
        conn.executemany(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            sorted(metadata.items()),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_captions(
    conn: sqlite3.Connection,
    captions: Iterable[dict[str, Any]],
) -> int:
    count = 0
    buffer: list[tuple[str, int, str, str, str]] = []
    for index, row in enumerate(captions):
        meta = row.get("meta")
        if not isinstance(meta, Mapping):
            meta = {}
        caption_id = str(row.get("caption_id") or row.get("key") or "")
        if not caption_id:
            continue
        buffer.append(
            (
                caption_id,
                index,
                str(row.get("caption_type") or meta.get("caption_type") or ""),
                str(row.get("caption_shape") or meta.get("caption_shape") or ""),
                str(row.get("caption") or ""),
            ),
        )
        count += 1
        if len(buffer) >= 5000:
            conn.executemany(
                "INSERT OR REPLACE INTO captions (caption_id, caption_index, caption_type, caption_shape, caption) VALUES (?, ?, ?, ?, ?)",
                buffer,
            )
            buffer.clear()
    if buffer:
        conn.executemany(
            "INSERT OR REPLACE INTO captions (caption_id, caption_index, caption_type, caption_shape, caption) VALUES (?, ?, ?, ?, ?)",
            buffer,
        )
    return count


def _view_metadata_from_counts(row_counts: Mapping[str, int]) -> list[dict[str, Any]]:
    views = []
    for name, definition in VIEW_DEFINITIONS.items():
        columns = list(definition["columns"])
        views.append(
            {
                "name": name,
                "label": definition["label"],
                "default_sort": definition["default_sort"],
                "default_dir": definition["default_dir"],
                "columns": columns,
                "row_count": int(row_counts.get(name, 0)),
            }
        )
    return views


def _create_view_table(
    conn: sqlite3.Connection,
    view: str,
    rows: Iterable[dict[str, Any]],
) -> int:
    columns = list(VIEW_DEFINITIONS[view]["columns"])
    sql_columns = ["_row_id INTEGER PRIMARY KEY", "_caption_ids TEXT"]
    for column in columns:
        column_type = "INTEGER" if column in {"count", "caption_count"} else "TEXT"
        sql_columns.append(f"{_q(column)} {column_type}")
    conn.execute(f"CREATE TABLE {_q(view)} ({', '.join(sql_columns)})")
    insert_columns = ["_row_id", "_caption_ids", *columns]
    placeholders = ", ".join("?" for _ in insert_columns)
    sql = f"INSERT INTO {_q(view)} ({', '.join(_q(column) for column in insert_columns)}) VALUES ({placeholders})"
    row_count = 0
    buffer: list[list[Any]] = []
    for row_index, row in enumerate(rows, start=1):
        buffer.append(
            [
                row_index,
                str(row.get("_caption_ids") or ""),
                *[_coerce_sql_value(row.get(column, ""), column) for column in columns],
            ],
        )
        row_count = row_index
        if len(buffer) >= 5000:
            conn.executemany(sql, buffer)
            buffer.clear()
    if buffer:
        conn.executemany(sql, buffer)
    for column in columns:
        if column in {"count", "caption_count"} or column.startswith("canonical_") or column in {
            "object",
            "attribute",
            "action",
            "relation",
            "source_object",
            "target_object",
            "agent_object",
            "patient_object",
            "component",
        }:
            conn.execute(f"CREATE INDEX idx_{view}_{column} ON {_q(view)} ({_q(column)})")
    return row_count


def _read_view_counts(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT value FROM metadata WHERE key = 'views'").fetchone()
    if rows is None:
        return {}
    views = json.loads(str(rows[0]))
    return {str(view["name"]): int(view.get("row_count", 0)) for view in views}


def write_static_report_files(output_dir: Path, *, title: str) -> None:
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(output_dir / "viewer.html", VIEWER_HTML.replace("__TITLE__", title))
    _atomic_write_text(output_dir / "report_server.py", REPORT_SERVER_PY)
    _atomic_write_text(assets_dir / "report.css", REPORT_CSS)
    _atomic_write_text(assets_dir / "report.js", REPORT_JS)
    repo_python = Path(sys.executable)
    controller_script = Path(__file__).with_name(
        "run_report_server_operation.py"
    ).resolve()
    start_bat = f"""@echo off
setlocal
cd /d "%~dp0"
set PY={repo_python}
set CONTROLLER={controller_script}
if not exist "%PY%" exit /b 1
if not exist "%CONTROLLER%" exit /b 1
"%PY%" "%CONTROLLER%" --operation-timeout-seconds 60 -- start --report-dir "." --state-dir "%TEMP%\\gpic_report_server_state" --state-name "{output_dir.name}_8765" --host 127.0.0.1 --port 8765 --user gpic --password 1234 --readiness-timeout-seconds 30
"""
    _atomic_write_text(output_dir / "start_report.bat", start_bat)


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _lookup_mention(
    mentions_by_key: Mapping[tuple[str, str], dict[str, Any]],
    edge: Mapping[str, Any],
    endpoint_field: str,
) -> dict[str, Any]:
    key = (str(edge["caption_id"]), str(edge[endpoint_field]))
    mention = mentions_by_key.get(key)
    if mention is None:
        raise KeyError(f"missing mention endpoint: {key}")
    return mention


def _raw_surface(mention: Mapping[str, Any]) -> str:
    raw_text = str(mention.get("raw_text") or "").strip().lower()
    if raw_text:
        return raw_text
    return str(mention.get("raw_lemma") or "").strip().lower()


def _edge_raw_surface(edge: Mapping[str, Any]) -> str:
    detail = edge.get("canonical_detail")
    if isinstance(detail, Mapping):
        value = detail.get("raw_span_surface")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return str(edge.get("label") or edge.get("canonical_label") or "").strip().lower()


def _relation_components(edge: Mapping[str, Any]) -> list[str]:
    detail = edge.get("canonical_detail")
    if isinstance(detail, Mapping):
        components = detail.get("relation_components")
        if isinstance(components, list):
            return [
                str(component).strip().lower()
                for component in components
                if str(component).strip()
            ]
    relation = str(edge.get("canonical_label") or edge.get("label") or "")
    return [component for component in relation.strip().lower().split() if component]


def _parent_synset_ids(mention: Mapping[str, Any]) -> list[str]:
    detail = mention.get("canonical_detail")
    if not isinstance(detail, Mapping):
        return []
    value = detail.get("parent_oewn_synsets")
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str):
        return [part for part in value.split("|") if part]
    return []


def _caption_count(records: Iterable[Mapping[str, Any]]) -> int:
    return len({str(record.get("caption_id") or "") for record in records})


def _example_caption_ids(records: Iterable[Mapping[str, Any]]) -> str:
    return "|".join(sorted({str(record.get("caption_id") or "") for record in records})[:5])


def _caption_ids(records: Iterable[Mapping[str, Any]]) -> str:
    return "|".join(sorted({str(record.get("caption_id") or "") for record in records if record.get("caption_id")}))


def _join_sorted(values: Iterable[str]) -> str:
    return "|".join(sorted({str(value) for value in values if str(value)}))


def _sort_count_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: (-int(row.get("count", 0)), json.dumps(row, ensure_ascii=False, sort_keys=True)))


def _coerce_sql_value(value: Any, column: str) -> Any:
    if column in {"count", "caption_count"}:
        return int(value or 0)
    return str(value or "")


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp_path, path)


VIEWER_HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__TITLE__</title>
  <link rel="stylesheet" href="assets/report.css">
</head>
<body>
  <header class="topbar">
    <div>
      <div class="eyebrow">GPIC Caption-To-Concept</div>
      <h1>__TITLE__</h1>
    </div>
    <div class="server-note">SQLite-backed local viewer</div>
  </header>
  <main class="layout">
    <aside class="sidebar">
      <div class="section-title">Views</div>
      <nav id="viewTabs" class="view-tabs"></nav>
    </aside>
    <section class="content">
      <div class="toolbar">
        <label>
          Search
          <input id="globalSearch" type="search" placeholder="raw, canonical, parent...">
        </label>
        <label>
          Rows
          <select id="pageSize">
            <option>10</option>
            <option selected>20</option>
            <option>50</option>
            <option>100</option>
            <option>200</option>
          </select>
        </label>
        <button id="clearFilters" type="button">Clear filters</button>
      </div>
      <div class="active-filters" id="activeFilters"></div>
      <div class="table-wrap">
        <table id="dataTable">
          <thead></thead>
          <tbody></tbody>
        </table>
      </div>
      <div class="pager">
        <button id="prevPage" type="button">Prev</button>
        <span id="pageInfo"></span>
        <button id="nextPage" type="button">Next</button>
      </div>
      <section id="captionPanel" class="caption-panel" hidden>
        <div class="caption-panel-head">
          <div>
            <div class="eyebrow">Selected Row Captions</div>
            <h2 id="captionPanelTitle"></h2>
          </div>
          <button id="closeCaptionPanel" type="button">Close</button>
        </div>
        <div id="captionList" class="caption-list"></div>
        <div class="pager small">
          <button id="captionPrev" type="button">Prev</button>
          <span id="captionPageInfo"></span>
          <button id="captionNext" type="button">Next</button>
        </div>
      </section>
    </section>
  </main>
  <dialog id="filterDialog">
    <form method="dialog">
      <div class="dialog-head">
        <div>
          <div class="eyebrow">Filter</div>
          <h2 id="filterTitle"></h2>
        </div>
        <button value="close" type="submit">Close</button>
      </div>
      <input id="filterSearch" type="search" placeholder="Search filter values">
      <div id="filterValues" class="filter-values"></div>
      <div class="pager small">
        <button id="filterPrev" type="button">Prev</button>
        <span id="filterPageInfo"></span>
        <button id="filterNext" type="button">Next</button>
      </div>
    </form>
  </dialog>
  <script src="assets/report.js"></script>
</body>
</html>
"""


REPORT_CSS = """
:root {
  color-scheme: light;
  --bg: #f6f7f9;
  --panel: #ffffff;
  --line: #d8dde6;
  --text: #17202a;
  --muted: #627084;
  --accent: #2867c7;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.45 system-ui, -apple-system, Segoe UI, sans-serif;
}
.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 18px 22px;
  background: var(--panel);
  border-bottom: 1px solid var(--line);
}
h1, h2 { margin: 0; }
h1 { font-size: 20px; }
h2 { font-size: 18px; }
.eyebrow, .server-note, .section-title { color: var(--muted); font-size: 12px; }
.layout {
  display: grid;
  grid-template-columns: 250px minmax(0, 1fr);
  min-height: calc(100vh - 76px);
}
.sidebar {
  padding: 16px;
  border-right: 1px solid var(--line);
  background: #eef2f7;
}
.view-tabs {
  display: grid;
  gap: 6px;
  margin-top: 10px;
}
.view-tabs button,
.toolbar button,
.pager button,
.dialog-head button,
.caption-panel-head button {
  border: 1px solid var(--line);
  background: var(--panel);
  color: var(--text);
  border-radius: 6px;
  padding: 8px 10px;
  cursor: pointer;
}
.view-tabs button {
  text-align: left;
}
.view-tabs button.active {
  border-color: var(--accent);
  color: var(--accent);
  font-weight: 650;
}
.content {
  min-width: 0;
  padding: 16px;
}
.toolbar {
  display: flex;
  gap: 12px;
  align-items: end;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
label {
  display: grid;
  gap: 4px;
  color: var(--muted);
}
input, select {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 8px 9px;
  min-width: 190px;
  background: var(--panel);
  color: var(--text);
}
.active-filters {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  min-height: 28px;
  margin-bottom: 8px;
}
.chip {
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 4px 8px;
  background: var(--panel);
}
.table-wrap {
  overflow: auto;
  max-height: calc(100vh - 205px);
  border: 1px solid var(--line);
  background: var(--panel);
}
table {
  width: 100%;
  min-width: 980px;
  border-collapse: collapse;
}
th, td {
  border-bottom: 1px solid var(--line);
  padding: 8px 9px;
  text-align: left;
  vertical-align: top;
  max-width: 360px;
}
tbody tr {
  cursor: pointer;
}
tbody tr:hover,
tbody tr.selected {
  background: #eef5ff;
}
th {
  position: sticky;
  top: 0;
  background: #f0f3f8;
  z-index: 1;
  white-space: nowrap;
}
th button {
  border: 0;
  background: transparent;
  cursor: pointer;
  font: inherit;
  font-weight: 700;
}
.filter-button {
  margin-left: 4px;
  color: var(--accent);
}
td.numeric { text-align: right; font-variant-numeric: tabular-nums; }
.pager {
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: center;
  padding: 12px;
}
.pager.small { padding: 8px 0 0; }
dialog {
  width: min(680px, 94vw);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 16px;
}
.dialog-head {
  display: flex;
  justify-content: space-between;
  align-items: start;
  margin-bottom: 12px;
}
.filter-values {
  display: grid;
  gap: 4px;
  max-height: 420px;
  overflow: auto;
  margin-top: 10px;
}
.filter-value {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  border: 1px solid var(--line);
  background: var(--panel);
  padding: 8px;
  border-radius: 6px;
  cursor: pointer;
}
.filter-value.selected {
  border-color: var(--accent);
  background: #eaf2ff;
}
.caption-panel {
  margin-top: 12px;
  border: 1px solid var(--line);
  background: var(--panel);
  padding: 14px;
}
.caption-panel-head {
  display: flex;
  align-items: start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}
.caption-list {
  display: grid;
  gap: 8px;
  max-height: 360px;
  overflow: auto;
}
.caption-card {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 9px;
  background: #fbfcfe;
}
.caption-meta {
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 4px;
}
.caption-text {
  white-space: pre-wrap;
}
.muted { color: var(--muted); }
@media (max-width: 850px) {
  .layout { grid-template-columns: 1fr; }
  .sidebar { border-right: 0; border-bottom: 1px solid var(--line); }
}
"""


REPORT_JS = """
const state = {
  views: [],
  view: null,
  page: 1,
  pageSize: 20,
  sort: null,
  dir: 'desc',
  q: '',
  filters: {},
  filterField: null,
  filterPage: 1,
  filterQ: '',
  selectedRowId: null,
  selectedRowLabel: '',
  captionPage: 1,
  captionPageSize: 50,
  captionTotal: 0,
};

const viewTabs = document.getElementById('viewTabs');
const table = document.getElementById('dataTable');
const pageInfo = document.getElementById('pageInfo');
const pageSize = document.getElementById('pageSize');
const globalSearch = document.getElementById('globalSearch');
const activeFilters = document.getElementById('activeFilters');
const filterDialog = document.getElementById('filterDialog');
const filterTitle = document.getElementById('filterTitle');
const filterSearch = document.getElementById('filterSearch');
const filterValues = document.getElementById('filterValues');
const filterPageInfo = document.getElementById('filterPageInfo');
const captionPanel = document.getElementById('captionPanel');
const captionPanelTitle = document.getElementById('captionPanelTitle');
const captionList = document.getElementById('captionList');
const captionPageInfo = document.getElementById('captionPageInfo');

async function api(path, params = {}) {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) {
      for (const item of value) {
        if (item !== undefined && item !== null && item !== '') url.searchParams.append(key, item);
      }
    } else if (value !== undefined && value !== null && value !== '') {
      url.searchParams.set(key, value);
    }
  }
  const response = await fetch(url);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function currentView() {
  return state.views.find((view) => view.name === state.view);
}

function filterParams() {
  const params = {};
  for (const [field, values] of Object.entries(state.filters)) {
    if (Array.isArray(values) && values.length) params[`filter_${field}`] = values;
  }
  return params;
}

async function init() {
  const data = await api('/api/views');
  state.views = data.views;
  state.view = state.views[0]?.name;
  const view = currentView();
  state.sort = view.default_sort;
  state.dir = view.default_dir;
  renderTabs();
  await loadRows();
}

function renderTabs() {
  viewTabs.innerHTML = '';
  for (const view of state.views) {
    const button = document.createElement('button');
    button.textContent = `${view.label} (${view.row_count.toLocaleString()})`;
    button.className = view.name === state.view ? 'active' : '';
    button.onclick = async () => {
      state.view = view.name;
      state.page = 1;
      state.filters = {};
      clearCaptionSelection();
      state.sort = view.default_sort;
      state.dir = view.default_dir;
      renderTabs();
      await loadRows();
    };
    viewTabs.appendChild(button);
  }
}

async function loadRows() {
  const data = await api('/api/rows', {
    view: state.view,
    page: state.page,
    page_size: state.pageSize,
    sort: state.sort,
    dir: state.dir,
    q: state.q,
    ...filterParams(),
  });
  renderFilters();
  renderTable(data);
}

function renderFilters() {
  activeFilters.innerHTML = '';
  for (const [field, values] of Object.entries(state.filters)) {
    for (const value of values) {
      const chip = document.createElement('button');
      chip.className = 'chip';
      chip.textContent = `${field}: ${value} ×`;
      chip.onclick = async () => {
        state.filters[field] = selectedFilterValues(field).filter((selected) => selected !== value);
        if (!state.filters[field].length) delete state.filters[field];
        state.page = 1;
        await loadRows();
      };
      activeFilters.appendChild(chip);
    }
  }
}

function renderTable(data) {
  const thead = table.querySelector('thead');
  const tbody = table.querySelector('tbody');
  thead.innerHTML = '';
  tbody.innerHTML = '';
  const headerRow = document.createElement('tr');
  for (const column of data.columns) {
    const th = document.createElement('th');
    const sortButton = document.createElement('button');
    sortButton.textContent = column + sortSuffix(column);
    sortButton.onclick = async () => {
      if (state.sort === column) state.dir = state.dir === 'asc' ? 'desc' : 'asc';
      else {
        state.sort = column;
        state.dir = column === 'count' || column === 'caption_count' ? 'desc' : 'asc';
      }
      state.page = 1;
      await loadRows();
    };
    th.appendChild(sortButton);
    if (!['count', 'caption_count', 'example_caption_ids'].includes(column)) {
      const filterButton = document.createElement('button');
      filterButton.className = 'filter-button';
      filterButton.textContent = 'filter';
      filterButton.onclick = () => openFilter(column);
      th.appendChild(filterButton);
    }
    headerRow.appendChild(th);
  }
  thead.appendChild(headerRow);

  for (const row of data.rows) {
    const tr = document.createElement('tr');
    if (row._row_id === state.selectedRowId) tr.className = 'selected';
    tr.onclick = async () => {
      state.selectedRowId = row._row_id;
      state.selectedRowLabel = describeRow(row, data.columns);
      state.captionPage = 1;
      await loadRowCaptions();
      renderTable(data);
    };
    for (const column of data.columns) {
      const td = document.createElement('td');
      if (column === 'count' || column === 'caption_count') td.className = 'numeric';
      td.textContent = row[column] ?? '';
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  const totalPages = Math.max(1, Math.ceil(data.total / state.pageSize));
  pageInfo.textContent = `Page ${state.page.toLocaleString()} / ${totalPages.toLocaleString()} · ${data.total.toLocaleString()} rows`;
  document.getElementById('prevPage').disabled = state.page <= 1;
  document.getElementById('nextPage').disabled = state.page >= totalPages;
}

function describeRow(row, columns) {
  const parts = [];
  for (const column of columns) {
    if (['count', 'caption_count', 'example_caption_ids'].includes(column)) continue;
    const value = row[column];
    if (value !== undefined && value !== null && value !== '') parts.push(`${column}: ${value}`);
    if (parts.length >= 3) break;
  }
  return parts.join(' · ') || `row ${row._row_id}`;
}

async function loadRowCaptions() {
  if (!state.selectedRowId) return;
  const data = await api('/api/row-captions', {
    view: state.view,
    row_id: state.selectedRowId,
    page: state.captionPage,
    page_size: state.captionPageSize,
  });
  state.captionTotal = data.total;
  captionPanel.hidden = false;
  captionPanelTitle.textContent = `${state.selectedRowLabel} (${data.total.toLocaleString()} captions)`;
  captionList.innerHTML = '';
  for (const caption of data.captions) {
    const card = document.createElement('article');
    card.className = 'caption-card';
    const meta = document.createElement('div');
    meta.className = 'caption-meta';
    meta.textContent = `${caption.caption_id} · ${caption.caption_type || ''} · ${caption.caption_shape || ''}`;
    const text = document.createElement('div');
    text.className = 'caption-text';
    text.textContent = caption.caption || '';
    card.appendChild(meta);
    card.appendChild(text);
    captionList.appendChild(card);
  }
  if (!data.captions.length) {
    captionList.innerHTML = '<div class="muted">No captions found for this row.</div>';
  }
  const totalPages = Math.max(1, Math.ceil(data.total / state.captionPageSize));
  captionPageInfo.textContent = `Page ${state.captionPage.toLocaleString()} / ${totalPages.toLocaleString()} · ${data.total.toLocaleString()} captions`;
  document.getElementById('captionPrev').disabled = state.captionPage <= 1;
  document.getElementById('captionNext').disabled = state.captionPage >= totalPages;
}

function clearCaptionSelection() {
  state.selectedRowId = null;
  state.selectedRowLabel = '';
  state.captionPage = 1;
  state.captionTotal = 0;
  captionPanel.hidden = true;
  captionList.innerHTML = '';
}

function sortSuffix(column) {
  if (state.sort !== column) return '';
  return state.dir === 'asc' ? ' ↑' : ' ↓';
}

async function openFilter(column) {
  state.filterField = column;
  state.filterPage = 1;
  state.filterQ = '';
  filterSearch.value = '';
  filterTitle.textContent = column;
  filterDialog.showModal();
  await loadFilterValues();
}

async function loadFilterValues() {
  const data = await api('/api/filter-values', {
    view: state.view,
    field: state.filterField,
    page: state.filterPage,
    page_size: 50,
    q: state.filterQ,
  });
  filterValues.innerHTML = '';
  for (const item of data.values) {
    const button = document.createElement('button');
    button.type = 'button';
    const selected = isFilterSelected(state.filterField, item.value);
    button.className = selected ? 'filter-value selected' : 'filter-value';
    button.innerHTML = `<span>${selected ? '[selected] ' : ''}${escapeHtml(item.value)}</span><span class="muted">${item.row_count} rows · ${item.total_count} count</span>`;
    button.onclick = async () => {
      await toggleFilterValue(state.filterField, item.value);
    };
    filterValues.appendChild(button);
  }
  const totalPages = Math.max(1, Math.ceil(data.total / 50));
  filterPageInfo.textContent = `Page ${state.filterPage} / ${totalPages} · ${data.total} values`;
  document.getElementById('filterPrev').disabled = state.filterPage <= 1;
  document.getElementById('filterNext').disabled = state.filterPage >= totalPages;
}

function selectedFilterValues(field) {
  const values = state.filters[field];
  return Array.isArray(values) ? values : [];
}

function isFilterSelected(field, value) {
  return selectedFilterValues(field).includes(value);
}

async function toggleFilterValue(field, value) {
  const values = selectedFilterValues(field);
  if (values.includes(value)) {
    state.filters[field] = values.filter((selected) => selected !== value);
    if (!state.filters[field].length) delete state.filters[field];
  } else {
    state.filters[field] = [...values, value];
  }
  state.page = 1;
  await loadRows();
  await loadFilterValues();
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#039;',
  }[char]));
}

pageSize.onchange = async () => {
  state.pageSize = Number(pageSize.value);
  state.page = 1;
  await loadRows();
};
globalSearch.oninput = debounce(async () => {
  state.q = globalSearch.value;
  state.page = 1;
  await loadRows();
}, 250);
document.getElementById('clearFilters').onclick = async () => {
  state.filters = {};
  state.q = '';
  globalSearch.value = '';
  state.page = 1;
  await loadRows();
};
document.getElementById('prevPage').onclick = async () => {
  if (state.page > 1) {
    state.page -= 1;
    await loadRows();
  }
};
document.getElementById('nextPage').onclick = async () => {
  state.page += 1;
  await loadRows();
};
document.getElementById('closeCaptionPanel').onclick = async () => {
  clearCaptionSelection();
  await loadRows();
};
document.getElementById('captionPrev').onclick = async () => {
  if (state.captionPage > 1) {
    state.captionPage -= 1;
    await loadRowCaptions();
  }
};
document.getElementById('captionNext').onclick = async () => {
  state.captionPage += 1;
  await loadRowCaptions();
};
filterSearch.oninput = debounce(async () => {
  state.filterQ = filterSearch.value;
  state.filterPage = 1;
  await loadFilterValues();
}, 250);
document.getElementById('filterPrev').onclick = async () => {
  if (state.filterPage > 1) {
    state.filterPage -= 1;
    await loadFilterValues();
  }
};
document.getElementById('filterNext').onclick = async () => {
  state.filterPage += 1;
  await loadFilterValues();
};

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

init().catch((error) => {
  document.body.innerHTML = `<pre>${escapeHtml(error.stack || error)}</pre>`;
});
"""


REPORT_SERVER_PY = r'''from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import sqlite3
import time
import webbrowser
from http import cookies
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "report.db"
HOST = os.environ.get("REPORT_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", os.environ.get("REPORT_PORT", "8768")))
REPORT_USER = os.environ.get("REPORT_USER", "gpic")
REPORT_PASSWORD = os.environ.get("REPORT_PASSWORD", "1234")
REPORT_SECRET = os.environ.get("REPORT_SECRET", REPORT_PASSWORD or "local-dev-secret")
REPORT_SESSION_MAX_AGE = int(os.environ.get("REPORT_SESSION_MAX_AGE", "43200"))
REPORT_OPEN_BROWSER = os.environ.get("REPORT_OPEN_BROWSER", "1").lower() not in {"0", "false", "no"}
COOKIE_NAME = "gpic_report_session"


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def auth_enabled() -> bool:
    return bool(REPORT_PASSWORD)


def normalize_next_url(value: str) -> str:
    if not value.startswith("/") or value.startswith("//"):
        return "/viewer.html"
    return value


def make_session_token() -> str:
    issued_at = str(int(time.time()))
    nonce = secrets.token_urlsafe(18)
    payload = f"{REPORT_USER}:{issued_at}:{nonce}"
    signature = hmac.new(
        REPORT_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    token = f"{payload}:{signature}".encode("utf-8")
    return base64.urlsafe_b64encode(token).decode("ascii")


def valid_session_token(token: str) -> bool:
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        user, issued_at, nonce, signature = decoded.rsplit(":", 3)
        payload = f"{user}:{issued_at}:{nonce}"
        expected = hmac.new(
            REPORT_SECRET.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False
        if user != REPORT_USER:
            return False
        if int(time.time()) - int(issued_at) > REPORT_SESSION_MAX_AGE:
            return False
        return True
    except Exception:
        return False


def login_html(next_url: str, *, failed: bool = False) -> bytes:
    error = "<p class='error'>Wrong username or password.</p>" if failed else ""
    safe_next = html.escape(next_url, quote=True)
    safe_user = html.escape(REPORT_USER, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GPIC Report Login</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #f6f7f9;
      color: #17202a;
      font: 14px/1.45 system-ui, -apple-system, Segoe UI, sans-serif;
    }}
    main {{
      width: min(420px, calc(100vw - 32px));
      background: #fff;
      border: 1px solid #d8dde6;
      border-radius: 10px;
      padding: 24px;
      box-shadow: 0 12px 32px rgba(23, 32, 42, 0.08);
    }}
    h1 {{ margin: 0 0 6px; font-size: 20px; }}
    p {{ margin: 0 0 18px; color: #627084; }}
    label {{ display: grid; gap: 5px; margin: 12px 0; font-weight: 650; }}
    input {{
      border: 1px solid #d8dde6;
      border-radius: 6px;
      padding: 10px;
      font: inherit;
    }}
    button {{
      width: 100%;
      margin-top: 10px;
      border: 1px solid #2867c7;
      border-radius: 6px;
      background: #2867c7;
      color: #fff;
      padding: 10px;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }}
    .error {{ color: #b42318; }}
  </style>
</head>
<body>
  <main>
    <h1>GPIC Count Report</h1>
    <p>Enter the shared report credentials.</p>
    {error}
    <form method="post" action="/login">
      <input type="hidden" name="next" value="{safe_next}">
      <label>Username
        <input name="username" autocomplete="username" value="{safe_user}">
      </label>
      <label>Password
        <input name="password" type="password" autocomplete="current-password" autofocus>
      </label>
      <button type="submit">Open report</button>
    </form>
  </main>
</body>
</html>
""".encode("utf-8")


class ReportHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        parsed = urlparse(path)
        if parsed.path == "/":
            return str(ROOT / "viewer.html")
        target = (ROOT / parsed.path.lstrip("/")).resolve()
        try:
            target.relative_to(ROOT.resolve())
        except ValueError:
            return str(ROOT / "__forbidden__")
        return str(target)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self.send_json({"ok": True})
            return
        if parsed.path == "/login":
            next_url = normalize_next_url(parse_qs(parsed.query).get("next", ["/viewer.html"])[0])
            self.send_login(next_url)
            return
        if parsed.path == "/logout":
            self.redirect_to_login("/viewer.html", clear_cookie=True)
            return
        if auth_enabled() and not self.is_authenticated():
            if parsed.path.startswith("/api/"):
                self.send_json({"error": "auth_required"}, status=401)
            else:
                self.redirect_to_login(self.path)
            return
        if parsed.path == "/api/views":
            self.send_json({"views": self.views()})
            return
        if parsed.path == "/api/rows":
            self.send_json(self.rows(parse_qs(parsed.query)))
            return
        if parsed.path == "/api/filter-values":
            self.send_json(self.filter_values(parse_qs(parsed.query)))
            return
        if parsed.path == "/api/row-captions":
            self.send_json(self.row_captions(parse_qs(parsed.query)))
            return
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/login":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length).decode("utf-8")
        params = parse_qs(data)
        username = params.get("username", [""])[0]
        password = params.get("password", [""])[0]
        next_url = normalize_next_url(params.get("next", ["/viewer.html"])[0])
        if hmac.compare_digest(username, REPORT_USER) and hmac.compare_digest(password, REPORT_PASSWORD):
            self.send_response(303)
            self.send_header("Location", next_url)
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}={make_session_token()}; Path=/; HttpOnly; SameSite=Lax; Max-Age={REPORT_SESSION_MAX_AGE}",
            )
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self.send_login(next_url, failed=True, status=401)

    def is_authenticated(self) -> bool:
        header = self.headers.get("Cookie", "")
        if not header:
            return False
        jar = cookies.SimpleCookie()
        try:
            jar.load(header)
        except cookies.CookieError:
            return False
        morsel = jar.get(COOKIE_NAME)
        return bool(morsel and valid_session_token(morsel.value))

    def send_login(self, next_url: str, *, failed: bool = False, status: int = 200) -> None:
        data = login_html(next_url, failed=failed)
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def redirect_to_login(self, next_url: str, *, clear_cookie: bool = False) -> None:
        safe_next = quote(normalize_next_url(next_url), safe="")
        self.send_response(303)
        self.send_header("Location", f"/login?next={safe_next}")
        if clear_cookie:
            self.send_header("Set-Cookie", f"{COOKIE_NAME}=; Path=/; Max-Age=0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def views(self) -> list[dict]:
        with sqlite3.connect(DB_PATH) as conn:
            value = conn.execute("SELECT value FROM metadata WHERE key = 'views'").fetchone()[0]
        return json.loads(value)

    def view_meta(self, view_name: str) -> dict:
        for view in self.views():
            if view["name"] == view_name:
                return view
        raise ValueError(f"unknown view: {view_name}")

    def rows(self, params: dict[str, list[str]]) -> dict:
        view = params.get("view", ["objects"])[0]
        meta = self.view_meta(view)
        columns = meta["columns"]
        page = max(1, int(params.get("page", ["1"])[0]))
        page_size = min(200, max(10, int(params.get("page_size", ["20"])[0])))
        sort = params.get("sort", [meta["default_sort"]])[0]
        if sort not in columns:
            sort = meta["default_sort"]
        direction = params.get("dir", [meta["default_dir"]])[0].lower()
        if direction not in {"asc", "desc"}:
            direction = "desc"
        where_sql, values = self.where_clause(params, columns)
        offset = (page - 1) * page_size
        select_columns = ", ".join(["_row_id", *[quote_identifier(column) for column in columns]])
        sql = (
            f"SELECT {select_columns} FROM {quote_identifier(view)} "
            f"{where_sql} ORDER BY {quote_identifier(sort)} {direction.upper()}, rowid ASC "
            "LIMIT ? OFFSET ?"
        )
        count_sql = f"SELECT COUNT(*) FROM {quote_identifier(view)} {where_sql}"
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            total = conn.execute(count_sql, values).fetchone()[0]
            rows = [
                dict(row)
                for row in conn.execute(sql, [*values, page_size, offset]).fetchall()
            ]
        return {"view": view, "columns": columns, "rows": rows, "total": total}

    def row_captions(self, params: dict[str, list[str]]) -> dict:
        view = params.get("view", ["objects"])[0]
        self.view_meta(view)
        row_id = max(1, int(params.get("row_id", ["1"])[0]))
        page = max(1, int(params.get("page", ["1"])[0]))
        page_size = min(200, max(10, int(params.get("page_size", ["50"])[0])))
        with sqlite3.connect(DB_PATH) as conn:
            indexed = self._row_caption_index_exists(conn)
            if indexed:
                indexed_total = conn.execute(
                    "SELECT COUNT(*) FROM report_caption_index "
                    "WHERE view_name = ? AND row_id = ?",
                    [view, row_id],
                ).fetchone()[0]
                if indexed_total:
                    offset = (page - 1) * page_size
                    page_ids = [
                        str(row[0])
                        for row in conn.execute(
                            "SELECT caption_id FROM report_caption_index "
                            "WHERE view_name = ? AND row_id = ? "
                            "ORDER BY caption_id ASC LIMIT ? OFFSET ?",
                            [view, row_id, page_size, offset],
                        ).fetchall()
                    ]
                    if not page_ids:
                        return {"captions": [], "total": indexed_total}
                    return {
                        "captions": self._fetch_captions(conn, page_ids),
                        "total": indexed_total,
                    }

            caption_id_row = conn.execute(
                f"SELECT _caption_ids FROM {quote_identifier(view)} WHERE _row_id = ?",
                [row_id],
            ).fetchone()
            if caption_id_row is None:
                raise ValueError(f"unknown row_id for {view}: {row_id}")
            caption_ids = [item for item in str(caption_id_row[0] or "").split("|") if item]
            total = len(caption_ids)
            offset = (page - 1) * page_size
            page_ids = caption_ids[offset : offset + page_size]
            if not page_ids:
                return {"captions": [], "total": total}
            captions = self._fetch_captions(conn, page_ids)
        return {"captions": captions, "total": total}

    @staticmethod
    def _row_caption_index_exists(conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = "
            "'report_caption_index'",
        ).fetchone()
        return row is not None

    @staticmethod
    def _fetch_captions(conn: sqlite3.Connection, caption_ids: list[str]) -> list[dict]:
        if not caption_ids:
            return []
        placeholders = ", ".join("?" for _ in caption_ids)
        conn.row_factory = sqlite3.Row
        fetched = {
            row["caption_id"]: dict(row)
            for row in conn.execute(
                "SELECT caption_id, caption_type, caption_shape, caption "
                f"FROM captions WHERE caption_id IN ({placeholders})",
                caption_ids,
            ).fetchall()
        }
        return [fetched[caption_id] for caption_id in caption_ids if caption_id in fetched]

    def filter_values(self, params: dict[str, list[str]]) -> dict:
        view = params.get("view", ["objects"])[0]
        meta = self.view_meta(view)
        columns = meta["columns"]
        field = params.get("field", [columns[0]])[0]
        if field not in columns:
            raise ValueError(f"unknown filter field: {field}")
        page = max(1, int(params.get("page", ["1"])[0]))
        page_size = min(100, max(10, int(params.get("page_size", ["50"])[0])))
        query = params.get("q", [""])[0].strip().lower()
        where = [f"{quote_identifier(field)} != ''"]
        values: list[str | int] = []
        if query:
            where.append(f"LOWER({quote_identifier(field)}) LIKE ?")
            values.append(f"%{query}%")
        where_sql = "WHERE " + " AND ".join(where)
        offset = (page - 1) * page_size
        sql = (
            f"SELECT {quote_identifier(field)} AS value, COUNT(*) AS row_count, "
            f"SUM(count) AS total_count FROM {quote_identifier(view)} {where_sql} "
            f"GROUP BY {quote_identifier(field)} "
            "ORDER BY total_count DESC, value ASC LIMIT ? OFFSET ?"
        )
        count_sql = (
            f"SELECT COUNT(*) FROM (SELECT 1 FROM {quote_identifier(view)} {where_sql} "
            f"GROUP BY {quote_identifier(field)})"
        )
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            total = conn.execute(count_sql, values).fetchone()[0]
            rows = [
                dict(row)
                for row in conn.execute(sql, [*values, page_size, offset]).fetchall()
            ]
        return {"values": rows, "total": total}

    def where_clause(self, params: dict[str, list[str]], columns: list[str]) -> tuple[str, list[str]]:
        clauses = []
        values = []
        q = params.get("q", [""])[0].strip().lower()
        if q:
            text_columns = [column for column in columns if column not in {"count", "caption_count"}]
            clauses.append(
                "(" + " OR ".join(f"LOWER({quote_identifier(column)}) LIKE ?" for column in text_columns) + ")"
            )
            values.extend([f"%{q}%"] * len(text_columns))
        for key, value_list in params.items():
            if not key.startswith("filter_"):
                continue
            field = key.removeprefix("filter_")
            if field not in columns:
                continue
            selected_values = [value for value in value_list if value]
            if not selected_values:
                continue
            placeholders = ", ".join("?" for _ in selected_values)
            clauses.append(f"{quote_identifier(field)} IN ({placeholders})")
            values.extend(selected_values)
        if not clauses:
            return "", []
        return "WHERE " + " AND ".join(clauses), values

    def send_json(self, obj: object, *, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    if HOST not in {"127.0.0.1", "localhost", "::1"} and not REPORT_PASSWORD:
        raise SystemExit(
            "REPORT_PASSWORD is required when REPORT_HOST is not localhost. "
            "Set REPORT_PASSWORD before exposing this report."
        )
    server = ThreadingHTTPServer((HOST, PORT), ReportHandler)
    browser_host = "127.0.0.1" if HOST in {"0.0.0.0", "::"} else HOST
    url = f"http://{browser_host}:{PORT}/viewer.html"
    print(f"Serving {ROOT}")
    print(url)
    if auth_enabled():
        print(f"Authentication enabled for user: {REPORT_USER}")
    else:
        print("Authentication disabled; localhost-only use is expected.")
    if REPORT_OPEN_BROWSER and browser_host in {"127.0.0.1", "localhost"}:
        webbrowser.open(url)
    server.serve_forever()
'''


if __name__ == "__main__":
    raise SystemExit(main())
