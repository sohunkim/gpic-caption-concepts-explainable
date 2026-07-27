from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
SRC = ROOT / "src"
for path in (SCRIPT_DIR, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from build_report_caption_index_from_facts import (
    VIEW_KEY_COLUMNS,
    _add_event_role_pair_index,
    _add_simple_fact_index,
    _add_triple_index_rows,
    _flush_pending,
    _load_row_maps,
    _prepare_index_table,
    _role_fact_from_event_role,
    _validate_view_key_schema,
    _write_index_metadata,
    _write_progress,
)
from gpic_concepts_v1.io_jsonl import to_jsonable
from gpic_concepts_v1.stage6_export_counts import (
    _CaptionGroupReader,
    _caption_facts,
    _coerce_canonical_edge,
    _coerce_canonical_mention,
    _iter_caption_groups,
)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Attach a full row-to-caption index to an interactive report DB by "
            "streaming Stage 5 canonical_mentions/canonical_edges directly. "
            "This avoids writing Stage 6 facts.jsonl only to recover caption "
            "drill-down."
        ),
    )
    parser.add_argument("--report-db", required=True, type=Path)
    parser.add_argument(
        "--stage5-dir",
        action="append",
        type=Path,
        default=[],
        help=(
            "Stage 5 directory containing canonical_mentions.jsonl and "
            "canonical_edges.jsonl. May be repeated."
        ),
    )
    parser.add_argument(
        "--stage456-sharded-dir",
        type=Path,
        help=(
            "Directory containing shards/shard_XXXX/stage5 outputs from "
            "run_stage456_sharded.py."
        ),
    )
    parser.add_argument("--progress-json", type=Path)
    parser.add_argument("--progress-every-captions", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=100_000)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--views",
        nargs="*",
        choices=tuple(VIEW_KEY_COLUMNS),
        default=tuple(VIEW_KEY_COLUMNS),
        help="Optional subset of views to index. Defaults to every report view.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    selected_views = set(args.views)
    stage5_dirs = _collect_stage5_dirs(
        explicit_stage5_dirs=args.stage5_dir,
        stage456_sharded_dir=args.stage456_sharded_dir,
    )
    if not args.report_db.exists():
        raise SystemExit(f"missing report DB: {args.report_db}")
    if not stage5_dirs:
        raise SystemExit("no Stage 5 inputs supplied")

    started_at = time.time()
    with sqlite3.connect(args.report_db) as conn:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA temp_store=MEMORY")
        _validate_view_key_schema(conn, selected_views)
        _prepare_index_table(conn, overwrite=args.overwrite)
        row_maps = _load_row_maps(conn, selected_views)
        summary = _stream_stage5_into_index(
            conn,
            stage5_dirs=stage5_dirs,
            row_maps=row_maps,
            selected_views=selected_views,
            progress_json=args.progress_json,
            progress_every_captions=args.progress_every_captions,
            batch_size=args.batch_size,
            started_at=started_at,
        )
        conn.commit()
        summary["index_rows"] = int(
            conn.execute("SELECT COUNT(*) FROM report_caption_index").fetchone()[0],
        )
        summary["view_index_rows"] = {
            str(row[0]): int(row[1])
            for row in conn.execute(
                "SELECT view_name, COUNT(*) FROM report_caption_index "
                "GROUP BY view_name ORDER BY view_name",
            ).fetchall()
        }
        _write_index_metadata(conn, summary)
        conn.commit()

    summary["elapsed_seconds"] = round(time.time() - started_at, 3)
    if args.progress_json:
        _write_progress(args.progress_json, {"phase": "complete", **summary})
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def _collect_stage5_dirs(
    *,
    explicit_stage5_dirs: Iterable[Path],
    stage456_sharded_dir: Path | None,
) -> list[Path]:
    stage5_dirs = list(explicit_stage5_dirs)
    if stage456_sharded_dir is not None:
        shard_root = stage456_sharded_dir / "shards"
        if not shard_root.exists():
            raise SystemExit(f"missing sharded Stage 4/5/6 directory: {shard_root}")
        stage5_dirs.extend(
            sorted(path for path in shard_root.glob("shard_*/stage5") if path.is_dir()),
        )
    unique_dirs = []
    seen: set[Path] = set()
    for stage5_dir in stage5_dirs:
        key = stage5_dir.absolute()
        if key in seen:
            continue
        seen.add(key)
        _require_stage5_files(stage5_dir)
        unique_dirs.append(stage5_dir)
    return unique_dirs


