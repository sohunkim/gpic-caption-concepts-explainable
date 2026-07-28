from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path
from typing import Any


DIFFERENCE_BANDS = (
    "0~5%",
    "5~10%",
    "10~20%",
    "20~30%",
    "30~50%",
    "50%+",
    "N/A",
)

NUMERIC_ATTRIBUTE_ALIASES = {
    "0": "zero",
    "1": "one",
    "2": "two",
    "3": "three",
    "4": "four",
    "5": "five",
    "6": "six",
    "7": "seven",
    "8": "eight",
    "9": "nine",
    "10": "ten",
    "11": "eleven",
    "12": "twelve",
    "13": "thirteen",
    "14": "fourteen",
    "15": "fifteen",
    "16": "sixteen",
    "17": "seventeen",
    "18": "eighteen",
    "19": "nineteen",
    "20": "twenty",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build Canary170 caption-count tables with T5 exact canonical-name "
            "counts and lexicon exact raw-surface overlap counts."
        ),
    )
    parser.add_argument("--display-terms-json", required=True, type=Path)
    parser.add_argument("--t5-canonical-counts-json", required=True, type=Path)
    parser.add_argument("--t5-multi-canonical-union-json", required=True, type=Path)
    parser.add_argument("--legacy-quantity-caption-ids-json", type=Path)
    parser.add_argument("--report-db", required=True, type=Path)
    parser.add_argument("--output-md", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    display = _load_json(args.display_terms_json)
    t5 = _load_json(args.t5_canonical_counts_json)
    t5_multi = _load_json(args.t5_multi_canonical_union_json)
    legacy_quantity = (
        _load_json(args.legacy_quantity_caption_ids_json)
        if args.legacy_quantity_caption_ids_json
        else {}
    )
    result = build_comparison(
        display=display,
        t5=t5,
        t5_multi=t5_multi,
        legacy_quantity=legacy_quantity,
        report_db=args.report_db,
    )
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(_render_markdown(result, args), encoding="utf-8")
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output_md": str(args.output_md),
                "output_json": str(args.output_json),
                "entities": len(result["entities"]),
                "attributes": len(result["attributes"]),
                "pairs": len(result["pairs"]),
            },
            ensure_ascii=False,
        ),
    )
    return 0


