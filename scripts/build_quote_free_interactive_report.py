from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any


ROOT = Path(__file__).absolute().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gpic_concepts_v1.atomic_io import atomic_text_writer


QUOTE_CHARS = "\"'“”‘’"


VIEW_QUOTE_FIELDS = {
    "objects": ["canonical_object", "object_raw_surfaces"],
    "attributes": ["canonical_attribute", "attribute_raw_surfaces"],
    "actions": ["canonical_action", "action_raw_surfaces"],
    "relations": [
        "source_object",
        "source_object_raw_surfaces",
        "target_object",
        "target_object_raw_surfaces",
    ],
    "object_cooccurrence": [
        "source_object",
        "source_object_raw_surfaces",
        "target_object",
        "target_object_raw_surfaces",
    ],
    "attribute_object_pairs": [
        "object",
        "object_raw_surfaces",
        "attribute",
        "attribute_raw_surfaces",
    ],
    "patient_action_pairs": [
        "patient_object",
        "patient_object_raw_surfaces",
        "action",
        "action_raw_surfaces",
    ],
    "agent_action_pairs": [
        "agent_object",
        "agent_object_raw_surfaces",
        "action",
        "action_raw_surfaces",
    ],
    "patient_action_agent_triples": [
        "patient_object",
        "patient_object_raw_surfaces",
        "action",
        "action_raw_surfaces",
        "agent_object",
        "agent_object_raw_surfaces",
    ],
    # relation_components intentionally excluded: its component values are
    # relation tokens, not extracted quoted entities/attributes.
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a conservative quote-noise-filtered copy of an interactive "
            "GPIC report by using quoted caption strings as row-removal evidence."
        )
    )
    parser.add_argument("--source-report-dir", required=True, type=Path)
    parser.add_argument("--stage3-records", type=Path)
    parser.add_argument(
        "--quote-source",
        choices=("report-captions", "stage3"),
        default="report-captions",
        help=(
            "report-captions extracts paired quote strings from the report DB "
            "captions table and is the default fast path. stage3 uses protected "
            "quote span records and is slower for 1M runs."
        ),
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--summary-json", required=True, type=Path)
    parser.add_argument("--quote-terms-json", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_dir = args.source_report_dir
    source_db = source_dir / "report.db"
    if not source_db.exists():
        raise SystemExit(f"missing source report DB: {source_db}")
    if args.quote_source == "stage3" and (
        args.stage3_records is None or not args.stage3_records.exists()
    ):
        raise SystemExit(f"missing Stage 3 records: {args.stage3_records}")

    raw_quote_terms_by_caption: dict[str, set[str]] | None = None
    quote_terms_by_caption: dict[str, set[str]] | None = None
    quote_records_with_quotes = 0
    quote_span_count = 0
    if args.quote_source == "report-captions":
        (
            raw_quote_terms,
            raw_quote_terms_by_caption,
            quote_records_with_quotes,
            quote_span_count,
        ) = load_or_extract_report_caption_quote_terms(source_db, args.quote_terms_json)
    else:
        raw_quote_terms = load_or_extract_quote_terms(
            source_db=source_db,
            stage3_records=args.stage3_records,
            cache_path=args.quote_terms_json,
            quote_source=args.quote_source,
        )
    quote_terms = filter_structural_quote_terms(raw_quote_terms)
    if raw_quote_terms_by_caption is not None:
        quote_terms_by_caption = {
            caption_id: filter_structural_quote_terms(terms)
            for caption_id, terms in raw_quote_terms_by_caption.items()
        }
        quote_terms_by_caption = {
            caption_id: terms
            for caption_id, terms in quote_terms_by_caption.items()
            if terms
        }
    quote_caption_counts = count_quote_term_captions(quote_terms_by_caption)
    if not quote_terms:
        raise SystemExit(
            "no structurally quote-like terms found; refusing to create a misleading quote-free report"
        )

    if args.dry_run:
        db_path = source_db
        output_dir = None
    else:
        output_dir = args.output_dir
        if output_dir.exists():
            if not args.overwrite:
                raise SystemExit(f"output directory already exists: {output_dir}")
            shutil.rmtree(output_dir)
        copy_report_dir(source_dir, output_dir)
        db_path = output_dir / "report.db"

    summary = filter_report_db(
        db_path,
        quote_terms=quote_terms,
        raw_quote_terms=raw_quote_terms,
        quote_terms_by_caption=quote_terms_by_caption,
        raw_quote_terms_by_caption=raw_quote_terms_by_caption,
        quote_caption_counts=quote_caption_counts,
        raw_quote_caption_counts=count_quote_term_captions(raw_quote_terms_by_caption),
        dry_run=args.dry_run,
    )
    summary.update(
        {
            "source_report_dir": str(source_dir),
            "output_dir": "" if output_dir is None else str(output_dir),
            "stage3_records": "" if args.stage3_records is None else str(args.stage3_records),
            "quote_source": args.quote_source,
            "raw_quote_term_count": len(raw_quote_terms),
            "quote_term_count": len(quote_terms),
            "quote_records_with_quotes": quote_records_with_quotes,
            "quote_span_count": quote_span_count,
            "quote_filter_mode": (
                "structural_quote_terms_quote_only_rows"
                if quote_terms_by_caption is not None
                else "structural_quote_terms_only"
            ),
            "dry_run": args.dry_run,
        }
    )

    if not args.dry_run and output_dir is not None:
        update_summary_json(output_dir, summary)
        stamp_title(db_path, output_dir)

    write_json_atomic(args.summary_json, summary)
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 0


def load_or_extract_quote_terms(
    *,
    source_db: Path,
    stage3_records: Path | None,
    cache_path: Path | None,
    quote_source: str,
) -> set[str]:
    if cache_path is not None and cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if payload.get("quote_source") == quote_source:
            return {term for term in payload.get("normalized_quote_terms", []) if term}

    if quote_source == "report-captions":
        terms, records_with_quotes, span_count = extract_quote_terms_from_report_captions(
            source_db,
        )
    elif quote_source == "stage3":
        if stage3_records is None:
            raise ValueError("stage3_records is required when quote_source='stage3'")
        terms, records_with_quotes, span_count = extract_quote_terms_from_stage3(
            stage3_records,
        )
    else:
        raise ValueError(f"unknown quote_source: {quote_source}")

    if cache_path is not None:
        cache_payload = {
            "quote_source": quote_source,
            "source_db": str(source_db),
            "stage3_records": "" if stage3_records is None else str(stage3_records),
            "records_with_quotes": records_with_quotes,
            "quote_span_count": span_count,
            "normalized_quote_terms": sorted(terms),
        }
        write_json_atomic(cache_path, cache_payload)
    return terms


def load_or_extract_report_caption_quote_terms(
    source_db: Path,
    cache_path: Path | None,
) -> tuple[set[str], dict[str, set[str]], int, int]:
    quote_terms_by_caption, records_with_quotes, span_count = (
        extract_quote_terms_by_caption_from_report_captions(source_db)
    )
    terms: set[str] = set()
    for caption_terms in quote_terms_by_caption.values():
        terms.update(caption_terms)
    if cache_path is not None:
        cache_payload = {
            "quote_source": "report-captions",
            "source_db": str(source_db),
            "records_with_quotes": records_with_quotes,
            "quote_span_count": span_count,
            "normalized_quote_terms": sorted(terms),
        }
        write_json_atomic(cache_path, cache_payload)
    return terms, quote_terms_by_caption, records_with_quotes, span_count


def extract_quote_terms_from_stage3(stage3_records: Path) -> tuple[set[str], int, int]:
    terms: set[str] = set()
    records_with_quotes = 0
    span_count = 0
    with stage3_records.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            record_had_quote = False
            for span in record.get("protected_spans", []):
                if span.get("kind") != "quote":
                    continue
                span_count += 1
                record_had_quote = True
                text = str(span.get("text", ""))
                for variant in quote_variants(text):
                    normalized = normalize_term(variant)
                    if normalized:
                        terms.add(normalized)
            if record_had_quote:
                records_with_quotes += 1
    return terms, records_with_quotes, span_count


def extract_quote_terms_by_caption_from_report_captions(
    source_db: Path,
) -> tuple[dict[str, set[str]], int, int]:
    quote_terms_by_caption: dict[str, set[str]] = {}
    records_with_quotes = 0
    span_count = 0
    conn = sqlite3.connect(source_db)
    try:
        for caption_id, caption in conn.execute("SELECT caption_id, caption FROM captions"):
            text = str(caption or "")
            spans = find_quote_strings(text)
            if not spans:
                continue
            terms: set[str] = set()
            for span in spans:
                for variant in quote_variants(span):
                    normalized = normalize_term(variant)
                    if normalized:
                        terms.add(normalized)
            if not terms:
                continue
            quote_terms_by_caption[str(caption_id)] = terms
            records_with_quotes += 1
            span_count += len(spans)
    finally:
        conn.close()
    return quote_terms_by_caption, records_with_quotes, span_count


def extract_quote_terms_from_report_captions(source_db: Path) -> tuple[set[str], int, int]:
    terms: set[str] = set()
    records_with_quotes = 0
    span_count = 0
    conn = sqlite3.connect(source_db)
    try:
        for (caption,) in conn.execute("SELECT caption FROM captions"):
            text = str(caption or "")
            spans = find_quote_strings(text)
            if not spans:
                continue
            records_with_quotes += 1
            span_count += len(spans)
            for span in spans:
                for variant in quote_variants(span):
                    normalized = normalize_term(variant)
                    if normalized:
                        terms.add(normalized)
    finally:
        conn.close()
    return terms, records_with_quotes, span_count


def find_quote_strings(text: str) -> list[str]:
    spans: list[str] = []
    spans.extend(match.group(0) for match in re.finditer(r'"[^"]+"', text))
    spans.extend(match.group(0) for match in re.finditer(r"“[^”]+”", text))
    return spans


def quote_variants(text: str) -> Iterable[str]:
    stripped = text.strip()
    if stripped:
        yield stripped
    inner = stripped.strip(QUOTE_CHARS).strip()
    if inner and inner != stripped:
        yield inner
    # Captions often preserve punctuation immediately inside a quoted title.
    inner_no_edge_punct = inner.strip(" .,:;!?()[]{}").strip()
    if inner_no_edge_punct and inner_no_edge_punct not in {inner, stripped}:
        yield inner_no_edge_punct


def normalize_term(value: str) -> str:
    text = value.strip().lower()
    text = text.strip(QUOTE_CHARS).strip()
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    if any(char.isalnum() for char in text):
        # Keep meaningful quote/code punctuation such as Yahoo!, C++, C#,
        # while still dropping trailing sentence punctuation.
        text = text.strip(" .,:;?()[]{}")
    else:
        text = text.strip()
    return text


def filter_structural_quote_terms(terms: set[str]) -> set[str]:
    return {term for term in terms if is_structural_quote_term(term)}


def count_quote_term_captions(
    quote_terms_by_caption: dict[str, set[str]] | None,
) -> dict[str, int]:
    if quote_terms_by_caption is None:
        return {}
    counts: Counter[str] = Counter()
    for terms in quote_terms_by_caption.values():
        counts.update(terms)
    return dict(counts)


def is_structural_quote_term(term: str) -> bool:
    text = term.strip()
    if not text:
        return False
    if len(text) == 1 and not text.isalnum():
        return True
    if any(char.isdigit() for char in text):
        return True
    if len(text.split()) >= 2:
        return True
    if re.search(r"[^a-z0-9 '\-]", text):
        return True
    if re.search(r"[a-z]\d|\d[a-z]", text):
        return True
    return False


def split_cell_terms(value: Any) -> set[str]:
    text = str(value or "")
    parts = [text]
    if "|" in text:
        parts.extend(part for part in text.split("|") if part)
    return {normalized for part in parts if (normalized := normalize_term(part))}


def copy_report_dir(source_dir: Path, output_dir: Path) -> None:
    def ignore_tmp(_: str, names: list[str]) -> set[str]:
        return {name for name in names if name.endswith(".tmp")}

    shutil.copytree(source_dir, output_dir, ignore=ignore_tmp)


def filter_report_db(
    db_path: Path,
    *,
    quote_terms: set[str],
    raw_quote_terms: set[str],
    quote_terms_by_caption: dict[str, set[str]] | None,
    raw_quote_terms_by_caption: dict[str, set[str]] | None,
    quote_caption_counts: dict[str, int],
    raw_quote_caption_counts: dict[str, int],
    dry_run: bool,
) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        views = load_views(conn)
        removed_by_view: dict[str, int] = {}
        leading_quote_removed_by_view: dict[str, int] = {}
        caption_index_removed_by_view: dict[str, int] = {}
        before_counts = {view["name"]: int(view["row_count"]) for view in views}
        after_counts: dict[str, int] = {}
        sample_removed: dict[str, list[dict[str, Any]]] = {}

        for view in views:
            view_name = view["name"]
            fields = [field for field in VIEW_QUOTE_FIELDS.get(view_name, []) if field in view["columns"]]
            if not fields:
                after_counts[view_name] = before_counts[view_name]
                removed_by_view[view_name] = 0
                leading_quote_removed_by_view[view_name] = 0
                caption_index_removed_by_view[view_name] = 0
                continue
            quote_row_ids, quote_samples = find_quote_row_ids(
                conn,
                view_name,
                fields,
                quote_terms,
                raw_quote_terms=raw_quote_terms,
                quote_terms_by_caption=quote_terms_by_caption,
                raw_quote_terms_by_caption=raw_quote_terms_by_caption,
                quote_caption_counts=quote_caption_counts,
                raw_quote_caption_counts=raw_quote_caption_counts,
            )
            leading_row_ids, leading_samples = find_leading_quoted_label_row_ids(
                conn,
                view_name,
                fields,
            )
            row_ids = sorted(set(quote_row_ids) | set(leading_row_ids))
            removed_by_view[view_name] = len(row_ids)
            leading_quote_removed_by_view[view_name] = len(set(leading_row_ids) - set(quote_row_ids))
            sample_removed[view_name] = (quote_samples + leading_samples)[:10]
            if row_ids and not dry_run:
                caption_index_removed_by_view[view_name] = delete_caption_index_rows(
                    conn,
                    view_name,
                    row_ids,
                )
                delete_rows(conn, view_name, row_ids)
            else:
                caption_index_removed_by_view[view_name] = count_caption_index_rows(
                    conn,
                    view_name,
                    row_ids,
                )
            after_counts[view_name] = before_counts[view_name] - len(row_ids)

        if not dry_run:
            update_views_metadata(conn, views, after_counts)
            conn.commit()

        return {
            "before_counts": before_counts,
            "after_counts": after_counts,
            "removed_rows_by_view": removed_by_view,
            "leading_quote_removed_rows_by_view": leading_quote_removed_by_view,
            "caption_index_rows_removed_by_view": caption_index_removed_by_view,
            "sample_removed_rows": sample_removed,
        }
    finally:
        conn.close()


def load_views(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    row = conn.execute("SELECT value FROM metadata WHERE key = 'views'").fetchone()
    if row is None:
        raise RuntimeError("metadata.views is missing")
    return json.loads(row[0])


def find_quote_row_ids(
    conn: sqlite3.Connection,
    view_name: str,
    fields: list[str],
    quote_terms: set[str],
    *,
    raw_quote_terms: set[str],
    quote_terms_by_caption: dict[str, set[str]] | None,
    raw_quote_terms_by_caption: dict[str, set[str]] | None,
    quote_caption_counts: dict[str, int],
    raw_quote_caption_counts: dict[str, int],
) -> tuple[list[int], list[dict[str, Any]]]:
    table_columns = get_table_columns(conn, view_name)
    selected_columns = ["_row_id", *fields]
    if "caption_count" in table_columns:
        selected_columns.append("caption_count")
    quoted_fields = ", ".join(quote_identifier(field) for field in selected_columns)
    row_ids: list[int] = []
    samples: list[dict[str, Any]] = []
    for row in conn.execute(f"SELECT {quoted_fields} FROM {quote_identifier(view_name)}"):
        row_terms_by_field = {field: split_cell_terms(row[field]) for field in fields}
        row_terms: set[str] = set()
        for terms in row_terms_by_field.values():
            row_terms.update(terms)
        matched_terms = row_terms & quote_terms
        row_has_literal_quote = any(
            contains_quote_char(str(row[field] or "")) for field in fields
        )
        row_quote_terms_by_caption = quote_terms_by_caption
        row_quote_caption_counts = quote_caption_counts
        if row_has_literal_quote:
            literal_matched_terms = row_terms & raw_quote_terms
            if literal_matched_terms:
                matched_terms = matched_terms | literal_matched_terms
                row_quote_terms_by_caption = raw_quote_terms_by_caption
                row_quote_caption_counts = raw_quote_caption_counts
        if not matched_terms:
            continue
        matched = [
            field
            for field, terms in row_terms_by_field.items()
            if terms & matched_terms
        ]
        if matched:
            row_id = int(row["_row_id"])
            if row_quote_terms_by_caption is not None:
                row_caption_count = int(row["caption_count"] or 0) if "caption_count" in row.keys() else 0
                max_quote_caption_count = max(
                    (row_quote_caption_counts.get(term, 0) for term in matched_terms),
                    default=0,
                )
                if row_caption_count and row_caption_count > max_quote_caption_count:
                    continue
                if not row_is_quote_only(
                    conn,
                    view_name,
                    row_id,
                    row_terms,
                    row_quote_terms_by_caption,
                ):
                    continue
            row_ids.append(row_id)
            if len(samples) < 10:
                samples.append(
                    {
                        "_row_id": row_id,
                        "matched_fields": matched,
                        "matched_terms": sorted(matched_terms)[:20],
                        **{field: row[field] for field in fields},
                    }
                )
    return row_ids, samples


def contains_quote_char(value: str) -> bool:
    return any(char in value for char in QUOTE_CHARS)


def starts_with_quote_char(value: str) -> bool:
    return value.startswith(tuple(QUOTE_CHARS))


def find_leading_quoted_label_row_ids(
    conn: sqlite3.Connection,
    view_name: str,
    fields: list[str],
) -> tuple[list[int], list[dict[str, Any]]]:
    selected_columns = ["_row_id", *fields]
    quoted_fields = ", ".join(quote_identifier(field) for field in selected_columns)
    row_ids: list[int] = []
    samples: list[dict[str, Any]] = []
    for row in conn.execute(f"SELECT {quoted_fields} FROM {quote_identifier(view_name)}"):
        matched = [
            field
            for field in fields
            if starts_with_quote_char(str(row[field] or ""))
        ]
        if not matched:
            continue
        row_id = int(row["_row_id"])
        row_ids.append(row_id)
        if len(samples) < 10:
            samples.append(
                {
                    "_row_id": row_id,
                    "matched_fields": matched,
                    "matched_terms": ["leading_quote_label"],
                    **{field: row[field] for field in fields},
                }
            )
    return row_ids, samples


def row_is_quote_only(
    conn: sqlite3.Connection,
    view_name: str,
    row_id: int,
    row_terms: set[str],
    quote_terms_by_caption: dict[str, set[str]],
) -> bool:
    caption_rows = conn.execute(
        "SELECT caption_id FROM report_caption_index WHERE view_name = ? AND row_id = ?",
        [view_name, row_id],
    ).fetchall()
    if not caption_rows:
        return False
    for (caption_id,) in caption_rows:
        if not (row_terms & quote_terms_by_caption.get(str(caption_id), set())):
            return False
    return True


def get_table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({quote_identifier(table_name)})")}


def count_caption_index_rows(
    conn: sqlite3.Connection,
    view_name: str,
    row_ids: list[int],
) -> int:
    if not row_ids or not report_caption_index_exists(conn):
        return 0
    total = 0
    for chunk in chunks(row_ids, 900):
        placeholders = ", ".join("?" for _ in chunk)
        total += int(
            conn.execute(
                "SELECT COUNT(*) FROM report_caption_index "
                f"WHERE view_name = ? AND row_id IN ({placeholders})",
                [view_name, *chunk],
            ).fetchone()[0]
        )
    return total


def delete_caption_index_rows(
    conn: sqlite3.Connection,
    view_name: str,
    row_ids: list[int],
) -> int:
    if not row_ids or not report_caption_index_exists(conn):
        return 0
    before = count_caption_index_rows(conn, view_name, row_ids)
    for chunk in chunks(row_ids, 900):
        placeholders = ", ".join("?" for _ in chunk)
        conn.execute(
            "DELETE FROM report_caption_index "
            f"WHERE view_name = ? AND row_id IN ({placeholders})",
            [view_name, *chunk],
        )
    return before


def delete_rows(conn: sqlite3.Connection, view_name: str, row_ids: list[int]) -> None:
    for chunk in chunks(row_ids, 900):
        placeholders = ", ".join("?" for _ in chunk)
        conn.execute(
            f"DELETE FROM {quote_identifier(view_name)} WHERE _row_id IN ({placeholders})",
            chunk,
        )


def report_caption_index_exists(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'report_caption_index'",
        ).fetchone()
        is not None
    )


def update_views_metadata(
    conn: sqlite3.Connection,
    views: list[dict[str, Any]],
    after_counts: dict[str, int],
) -> None:
    updated = []
    for view in views:
        copy = dict(view)
        copy["row_count"] = after_counts[view["name"]]
        updated.append(copy)
    conn.execute(
        "UPDATE metadata SET value = ? WHERE key = 'views'",
        [json.dumps(updated, ensure_ascii=False)],
    )


def update_summary_json(output_dir: Path, filter_summary: dict[str, Any]) -> None:
    summary_path = output_dir / "summary.json"
    if not summary_path.exists():
        return
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["title"] = str(summary.get("title", "GPIC Count Report")) + " (Quote-Free)"
    summary["quote_free_filter"] = {
        "quote_term_count": filter_summary["quote_term_count"],
        "removed_rows_by_view": filter_summary["removed_rows_by_view"],
        "leading_quote_removed_rows_by_view": filter_summary[
            "leading_quote_removed_rows_by_view"
        ],
        "caption_index_rows_removed_by_view": filter_summary[
            "caption_index_rows_removed_by_view"
        ],
    }
    summary["view_row_counts"] = filter_summary["after_counts"]
    write_json_atomic(summary_path, summary)


def stamp_title(db_path: Path, output_dir: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT value FROM metadata WHERE key = 'title'").fetchone()
        title = "GPIC Count Report"
        if row is not None:
            title = str(row[0])
        if "Quote-Free" not in title:
            title = f"{title} (Quote-Free)"
        conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('title', ?)",
            [title],
        )
        conn.commit()
    viewer_path = output_dir / "viewer.html"
    if viewer_path.exists():
        text = viewer_path.read_text(encoding="utf-8")
        text = text.replace("GPIC 1M Count Report", "GPIC 1M Count Report (Quote-Free)")
        with atomic_text_writer(viewer_path) as handle:
            handle.write(text)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    with atomic_text_writer(path, newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")


def chunks(values: list[int], size: int) -> Iterable[list[int]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


if __name__ == "__main__":
    raise SystemExit(main())