def _require_stage5_files(stage5_dir: Path) -> None:
    mentions_path = stage5_dir / "canonical_mentions.jsonl"
    edges_path = stage5_dir / "canonical_edges.jsonl"
    if not mentions_path.exists():
        raise SystemExit(f"missing canonical mentions: {mentions_path}")
    if not edges_path.exists():
        raise SystemExit(f"missing canonical edges: {edges_path}")


def _stream_stage5_into_index(
    conn: sqlite3.Connection,
    *,
    stage5_dirs: list[Path],
    row_maps: Mapping[str, Mapping[str, int]],
    selected_views: set[str],
    progress_json: Path | None,
    progress_every_captions: int,
    batch_size: int,
    started_at: float,
) -> dict[str, Any]:
    pending: list[tuple[str, int, str]] = []
    caption_groups_processed = 0
    fact_total = 0
    attempted_index_rows = 0
    fact_type_counts: dict[str, int] = defaultdict(int)
    missing_row_keys: dict[str, int] = defaultdict(int)

    def add(view: str, key: str, caption_id: str) -> None:
        nonlocal attempted_index_rows
        if view not in selected_views:
            return
        row_id = row_maps.get(view, {}).get(key)
        if row_id is None:
            missing_row_keys[view] += 1
            return
        pending.append((view, row_id, caption_id))
        attempted_index_rows += 1
        if len(pending) >= max(1, batch_size):
            _flush_pending(conn, pending)

    for stage5_dir in stage5_dirs:
        mentions_path = stage5_dir / "canonical_mentions.jsonl"
        edges_path = stage5_dir / "canonical_edges.jsonl"
        mention_groups = _iter_caption_groups(mentions_path, _coerce_canonical_mention)
        edge_groups = _CaptionGroupReader(edges_path, _coerce_canonical_edge)
        for caption_id, mentions in mention_groups:
            edges = edge_groups.take_if_caption(caption_id)
            roles_by_action: dict[str, dict[str, list[Any]]] = defaultdict(
                lambda: defaultdict(list),
            )
            for fact in _caption_facts(mentions, edges, start_index=fact_total):
                fact_payload = to_jsonable(fact)
                fact_type = str(fact_payload.get("fact_type") or "")
                fact_type_counts[fact_type] += 1
                fact_total += 1
                if fact_type == "event_role":
                    role_fact = _role_fact_from_event_role(fact_payload)
                    if role_fact is not None:
                        roles_by_action[role_fact.action_mention_id][
                            role_fact.role
                        ].append(role_fact)
                    _add_event_role_pair_index(fact_payload, add)
                else:
                    _add_simple_fact_index(fact_payload, add)
            _add_triple_index_rows(roles_by_action, add)

            caption_groups_processed += 1
            if (
                progress_json
                and caption_groups_processed % max(1, progress_every_captions) == 0
            ):
                _flush_pending(conn, pending)
                _write_progress(
                    progress_json,
                    {
                        "phase": "streaming_stage5",
                        "caption_groups_processed": caption_groups_processed,
                        "fact_total": fact_total,
                        "attempted_index_rows": attempted_index_rows,
                        "stage5_dir": str(stage5_dir),
                        "fact_type_counts": dict(sorted(fact_type_counts.items())),
                        "missing_row_keys": dict(sorted(missing_row_keys.items())),
                        "elapsed_seconds": round(time.time() - started_at, 3),
                    },
                )

        leftover_caption = edge_groups.peek_caption()
        if leftover_caption is not None:
            raise ValueError(
                "canonical_edges contains a caption with no matching canonical_mentions "
                "group or the files are not in the same caption order: "
                f"{leftover_caption!r}",
            )

    _flush_pending(conn, pending)
    return {
        "source_mode": "stage5_direct",
        "stage5_dirs": [str(path) for path in stage5_dirs],
        "caption_groups_processed": caption_groups_processed,
        "fact_total": fact_total,
        "attempted_index_rows": attempted_index_rows,
        "fact_type_counts": dict(sorted(fact_type_counts.items())),
        "missing_row_keys": dict(sorted(missing_row_keys.items())),
    }


if __name__ == "__main__":
    raise SystemExit(main())