def build_comparison(
    *,
    display: dict[str, Any],
    t5: dict[str, Any],
    t5_multi: dict[str, Any],
    legacy_quantity: dict[str, Any] | None = None,
    report_db: Path,
) -> dict[str, Any]:
    legacy_quantity = legacy_quantity or {}
    _validate_t5_canonical_count_contract(t5)
    attribute_names = {
        _exact_key(item["surface"]): tuple(
            sorted({_exact_key(value) for value in item.get("internal_names", []) if value}),
        )
        for item in display.get("attribute_records", [])
    }
    if any(not names for names in attribute_names.values()):
        missing = sorted(surface for surface, names in attribute_names.items() if not names)
        raise ValueError(f"8756 attributes are missing explicit canonical names: {missing}")
    pair_attribute_surfaces = {
        _exact_key(item["attribute"])
        for item in display.get("pairs", [])
        if item.get("attribute")
    }
    missing_pair_attributes = sorted(pair_attribute_surfaces - set(attribute_names))
    if missing_pair_attributes:
        raise ValueError(
            "8756 display terms JSON is missing attribute_records for pair "
            "attributes. Use the exact-surface terms artifact, not the "
            "display-surface-only artifact. Missing attributes: "
            f"{missing_pair_attributes}"
        )

    t5_attribute_counts = t5.get("attribute_caption_counts", {})
    t5_pair_counts = t5.get("entity_attribute_pair_caption_counts", {})
    t5_multi_attribute_counts = t5_multi.get("attribute_caption_counts", {})
    t5_multi_pair_counts = t5_multi.get("entity_attribute_pair_caption_counts", {})

    conn = sqlite3.connect(f"file:{report_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    entity_rows = []
    for entity in display.get("entities", []):
        key = _exact_key(entity)
        t5_count = int(t5.get("entity_caption_counts", {}).get(key, 0))
        lexicon_count = _lexicon_entity_caption_count(conn, key)
        difference_percent = _relative_difference_percent(
            lexicon_count,
            t5_count,
        )
        entity_rows.append(
            {
                "entity": key,
                "t5_caption_count": t5_count,
                "lexicon_caption_count": lexicon_count,
                "lexicon_minus_t5_percent": difference_percent,
                "difference_band": _difference_band(difference_percent),
            },
        )
    attribute_rows = []
    for surface, names in attribute_names.items():
        aliases = _lexicon_aliases_for_display_attribute(surface, names)
        t5_count = _canonical_attribute_count(
            surface,
            names,
            t5_attribute_counts,
            t5_multi_attribute_counts,
        )
        lexicon_match = _lexicon_attribute_match(
            conn,
            aliases=aliases,
            extra_caption_ids=_legacy_attribute_caption_ids(
                legacy_quantity,
                aliases=aliases,
            ),
        )
        difference_percent = _relative_difference_percent(
            lexicon_match["caption_count"],
            t5_count,
        )
        attribute_rows.append(
            {
                "surface": surface,
                "t5_canonical_names": list(names),
                "t5_caption_count": t5_count,
                "lexicon_matched_raw_surfaces": lexicon_match["raw_surfaces"],
                "lexicon_canonical_attributes": lexicon_match["canonical_attributes"],
                "lexicon_caption_count": lexicon_match["caption_count"],
                "lexicon_minus_t5_percent": difference_percent,
                "difference_band": _difference_band(difference_percent),
            },
        )

    pair_rows = []
    for item in display.get("pairs", []):
        entity = _exact_key(item["entity"])
        surface = _exact_key(item["attribute"])
        names = attribute_names[surface]
        aliases = _lexicon_aliases_for_display_attribute(surface, names)
        t5_count = _canonical_pair_count(
            entity,
            surface,
            names,
            t5_pair_counts,
            t5_multi_pair_counts,
        )
        lexicon_match = _lexicon_pair_match(
            conn,
            entity=entity,
            attribute_aliases=aliases,
            extra_caption_ids=_legacy_pair_caption_ids(
                legacy_quantity,
                entity=entity,
                aliases=aliases,
            ),
        )
        difference_percent = _relative_difference_percent(
            lexicon_match["caption_count"],
            t5_count,
        )
        pair_rows.append(
            {
                "entity": entity,
                "surface": surface,
                "t5_canonical_names": list(names),
                "t5_caption_count": t5_count,
                "lexicon_matched_raw_surfaces": lexicon_match["raw_surfaces"],
                "lexicon_canonical_attributes": lexicon_match["canonical_attributes"],
                "lexicon_caption_count": lexicon_match["caption_count"],
                "lexicon_minus_t5_percent": difference_percent,
                "difference_band": _difference_band(difference_percent),
            },
        )

    conn.close()

    return {
        "count_basis": "unique_caption_id",
        "t5_policy": "exact_8756_internal_canonical_name",
        "lexicon_policy": (
            "select_canonical_rows_by_exact_raw_surface_overlap_with_8756_surface_or_canonical_name"
        ),
        "generated_variants": False,
        "numeric_quantity_aliases": NUMERIC_ATTRIBUTE_ALIASES,
        "legacy_quantity_caption_ids_merged": bool(legacy_quantity),
        "difference_band_summary": {
            "entities": _difference_band_counts(entity_rows),
            "attributes": _difference_band_counts(attribute_rows),
            "pairs": _difference_band_counts(pair_rows),
        },
        "statistics": {
            "entities": _aggregate_statistics(entity_rows),
            "attributes": _aggregate_statistics(attribute_rows),
            "pairs": _aggregate_statistics(pair_rows),
        },
        "entities": sorted(
            entity_rows,
            key=lambda row: (-row["t5_caption_count"], row["entity"]),
        ),
        "attributes": sorted(
            attribute_rows,
            key=lambda row: (-row["t5_caption_count"], row["surface"]),
        ),
        "pairs": sorted(
            pair_rows,
            key=lambda row: (
                -row["t5_caption_count"],
                row["entity"],
                row["surface"],
            ),
        ),
    }


def _validate_t5_canonical_count_contract(t5: dict[str, Any]) -> None:
    policy = str(t5.get("attribute_label_policy", "")).strip().lower()
    key_space = str(t5.get("attribute_count_key_space", "")).strip().lower()
    if "display surface" in policy or "remapped" in policy:
        raise ValueError(
            "T5 canonical counts must be keyed by the T5 canonical label, but "
            f"the supplied file declares attribute_label_policy={policy!r}. "
            "Use the global canonical-count artifact instead of a display-surface "
            "remap."
        )
    if key_space and key_space != "canonical_t5_label":
        raise ValueError(
            "T5 canonical counts declare an incompatible key space: "
            f"{key_space!r}; expected 'canonical_t5_label'."
        )


def _relative_difference_percent(
    lexicon_count: int,
    t5_count: int,
) -> float | None:
    if t5_count == 0:
        return None
    return (lexicon_count - t5_count) * 100.0 / t5_count


def _difference_band(value: float | None) -> str:
    if value is None:
        return "N/A"
    absolute = abs(value)
    if absolute < 5.0:
        return "0~5%"
    if absolute < 10.0:
        return "5~10%"
    if absolute < 20.0:
        return "10~20%"
    if absolute < 30.0:
        return "20~30%"
    if absolute < 50.0:
        return "30~50%"
    return "50%+"


def _difference_band_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {band: 0 for band in DIFFERENCE_BANDS}
    for row in rows:
        counts[row["difference_band"]] += 1
    return counts


def _aggregate_statistics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    t5_values = [int(row["t5_caption_count"]) for row in rows]
    lexicon_values = [int(row["lexicon_caption_count"]) for row in rows]
    errors = [lexicon - t5 for t5, lexicon in zip(t5_values, lexicon_values)]
    abs_errors = [abs(error) for error in errors]
    percentage_errors = [
        abs(error) / t5 * 100.0
        for t5, error in zip(t5_values, errors)
        if t5 > 0
    ]
    t5_total = sum(t5_values)
    lexicon_total = sum(lexicon_values)
    pearson_r = _pearson_correlation(t5_values, lexicon_values)
    return {
        "rows": len(rows),
        "t5_total": t5_total,
        "lexicon_total": lexicon_total,
        "total_diff": lexicon_total - t5_total,
        "total_diff_percent": _relative_difference_percent(
            lexicon_total,
            t5_total,
        ),
        "pearson_r": pearson_r,
        "r_squared": None if pearson_r is None else pearson_r * pearson_r,
        "mean_absolute_error": (
            None if not abs_errors else sum(abs_errors) / len(abs_errors)
        ),
        "mean_absolute_percentage_error": (
            None
            if not percentage_errors
            else sum(percentage_errors) / len(percentage_errors)
        ),
    }


def _pearson_correlation(
    left_values: list[int],
    right_values: list[int],
) -> float | None:
    if len(left_values) < 2 or len(left_values) != len(right_values):
        return None
    left_mean = sum(left_values) / len(left_values)
    right_mean = sum(right_values) / len(right_values)
    left_deltas = [value - left_mean for value in left_values]
    right_deltas = [value - right_mean for value in right_values]
    left_ss = sum(value * value for value in left_deltas)
    right_ss = sum(value * value for value in right_deltas)
    if left_ss == 0.0 or right_ss == 0.0:
        return None
    covariance = sum(
        left * right
        for left, right in zip(left_deltas, right_deltas)
    )
    return covariance / math.sqrt(left_ss * right_ss)


def _canonical_attribute_count(
    surface: str,
    names: tuple[str, ...],
    exact_counts: dict[str, Any],
    multi_union_counts: dict[str, Any],
) -> int:
    if len(names) == 1:
        return int(exact_counts.get(names[0], 0))
    if surface not in multi_union_counts:
        raise ValueError(
            "Multiple T5 canonical names require a caption-ID union override: "
            f"{surface} -> {names}",
        )
    return int(multi_union_counts[surface])


def _canonical_pair_count(
    entity: str,
    surface: str,
    names: tuple[str, ...],
    exact_counts: dict[str, Any],
    multi_union_counts: dict[str, Any],
) -> int:
    if len(names) == 1:
        return int(exact_counts.get(f"{entity}\t{names[0]}", 0))
    key = f"{entity}\t{surface}"
    if key not in multi_union_counts:
        raise ValueError(
            "Multiple T5 canonical names require a pair caption-ID union override: "
            f"{key} -> {names}",
        )
    return int(multi_union_counts[key])


def _lexicon_aliases_for_display_attribute(
    surface: str,
    names: tuple[str, ...],
) -> set[str]:
    aliases = {_exact_key(surface), *(_exact_key(name) for name in names)}
    aliases.discard("")
    for value in list(aliases):
        if value in NUMERIC_ATTRIBUTE_ALIASES:
            aliases.add(NUMERIC_ATTRIBUTE_ALIASES[value])
    return aliases


def _lexicon_entity_caption_count(conn: sqlite3.Connection, entity: str) -> int:
    row_ids = {
        int(row["_row_id"])
        for row in conn.execute(
            "SELECT _row_id FROM objects WHERE lower(trim(canonical_object)) = ?",
            (entity,),
        )
    }
    return _caption_count_for_rows(conn, "objects", row_ids)


def _lexicon_attribute_match(
    conn: sqlite3.Connection,
    *,
    aliases: set[str],
    extra_caption_ids: set[str] | None = None,
) -> dict[str, Any]:
    return _lexicon_raw_overlap_match(
        conn,
        view="attributes",
        label_column="canonical_attribute",
        raw_column="attribute_raw_surfaces",
        aliases=aliases,
        extra_caption_ids=extra_caption_ids,
    )


def _lexicon_pair_match(
    conn: sqlite3.Connection,
    *,
    entity: str,
    attribute_aliases: set[str],
    extra_caption_ids: set[str] | None = None,
) -> dict[str, Any]:
    return _lexicon_raw_overlap_match(
        conn,
        view="attribute_object_pairs",
        label_column="attribute",
        raw_column="attribute_raw_surfaces",
        aliases=attribute_aliases,
        where_column="object",
        where_value=entity,
        extra_caption_ids=extra_caption_ids,
    )


def _lexicon_raw_overlap_match(
    conn: sqlite3.Connection,
    *,
    view: str,
    label_column: str,
    raw_column: str,
    aliases: set[str],
    where_column: str | None = None,
    where_value: str | None = None,
    extra_caption_ids: set[str] | None = None,
) -> dict[str, Any]:
    exact_aliases = {_exact_key(value) for value in aliases if _exact_key(value)}
    query = f"SELECT _row_id, {label_column} AS label, {raw_column} AS raw_surfaces FROM {view}"
    params: tuple[Any, ...] = ()
    if where_column is not None:
        query += f" WHERE lower(trim({where_column})) = ?"
        params = (_exact_key(where_value),)

    row_ids: set[int] = set()
    matched_raw_surfaces: set[str] = set()
    canonical_attributes: set[str] = set()
    for row in conn.execute(query, params):
        row_raw_surfaces = {
            key
            for value in str(row["raw_surfaces"] or "").split("|")
            if (key := _exact_key(value))
        }
        overlap = row_raw_surfaces & exact_aliases
        if not overlap:
            continue
        row_ids.add(int(row["_row_id"]))
        matched_raw_surfaces.update(overlap)
        label = str(row["label"] or "").strip()
        if label:
            canonical_attributes.add(label)
    caption_ids = _caption_ids_for_rows(conn, view, row_ids)
    caption_ids.update(extra_caption_ids or ())
    return {
        "raw_surfaces": sorted(matched_raw_surfaces),
        "canonical_attributes": sorted(canonical_attributes),
        "caption_count": len(caption_ids),
    }


def _caption_count_for_rows(
    conn: sqlite3.Connection,
    view: str,
    row_ids: set[int],
) -> int:
    return len(_caption_ids_for_rows(conn, view, row_ids))


def _caption_ids_for_rows(
    conn: sqlite3.Connection,
    view: str,
    row_ids: set[int],
) -> set[str]:
    if not row_ids:
        return set()
    placeholders = ",".join("?" for _ in row_ids)
    rows = conn.execute(
        "SELECT DISTINCT caption_id FROM report_caption_index "
        f"WHERE view_name = ? AND row_id IN ({placeholders})",
        [view, *sorted(row_ids)],
    )
    return {str(row[0]) for row in rows}


def _legacy_attribute_caption_ids(
    legacy_quantity: dict[str, Any],
    *,
    aliases: set[str],
) -> set[str]:
    by_label = legacy_quantity.get("attribute_caption_ids", {})
    if not isinstance(by_label, dict):
        return set()
    result: set[str] = set()
    for alias in {_exact_key(value) for value in aliases}:
        values = by_label.get(alias, [])
        if isinstance(values, list):
            result.update(str(value) for value in values if str(value))
    return result


def _legacy_pair_caption_ids(
    legacy_quantity: dict[str, Any],
    *,
    entity: str,
    aliases: set[str],
) -> set[str]:
    by_pair = legacy_quantity.get("entity_attribute_pair_caption_ids", {})
    if not isinstance(by_pair, dict):
        return set()
    result: set[str] = set()
    entity_key = _exact_key(entity)
    for alias in {_exact_key(value) for value in aliases}:
        values = by_pair.get(f"{entity_key}\t{alias}", [])
        if isinstance(values, list):
            result.update(str(value) for value in values if str(value))
    return result


def _render_markdown(result: dict[str, Any], args: argparse.Namespace) -> str:
    lines = [
        "# Canary170 T5 Canonical vs Lexicon Exact-Raw Caption Counts",
        "",
        "- Count basis: `COUNT(DISTINCT caption_id)` on both sides.",
        "- Difference rate: `(lexicon caption_count - T5 caption_count) / "
        "T5 caption_count * 100`; `N/A` when the T5 count is zero.",
        "- T5: exact 8756 internal canonical `name`.",
        "- Lexicon: select canonical rows only when their `raw_surfaces` exactly "
        "overlap the explicit 8756 `{surface, canonical name}` set; then use the "
        "selected rows' distinct caption union.",
        "- No generated morphology, lemma family, separator, or shape variants.",
        "- Numeric quantity attributes use a transparent digit-to-word alias "
        "when the 8756 canonical label is a digit, so `2` can match exact "
        "lexicon rows such as `two`.",
        "- Legacy quantity facts omitted by the old report DB are merged by exact "
        "label and caption-ID union when the optional quantity-ID input is supplied.",
        "",
        "## Difference Band Summary",
        "",
        "| difference band | Entity count | Entity % | Attribute count | "
        "Attribute % | Entity-Attribute Pair count | Entity-Attribute Pair % |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    summary = result["difference_band_summary"]
    totals = {
        "entities": len(result["entities"]),
        "attributes": len(result["attributes"]),
        "pairs": len(result["pairs"]),
    }
    for band in DIFFERENCE_BANDS:
        lines.append(
            _table_row(
                [
                    band,
                    _fmt(summary["entities"][band]),
                    _fmt_band_percent(summary["entities"][band], totals["entities"]),
                    _fmt(summary["attributes"][band]),
                    _fmt_band_percent(
                        summary["attributes"][band],
                        totals["attributes"],
                    ),
                    _fmt(summary["pairs"][band]),
                    _fmt_band_percent(summary["pairs"][band], totals["pairs"]),
                ],
            ),
        )
    lines.extend(
        [
            "",
            "## Aggregate Statistics",
            "",
            "| group | rows | T5 total | lexicon total | lexicon - T5 total diff | "
            "total difference rate | Pearson r | R^2 | MAE | MAPE |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ],
    )
    statistics = result["statistics"]
    for label, key in [
        ("Entity", "entities"),
        ("Attribute", "attributes"),
        ("Entity-Attribute Pair", "pairs"),
    ]:
        stat = statistics[key]
        lines.append(
            _table_row(
                [
                    label,
                    _fmt(stat["rows"]),
                    _fmt(stat["t5_total"]),
                    _fmt(stat["lexicon_total"]),
                    _fmt(stat["total_diff"]),
                    _fmt_percent(stat["total_diff_percent"]),
                    _fmt_decimal(stat["pearson_r"], places=4),
                    _fmt_decimal(stat["r_squared"], places=4),
                    _fmt_decimal(stat["mean_absolute_error"], places=2),
                    _fmt_unsigned_percent(
                        stat["mean_absolute_percentage_error"],
                    ),
                ],
            ),
        )
    lines.extend(
        [
            "",
        "## 1. Entity Caption Counts",
        "",
        "| entity | T5 canonical caption_count | lexicon canonical caption_count | "
        "lexicon - T5 diff | difference rate | difference band |",
        "|---|---:|---:|---:|---:|---|",
        ],
    )
    for row in result["entities"]:
        lines.append(
            _table_row(
                [
                    row["entity"],
                    _fmt(row["t5_caption_count"]),
                    _fmt(row["lexicon_caption_count"]),
                    _fmt(row["lexicon_caption_count"] - row["t5_caption_count"]),
                    _fmt_percent(row["lexicon_minus_t5_percent"]),
                    row["difference_band"],
                ],
            ),
        )

    lines.extend(
        [
            "",
            "## 2. Attribute Caption Counts",
            "",
            "| 8756 raw surface | T5 canonical name | T5 caption_count | "
            "lexicon matched raw_surface | lexicon canonical attribute | "
            "lexicon caption_count | lexicon - T5 diff | difference rate | "
            "difference band |",
            "|---|---|---:|---|---|---:|---:|---:|---|",
        ],
    )
    for row in result["attributes"]:
        lines.append(
            _table_row(
                [
                    row["surface"],
                    _join(row["t5_canonical_names"]),
                    _fmt(row["t5_caption_count"]),
                    _join(row["lexicon_matched_raw_surfaces"]),
                    _join(row["lexicon_canonical_attributes"]),
                    _fmt(row["lexicon_caption_count"]),
                    _fmt(row["lexicon_caption_count"] - row["t5_caption_count"]),
                    _fmt_percent(row["lexicon_minus_t5_percent"]),
                    row["difference_band"],
                ],
            ),
        )

    lines.extend(
        [
            "",
            "## 3. Entity-Attribute Pair Caption Counts",
            "",
            "| entity | 8756 raw surface | T5 canonical name | T5 caption_count | "
            "lexicon matched raw_surface | lexicon canonical attribute | "
            "lexicon caption_count | lexicon - T5 diff | difference rate | "
            "difference band |",
            "|---|---|---|---:|---|---|---:|---:|---:|---|",
        ],
    )
    for row in result["pairs"]:
        lines.append(
            _table_row(
                [
                    row["entity"],
                    row["surface"],
                    _join(row["t5_canonical_names"]),
                    _fmt(row["t5_caption_count"]),
                    _join(row["lexicon_matched_raw_surfaces"]),
                    _join(row["lexicon_canonical_attributes"]),
                    _fmt(row["lexicon_caption_count"]),
                    _fmt(row["lexicon_caption_count"] - row["t5_caption_count"]),
                    _fmt_percent(row["lexicon_minus_t5_percent"]),
                    row["difference_band"],
                ],
            ),
        )

    lines.extend(
        [
            "",
            "## Inputs",
            "",
            f"- 8756 terms: `{args.display_terms_json}`",
            f"- T5 canonical counts: `{args.t5_canonical_counts_json}`",
            f"- T5 multi-canonical unions: `{args.t5_multi_canonical_union_json}`",
            f"- legacy quantity caption IDs: `{args.legacy_quantity_caption_ids_json}`",
            f"- report DB: `{args.report_db}`",
            "",
        ],
    )
    return "\n".join(lines)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _exact_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _table_row(values: list[Any]) -> str:
    return "| " + " | ".join(_escape(value) for value in values) + " |"


def _escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def _join(values: list[str]) -> str:
    return "<br>".join(values) if values else "MISSING"


def _fmt(value: int) -> str:
    return f"{int(value):,}"


def _fmt_percent(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.1f}%"


def _fmt_unsigned_percent(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1f}%"


def _fmt_decimal(value: float | None, *, places: int) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.{places}f}"


def _fmt_band_percent(count: int, total: int) -> str:
    if total <= 0:
        return "N/A"
    return f"{count / total * 100.0:.1f}%"


if __name__ == "__main__":
    raise SystemExit(main())
