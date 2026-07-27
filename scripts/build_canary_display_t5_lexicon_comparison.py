from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class CountRow:
    row_id: int
    label: str
    raw_surfaces: str
    caption_count: int


@dataclass(frozen=True, slots=True)
class PairRow:
    row_id: int
    entity: str
    attribute: str


@dataclass(frozen=True, slots=True)
class MatchResult:
    labels: tuple[str, ...]
    caption_count: int
    strategy: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build Canary170 comparison tables using visible 8756 attribute "
            "surfaces and caption-level counts on both sides."
        ),
    )
    parser.add_argument("--display-terms-json", required=True, type=Path)
    parser.add_argument("--t5-counts-json", required=True, type=Path)
    parser.add_argument("--report-db", required=True, type=Path)
    parser.add_argument("--legacy-quantity-caption-ids-json", type=Path)
    parser.add_argument("--output-md", required=True, type=Path)
    parser.add_argument("--output-counts-json", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _assert_existing_file(args.display_terms_json, "display terms JSON")
    _assert_existing_file(args.t5_counts_json, "T5 counts JSON")
    _assert_existing_file(args.report_db, "lexicon report DB")
    if args.legacy_quantity_caption_ids_json is not None:
        _assert_existing_file(
            args.legacy_quantity_caption_ids_json,
            "legacy quantity caption IDs JSON",
        )
    terms = json.loads(args.display_terms_json.read_text(encoding="utf-8"))
    t5_counts = json.loads(args.t5_counts_json.read_text(encoding="utf-8"))
    _assert_exact_surface_caption_counts(t5_counts)
    legacy_quantity = _load_legacy_quantity_caption_ids(
        args.legacy_quantity_caption_ids_json,
    )

    entities = [str(value).strip().lower() for value in terms["entities"]]
    attributes = [str(value).strip().lower() for value in terms["attributes"]]
    pairs = [
        (str(item["entity"]).strip().lower(), str(item["attribute"]).strip().lower())
        for item in terms["pairs"]
    ]

    display_t5 = _select_exact_surface_t5_counts(
        entities=entities,
        attributes=attributes,
        pairs=pairs,
        t5_counts=t5_counts,
    )

    conn = sqlite3.connect(f"file:{args.report_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    _assert_caption_index(conn)
    object_rows = _load_count_rows(
        conn,
        view="objects",
        label_col="canonical_object",
        raw_col="object_raw_surfaces",
    )
    attribute_rows = _load_count_rows(
        conn,
        view="attributes",
        label_col="canonical_attribute",
        raw_col="attribute_raw_surfaces",
    )
    pair_rows_by_key = _load_pair_rows(conn)

    object_matcher = LabelMatcher(
        object_rows,
        include_variants_with_exact=False,
        conn=conn,
        view="objects",
    )
    attribute_matcher = LabelMatcher(
        attribute_rows,
        include_variants_with_exact=True,
        conn=conn,
        view="attributes",
        supplemental_caption_ids=legacy_quantity["attribute_caption_ids"],
    )

    lines = [
        "# Canary170 T5 vs Lexicon Caption Count Comparison",
        "",
        "- T5 attribute label policy: 8756 visible display surface.",
        "- T5 lookup policy: exact same-string match against the 1M T5 attribute name/term.",
        "- Lexicon count source: local 1M quantity-as-attribute report caption index.",
        "- Legacy Stage 6 quantity facts are unioned by caption ID when supplied.",
        "- Count basis on both sides: unique caption_count.",
        "- No T5 morphology, generated variant, or base-form remapping is applied.",
        "- Match order: exact canonical/raw, then normalized generated variants.",
        "",
        "## 1. Entity Caption Counts",
        "",
        "| entity | T5 caption_count | lexicon entity | lexicon caption_count | lexicon - T5 diff | match |",
        "|---|---:|---|---:|---:|---|",
    ]
    for entity in sorted(entities, key=lambda key: (-display_t5["entity_caption_counts"].get(key, 0), key)):
        t5_count = int(display_t5["entity_caption_counts"].get(entity, 0))
        match = object_matcher.match(entity)
        lines.append(
            _table_row(
                [
                    entity,
                    _fmt_int(t5_count),
                    _join_labels(match.labels),
                    _fmt_int(match.caption_count),
                    _fmt_int(match.caption_count - t5_count),
                    match.strategy,
                ],
            ),
        )

    lines.extend(
        [
            "",
            "## 2. Attribute Caption Counts",
            "",
            "| T5 attribute | T5 caption_count | lexicon attribute | lexicon caption_count | lexicon - T5 diff | match |",
            "|---|---:|---|---:|---:|---|",
        ],
    )
    for attribute in sorted(
        attributes,
        key=lambda key: (-display_t5["attribute_caption_counts"].get(key, 0), key),
    ):
        t5_count = int(display_t5["attribute_caption_counts"].get(attribute, 0))
        match = attribute_matcher.match(attribute)
        lines.append(
            _table_row(
                [
                    attribute,
                    _fmt_int(t5_count),
                    _join_labels(match.labels),
                    _fmt_int(match.caption_count),
                    _fmt_int(match.caption_count - t5_count),
                    match.strategy,
                ],
            ),
        )

    pair_rows: list[dict[str, Any]] = []
    for entity, attribute in pairs:
        entity_match = object_matcher.match(entity)
        attribute_match = attribute_matcher.match(attribute)
        caption_count = _fetch_pair_caption_count(
            conn,
            pair_rows_by_key=pair_rows_by_key,
            entity_labels=entity_match.labels,
            attribute_labels=attribute_match.labels,
            supplemental_caption_ids=legacy_quantity[
                "entity_attribute_pair_caption_ids"
            ],
        )
        t5_count = int(display_t5["entity_attribute_pair_caption_counts"].get(f"{entity}\t{attribute}", 0))
        pair_rows.append(
            {
                "entity": entity,
                "attribute": attribute,
                "t5_count": t5_count,
                "lexicon_entities": entity_match.labels,
                "lexicon_attributes": attribute_match.labels,
                "lexicon_count": caption_count,
                "strategy": _pair_strategy(entity_match, attribute_match, caption_count),
            },
        )

    lines.extend(
        [
            "",
            "## 3. Entity-Attribute Pair Caption Counts",
            "",
            "| entity | attribute | T5 caption_count | lexicon entity | lexicon attribute | lexicon caption_count | lexicon - T5 diff | match |",
            "|---|---|---:|---|---|---:|---:|---|",
        ],
    )
    for row in sorted(pair_rows, key=lambda item: (-int(item["t5_count"]), item["entity"], item["attribute"])):
        lines.append(
            _table_row(
                [
                    row["entity"],
                    row["attribute"],
                    _fmt_int(row["t5_count"]),
                    _join_labels(row["lexicon_entities"]),
                    _join_labels(row["lexicon_attributes"]),
                    _fmt_int(row["lexicon_count"]),
                    _fmt_int(row["lexicon_count"] - row["t5_count"]),
                    row["strategy"],
                ],
            ),
        )

    lines.extend(
        [
            "",
            "## Files",
            "",
            f"- display terms JSON: `{args.display_terms_json}`",
            f"- selected exact-term T5 counts JSON: `{args.output_counts_json}`",
            (
                "- legacy quantity caption IDs JSON: "
                f"`{args.legacy_quantity_caption_ids_json}`"
                if args.legacy_quantity_caption_ids_json is not None
                else "- legacy quantity caption IDs JSON: not supplied"
            ),
        ],
    )

    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    args.output_counts_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_counts_json.write_text(json.dumps(display_t5, ensure_ascii=False, indent=2), encoding="utf-8")
    conn.close()
    print(
        json.dumps(
            {
                "output_md": str(args.output_md),
                "output_counts_json": str(args.output_counts_json),
                "entities": len(entities),
                "attributes": len(attributes),
                "pairs": len(pairs),
                "illuminate_present": "illuminate" in attributes,
                "illuminated_t5_count": display_t5["attribute_caption_counts"].get("illuminated", 0),
                "cluster_t5_count": display_t5["attribute_caption_counts"].get("cluster", 0),
                "clustered_t5_count": display_t5["attribute_caption_counts"].get("clustered", 0),
                "clusters_t5_count": display_t5["attribute_caption_counts"].get("clusters", 0),
                "silhouette_t5_count": display_t5["attribute_caption_counts"].get("silhouette", 0),
                "silhouetted_t5_count": display_t5["attribute_caption_counts"].get("silhouetted", 0),
                "smaller_t5_count": display_t5["attribute_caption_counts"].get("smaller", 0),
                "eiffel_tower_illuminated_t5_count": display_t5["entity_attribute_pair_caption_counts"].get(
                    "eiffel tower\tilluminated",
                    0,
                ),
            },
            ensure_ascii=False,
        ),
    )
    return 0


def _assert_exact_surface_caption_counts(t5_counts: dict[str, Any]) -> None:
    if t5_counts.get("count_basis") != "unique_caption_id":
        raise SystemExit(
            "T5 input is not caption-level data: count_basis must be 'unique_caption_id'. "
            "Frequency and mention-count inputs are forbidden.",
        )
    if t5_counts.get("attribute_label_policy") != "exact_t5_attribute_name":
        raise SystemExit(
            "T5 input is not an exact attribute-name/term scan. "
            "Surface remaps and canonical/base-form copying are forbidden.",
        )


def _select_exact_surface_t5_counts(
    *,
    entities: list[str],
    attributes: list[str],
    pairs: list[tuple[str, str]],
    t5_counts: dict[str, Any],
) -> dict[str, Any]:
    source_entities = t5_counts.get("entity_caption_counts", {})
    source_attributes = t5_counts.get("attribute_caption_counts", {})
    source_pairs = t5_counts.get("entity_attribute_pair_caption_counts", {})

    return {
        "records_scanned": t5_counts.get("records_scanned"),
        "elapsed_seconds": t5_counts.get("elapsed_seconds"),
        "count_basis": "unique_caption_id",
        "entity_label_policy": t5_counts.get("entity_label_policy"),
        "attribute_label_policy": t5_counts.get("attribute_label_policy"),
        "pair_label_policy": t5_counts.get("pair_label_policy"),
        "entity_caption_counts": {entity: int(source_entities.get(entity, 0)) for entity in entities},
        "attribute_caption_counts": {
            attribute: int(source_attributes.get(attribute, 0)) for attribute in attributes
        },
        "entity_attribute_pair_caption_counts": {
            f"{entity}\t{attribute}": int(source_pairs.get(f"{entity}\t{attribute}", 0))
            for entity, attribute in pairs
        },
    }


def _assert_caption_index(conn: sqlite3.Connection) -> None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'report_caption_index'",
    ).fetchone()
    if exists is None:
        raise SystemExit(
            "Lexicon report DB lacks report_caption_index; unique caption_count cannot be guaranteed.",
        )


def _load_count_rows(
    conn: sqlite3.Connection,
    *,
    view: str,
    label_col: str,
    raw_col: str,
) -> list[CountRow]:
    rows = conn.execute(
        f"SELECT _row_id, {label_col} AS label, {raw_col} AS raw_surfaces, caption_count FROM {view}",
    ).fetchall()
    return [
        CountRow(
            row_id=int(row["_row_id"]),
            label=str(row["label"] or ""),
            raw_surfaces=str(row["raw_surfaces"] or ""),
            caption_count=int(row["caption_count"] or 0),
        )
        for row in rows
    ]


def _load_pair_rows(conn: sqlite3.Connection) -> dict[tuple[str, str], set[PairRow]]:
    rows_by_key: dict[tuple[str, str], set[PairRow]] = defaultdict(set)
    for row in conn.execute(
        "SELECT _row_id, object, attribute FROM attribute_object_pairs",
    ):
        pair_row = PairRow(
            row_id=int(row["_row_id"]),
            entity=str(row["object"] or ""),
            attribute=str(row["attribute"] or ""),
        )
        rows_by_key[(_norm(pair_row.entity), _norm(pair_row.attribute))].add(pair_row)
    return rows_by_key


class LabelMatcher:
    def __init__(
        self,
        rows: Iterable[CountRow],
        *,
        include_variants_with_exact: bool,
        conn: sqlite3.Connection,
        view: str,
        supplemental_caption_ids: dict[str, set[str]] | None = None,
    ) -> None:
        self._include_variants_with_exact = include_variants_with_exact
        self._conn = conn
        self._view = view
        self._supplemental_caption_ids = supplemental_caption_ids or {}
        self._exact: dict[str, set[CountRow]] = defaultdict(set)
        for row in rows:
            for surface in _row_surfaces(row):
                norm = _norm(surface)
                if norm:
                    self._exact[norm].add(row)

    def match(self, label: str) -> MatchResult:
        norm = _norm(label)
        exact_rows = self._exact.get(norm, set())
        exact_supplemental = {norm} if norm in self._supplemental_caption_ids else set()
        if (exact_rows or exact_supplemental) and not self._include_variants_with_exact:
            return _match_result(
                self._conn,
                self._view,
                exact_rows,
                "exact_canonical_or_raw",
                supplemental_labels=exact_supplemental,
                supplemental_caption_ids=self._supplemental_caption_ids,
            )

        variant_rows: set[CountRow] = set()
        variant_supplemental: set[str] = set()
        for key in _label_keys(norm):
            variant_rows.update(self._exact.get(key, set()))
            if key in self._supplemental_caption_ids:
                variant_supplemental.add(key)
        variant_rows = _filter_variant_rows(label, variant_rows)
        if exact_rows or exact_supplemental:
            combined = set(exact_rows)
            combined.update(variant_rows)
            combined_supplemental = set(exact_supplemental)
            combined_supplemental.update(variant_supplemental)
            strategy = "exact_canonical_or_raw"
            if variant_rows - exact_rows or variant_supplemental - exact_supplemental:
                strategy = "exact_plus_generated_variant"
            return _match_result(
                self._conn,
                self._view,
                combined,
                strategy,
                supplemental_labels=combined_supplemental,
                supplemental_caption_ids=self._supplemental_caption_ids,
            )
        return _match_result(
            self._conn,
            self._view,
            variant_rows,
            "generated_variant"
            if variant_rows or variant_supplemental
            else "missing",
            supplemental_labels=variant_supplemental,
            supplemental_caption_ids=self._supplemental_caption_ids,
        )


def _fetch_pair_caption_count(
    conn: sqlite3.Connection,
    *,
    pair_rows_by_key: dict[tuple[str, str], set[PairRow]],
    entity_labels: Iterable[str],
    attribute_labels: Iterable[str],
    supplemental_caption_ids: dict[str, set[str]] | None = None,
) -> int:
    row_ids: set[int] = set()
    for entity in entity_labels:
        for attribute in attribute_labels:
            rows = pair_rows_by_key.get((_norm(entity), _norm(attribute)), set())
            row_ids.update(row.row_id for row in rows)
    caption_ids = _distinct_caption_ids(conn, "attribute_object_pairs", row_ids)
    supplemental = supplemental_caption_ids or {}
    for entity in entity_labels:
        for attribute in attribute_labels:
            caption_ids.update(
                supplemental.get(f"{_norm(entity)}\t{_norm(attribute)}", set()),
            )
    return len(caption_ids)


def _assert_existing_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise SystemExit(f"Missing {label}: {path}")


def _pair_strategy(entity_match: MatchResult, attribute_match: MatchResult, caption_count: int) -> str:
    if not entity_match.labels or not attribute_match.labels:
        return "missing"
    if not caption_count:
        return "no_pair_row_for_matched_labels"
    if entity_match.strategy == attribute_match.strategy == "exact_canonical_or_raw":
        return "exact_canonical_or_raw"
    return f"entity:{entity_match.strategy};attribute:{attribute_match.strategy}"


def _match_result(
    conn: sqlite3.Connection,
    view: str,
    rows: Iterable[CountRow],
    strategy: str,
    supplemental_labels: Iterable[str] = (),
    supplemental_caption_ids: dict[str, set[str]] | None = None,
) -> MatchResult:
    unique = sorted(set(rows), key=lambda row: (-row.caption_count, _norm(row.label)))
    labels = list(_display_labels(unique))
    seen = {_norm(label) for label in labels}
    supplemental_labels = set(supplemental_labels)
    for label in sorted(supplemental_labels):
        if _norm(label) not in seen:
            labels.append(label)
            seen.add(_norm(label))
    caption_ids = _distinct_caption_ids(
        conn,
        view,
        {row.row_id for row in unique},
    )
    supplemental = supplemental_caption_ids or {}
    for label in supplemental_labels:
        caption_ids.update(supplemental.get(_norm(label), set()))
    return MatchResult(
        labels=tuple(labels),
        caption_count=len(caption_ids),
        strategy=strategy,
    )


def _distinct_caption_count(
    conn: sqlite3.Connection,
    view: str,
    row_ids: set[int],
) -> int:
    return len(_distinct_caption_ids(conn, view, row_ids))


def _distinct_caption_ids(
    conn: sqlite3.Connection,
    view: str,
    row_ids: set[int],
) -> set[str]:
    if not row_ids:
        return set()
    placeholders = ", ".join("?" for _ in row_ids)
    query = (
        "SELECT DISTINCT caption_id FROM report_caption_index "
        f"WHERE view_name = ? AND row_id IN ({placeholders})"
    )
    return {
        str(row[0])
        for row in conn.execute(query, [view, *sorted(row_ids)])
        if row[0]
    }


def _load_legacy_quantity_caption_ids(
    path: Path | None,
) -> dict[str, dict[str, set[str]]]:
    empty = {
        "attribute_caption_ids": {},
        "entity_attribute_pair_caption_ids": {},
    }
    if path is None:
        return empty
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("count_basis") != "unique_caption_id":
        raise SystemExit(
            "Legacy quantity input is not caption-level data: "
            "count_basis must be 'unique_caption_id'.",
        )
    result: dict[str, dict[str, set[str]]] = {}
    for field in empty:
        source = payload.get(field, {})
        if not isinstance(source, dict):
            raise SystemExit(f"Legacy quantity input field must be an object: {field}")
        result[field] = {
            _norm(key): {str(caption_id) for caption_id in caption_ids if caption_id}
            for key, caption_ids in source.items()
        }
    return result


def _filter_variant_rows(label: str, rows: Iterable[CountRow]) -> set[CountRow]:
    query_is_proper = _looks_like_proper_label(str(label or "").strip())
    filtered = set()
    for row in rows:
        candidate = _display_label(row.label)
        if _looks_like_proper_label(candidate) and not query_is_proper:
            continue
        filtered.add(row)
    return filtered


def _row_surfaces(row: CountRow) -> list[str]:
    surfaces = [row.label]
    surfaces.extend(part for part in row.raw_surfaces.split("|") if part)
    return surfaces


def _label_keys(label: str) -> set[str]:
    norm = _norm(label)
    keys = {norm}
    if not norm:
        return keys
    keys.update(_separator_variants(norm))
    for item in list(keys):
        keys.update(_inflection_variants(item))
        keys.update(_shape_variants(item))
    return {_norm(key) for key in keys if _norm(key)}


def _separator_variants(label: str) -> set[str]:
    variants = {label}
    variants.add(label.replace("_", " "))
    variants.add(label.replace("-", " "))
    variants.add(label.replace(" ", "-"))
    variants.add(label.replace(" - ", "-"))
    variants.add(label.replace(" - ", " "))
    return variants


def _inflection_variants(label: str) -> set[str]:
    variants = {label}
    words = label.split()
    if not words:
        return variants
    head = words[-1]
    stems = {head}
    if head.endswith("s") and not head.endswith("ss") and len(head) > 3:
        stems.add(head[:-1])
        variants.add(" ".join((*words[:-1], head[:-1])))
    if head.endswith("ies") and len(head) > 3:
        stems.add(head[:-3] + "y")
        variants.add(" ".join((*words[:-1], head[:-3] + "y")))
    if head.endswith("ied") and len(head) > 3:
        stems.add(head[:-3] + "y")
    if head.endswith("ed") and len(head) > 3:
        base = head[:-2]
        stems.add(base)
        if len(base) >= 2 and base[-1] == base[-2]:
            stems.add(base[:-1])
        stems.add(head[:-1])
    if head.endswith("ing") and len(head) > 4:
        base = head[:-3]
        stems.add(base)
        stems.add(base + "e")
        if len(base) >= 2 and base[-1] == base[-2]:
            stems.add(base[:-1])
    for stem in list(stems):
        forms = {stem + "ed", stem + "ing"}
        if stem.endswith("e"):
            forms.add(stem + "d")
            forms.add(stem[:-1] + "ing")
        if _is_cvc(stem):
            forms.add(stem + stem[-1] + "ed")
            forms.add(stem + stem[-1] + "ing")
        forms.update(_irregular_participle_forms(stem))
        for form in forms:
            variants.add(" ".join((*words[:-1], form)))
    if len(words) == 1:
        variants.update(stems)
    return variants


def _irregular_participle_forms(stem: str) -> set[str]:
    irregular = {"draw": {"drawn"}}
    forms = set(irregular.get(stem, set()))
    if "-" in stem:
        prefix, suffix = stem.rsplit("-", 1)
        for form in irregular.get(suffix, set()):
            forms.add(f"{prefix}-{form}")
    return forms


def _shape_variants(label: str) -> set[str]:
    variants = {label}
    if label.endswith(" - shape"):
        variants.add(label[: -len(" - shape")] + "-shaped")
    if label.endswith(" shape"):
        variants.add(label[: -len(" shape")] + "-shaped")
    if label.endswith("-shaped"):
        variants.add(label[: -len("-shaped")] + " shape")
        variants.add(label[: -len("-shaped")] + " - shape")
    return variants


def _is_cvc(stem: str) -> bool:
    if len(stem) < 3:
        return False
    vowels = set("aeiou")
    return stem[-1] not in vowels and stem[-2] in vowels and stem[-3] not in vowels and stem[-1] not in {"w", "x", "y"}


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = text.replace("_", " ")
    return text


def _display_labels(rows: Iterable[CountRow]) -> tuple[str, ...]:
    seen = set()
    labels: list[str] = []
    for row in rows:
        label = _display_label(row.label)
        key = _norm(label)
        if key in seen:
            continue
        seen.add(key)
        labels.append(label)
    return tuple(labels)


def _display_label(label: str) -> str:
    return str(label or "").strip()


def _looks_like_proper_label(label: str) -> bool:
    stripped = _display_label(label)
    if not stripped:
        return False
    letters = [char for char in stripped if char.isalpha()]
    if not letters:
        return False
    return any(char.isupper() for char in letters) and not stripped.isupper()


def _fmt_int(value: int) -> str:
    return f"{value:,}"


def _join_labels(labels: Iterable[str]) -> str:
    values = list(labels)
    return "<br>".join(values) if values else "MISSING"


def _table_row(values: Iterable[Any]) -> str:
    return "| " + " | ".join(_escape_md(value) for value in values) + " |"


def _escape_md(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")


if __name__ == "__main__":
    raise SystemExit(main())
