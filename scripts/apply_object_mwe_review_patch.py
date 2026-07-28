from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import sys
from typing import Any, Mapping

import wn

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from incident_gate import guarded_entrypoint

from gpic_concepts_v1.atomic_io import atomic_text_writer
from gpic_concepts_v1.stage4_extract_raw import (
    CONDITIONAL_OBJECT_LEXFILES,
    OBJECT_COMPATIBLE_LEXFILES,
    OEWN_SPEC,
    WN_DATA_DIR,
)


REVIEW_SOURCE = "object_mwe_exhaustive_review_20260727"
EXPECTED_COUNTS = {
    "additional_surface_rewrite": 34,
    "surface_rewrite_reason_relabel": 13,
    "full_phrase_synset_correction": 17,
}

SEMANTIC_COPY_FIELDS = (
    "selected_query",
    "has_oewn_noun_synset",
    "oewn_synset_count",
    "selected_oewn_synset",
    "selected_oewn_lexfile",
    "objectness_gate",
    "synset_lemmas",
    "parent_oewn_synsets",
    "parent_oewn_lexfiles",
    "parent_lemmas",
    "parent_selection_tag",
    "canonical_surface",
    "canonical_label_key",
    "canonical_selection_tag",
    "canonical_candidate_lemmas",
    "canonical_candidate_lemma_counts",
    "google_ngram_candidate_surfaces",
    "google_ngram_candidate_mean_frequencies",
    "all_oewn_synsets",
    "all_oewn_lexfiles",
    "synset_selection_tag",
    "wn30_lemma_counts",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Apply the vetted object MWE review actions without touching unrelated "
            "object inventory rows."
        )
    )
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--review", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--audit-output", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument(
        "--allow-count-mismatch",
        action="store_true",
        help="Allow review verdict counts to differ from the locked 34/13/17 contract.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = apply_object_mwe_review_patch(
        inventory_path=Path(args.inventory),
        review_path=Path(args.review),
        output_path=Path(args.output),
        audit_output_path=Path(args.audit_output),
        summary_path=Path(args.summary),
        allow_count_mismatch=args.allow_count_mismatch,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def apply_object_mwe_review_patch(
    *,
    inventory_path: Path,
    review_path: Path,
    output_path: Path,
    audit_output_path: Path,
    summary_path: Path,
    allow_count_mismatch: bool = False,
) -> dict[str, Any]:
    rows, fieldnames = _read_tsv(inventory_path)
    review_rows, _ = _read_tsv(review_path)
    by_key = _unique_by_span_key(rows)

    selected_reviews = [
        row for row in review_rows if row.get("review_verdict", "") in EXPECTED_COUNTS
    ]
    verdict_counts = Counter(row.get("review_verdict", "") for row in selected_reviews)
    if not allow_count_mismatch and dict(verdict_counts) != EXPECTED_COUNTS:
        raise ValueError(
            "unexpected_object_mwe_review_counts: "
            + json.dumps(dict(verdict_counts), ensure_ascii=False, sort_keys=True)
        )

    review_by_key = _unique_review_by_span_key(selected_reviews)
    missing_inventory_rows = sorted(set(review_by_key) - set(by_key))
    if missing_inventory_rows:
        raise ValueError(
            "review rows missing from inventory: " + ", ".join(missing_inventory_rows[:20])
        )

    wn.config.data_directory = str(WN_DATA_DIR)
    oewn = wn.Wordnet(OEWN_SPEC, expand="")

    output_rows: list[dict[str, str]] = []
    audit_rows: list[dict[str, str]] = []
    for row in rows:
        key = row.get("span_key", "")
        review = review_by_key.get(key)
        if review is None:
            output_rows.append(dict(row))
            continue
        before = dict(row)
        verdict = review.get("review_verdict", "")
        patched = dict(row)
        if verdict in {"additional_surface_rewrite", "surface_rewrite_reason_relabel"}:
            _apply_surface_rewrite(
                patched,
                review=review,
                replacement_source=by_key[_normalized_review_replacement_key(review)],
                oewn=oewn,
            )
        elif verdict == "full_phrase_synset_correction":
            _apply_full_phrase_synset_correction(patched, review=review, oewn=oewn)
        else:
            raise AssertionError(verdict)
        output_rows.append(patched)
        audit_rows.append(_audit_row(before=before, after=patched, review=review))

    _validate_no_unrelated_changes(rows, output_rows, set(review_by_key))
    _validate_patched_rows(output_rows, review_by_key)
    _write_tsv(output_path, output_rows, fieldnames)
    _write_tsv(audit_output_path, audit_rows, _audit_fieldnames(audit_rows))

    summary = {
        "status": "ok",
        "inventory": str(inventory_path),
        "review": str(review_path),
        "output": str(output_path),
        "audit_output": str(audit_output_path),
        "rows": len(output_rows),
        "patched_rows": len(audit_rows),
        "patched_verdict_counts": dict(sorted(verdict_counts.items())),
        "decision_reason_counts_after": _count_by(output_rows, "decision_reason"),
        "decision_status_counts_after": _count_by(output_rows, "decision_status"),
        "changed_span_keys": [row["span_key"] for row in audit_rows],
    }
    with atomic_text_writer(summary_path) as handle:
        handle.write(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")
    return summary


def _apply_surface_rewrite(
    row: dict[str, str],
    *,
    review: Mapping[str, str],
    replacement_source: Mapping[str, str],
    oewn: wn.Wordnet,
) -> None:
    replacement_key = _normalized_review_replacement_key(review)
    if replacement_source.get("decision_status", "") != "chosen":
        raise ValueError(f"surface rewrite replacement is not chosen: {replacement_key}")
    if not replacement_source.get("selected_oewn_synset", ""):
        raise ValueError(f"surface rewrite replacement has no synset: {replacement_key}")
    for field in SEMANTIC_COPY_FIELDS:
        if field in row:
            row[field] = replacement_source.get(field, "")
    row["decision_status"] = "chosen"
    row["decision_reason"] = "manual_surface_rewrite_to_replacement_span"
    row["selected_lookup_case"] = "manual_surface_rewrite_to_replacement_span"
    row["manual_action"] = "surface_rewrite_only"
    row["replacement_span_key"] = replacement_key
    row["replacement_reason"] = "object_mwe_review_surface_rewrite_to_head"
    row["manual_note"] = _append_note(
        row.get("manual_note", ""),
        review.get("review_reason", "") or "object MWE review selected head rewrite",
    )
    row["decision_basis"] = _append_pipe(row.get("decision_basis", ""), REVIEW_SOURCE)
    _normalize_candidate_lexfiles(row, oewn=oewn)


def _apply_full_phrase_synset_correction(
    row: dict[str, str],
    *,
    review: Mapping[str, str],
    oewn: wn.Wordnet,
) -> None:
    selected_synset_id = review.get("proposed_selected_oewn_synset", "").strip()
    if not selected_synset_id:
        raise ValueError(f"full phrase correction missing proposed synset: {row}")
    candidate_ids = _split_pipe(row.get("all_oewn_synsets", ""))
    if selected_synset_id not in candidate_ids:
        raise ValueError(
            f"full phrase correction synset is not a candidate: {row.get('span_key')} "
            f"{selected_synset_id}"
        )
    synset = oewn.synset(selected_synset_id)
    parents = list(synset.hypernyms())
    row["decision_status"] = "chosen"
    row["decision_reason"] = "manual_full_phrase_synset_correction"
    row["selected_lookup_case"] = "manual_full_phrase_synset_correction"
    row["has_oewn_noun_synset"] = "true"
    row["oewn_synset_count"] = str(len(candidate_ids))
    row["selected_oewn_synset"] = selected_synset_id
    row["selected_oewn_lexfile"] = synset.lexfile()
    row["objectness_gate"] = _objectness_gate(synset.lexfile())
    row["synset_lemmas"] = "|".join(synset.lemmas())
    row["parent_oewn_synsets"] = "|".join(parent.id for parent in parents)
    row["parent_oewn_lexfiles"] = "|".join(
        f"{parent.id}:{parent.lexfile()}" for parent in parents
    )
    row["parent_lemmas"] = "|".join(
        f"{parent.id}:{';'.join(parent.lemmas())}" for parent in parents
    )
    row["parent_selection_tag"] = (
        "selected_all_immediate_oewn_hypernyms"
        if parents
        else "no_immediate_oewn_hypernym"
    )
    _normalize_candidate_lexfiles(row, oewn=oewn)
    row["synset_selection_tag"] = "manual_full_phrase_synset_correction"
    row["decision_basis"] = _append_pipe(row.get("decision_basis", ""), REVIEW_SOURCE)
    row["manual_action"] = ""
    row["replacement_span_key"] = ""
    row["replacement_reason"] = ""
    row["manual_note"] = _append_note(
        row.get("manual_note", ""),
        review.get("review_reason", "") or "object MWE review selected alternate full phrase synset",
    )
    _validate_preserved_canonical(row, synset=synset)
    row["canonical_selection_tag"] = _append_pipe(
        row.get("canonical_selection_tag", ""),
        "manual_full_phrase_synset_correction_preserved_canonical",
    )


def _validate_preserved_canonical(row: Mapping[str, str], *, synset: wn.Synset) -> None:
    canonical = row.get("canonical_surface", "").strip()
    if not canonical:
        raise ValueError(f"full phrase correction lost canonical: {row.get('span_key')}")
    lemma_keys = {_separator_key(lemma) for lemma in synset.lemmas()}
    if _separator_key(canonical) not in lemma_keys:
        raise ValueError(
            "full phrase correction canonical is not in selected synset lemmas: "
            f"{row.get('span_key')} canonical={canonical!r} lemmas={sorted(synset.lemmas())}"
        )


def _validate_patched_rows(
    rows: list[dict[str, str]],
    review_by_key: Mapping[str, Mapping[str, str]],
) -> None:
    by_key = {row["span_key"]: row for row in rows}
    for key, review in review_by_key.items():
        row = by_key[key]
        verdict = review.get("review_verdict", "")
        if verdict in {"additional_surface_rewrite", "surface_rewrite_reason_relabel"}:
            replacement_key = _normalized_review_replacement_key(review)
            if row.get("decision_reason") != "manual_surface_rewrite_to_replacement_span":
                raise ValueError(f"rewrite row missing rewrite decision_reason: {key}")
            if row.get("replacement_span_key") != replacement_key:
                raise ValueError(f"rewrite row replacement mismatch: {key}")
            if row.get("manual_action") != "surface_rewrite_only":
                raise ValueError(f"rewrite row missing manual_action: {key}")
        elif verdict == "full_phrase_synset_correction":
            if row.get("selected_oewn_synset") != review.get(
                "proposed_selected_oewn_synset", ""
            ).strip():
                raise ValueError(f"full phrase correction synset mismatch: {key}")
            if row.get("decision_reason") != "manual_full_phrase_synset_correction":
                raise ValueError(f"full phrase correction reason mismatch: {key}")
        if row.get("decision_status") != "chosen":
            raise ValueError(f"patched row is not chosen: {key}")
        if not row.get("selected_oewn_synset", ""):
            raise ValueError(f"patched row has no selected synset: {key}")
        if not row.get("canonical_surface", ""):
            raise ValueError(f"patched row has no canonical surface: {key}")
        _validate_candidate_lexfile_format(row)


def _normalize_candidate_lexfiles(row: dict[str, str], *, oewn: wn.Wordnet) -> None:
    candidate_ids = _split_pipe(row.get("all_oewn_synsets", ""))
    if not candidate_ids:
        row["all_oewn_lexfiles"] = ""
        return
    row["all_oewn_lexfiles"] = "|".join(
        f"{candidate_id}:{oewn.synset(candidate_id).lexfile()}"
        for candidate_id in candidate_ids
    )


def _validate_candidate_lexfile_format(row: Mapping[str, str]) -> None:
    candidate_ids = _split_pipe(row.get("all_oewn_synsets", ""))
    candidate_lexfiles = _split_pipe(row.get("all_oewn_lexfiles", ""))
    if len(candidate_ids) != len(candidate_lexfiles):
        raise ValueError(f"candidate metadata length mismatch: {row.get('span_key')}")
    for candidate_id, lexfile in zip(candidate_ids, candidate_lexfiles):
        if not lexfile.startswith(candidate_id + ":"):
            raise ValueError(
                "candidate lexfile is not keyed by synset id: "
                f"{row.get('span_key')} {candidate_id} {lexfile}"
            )


def _validate_no_unrelated_changes(
    before_rows: list[dict[str, str]],
    after_rows: list[dict[str, str]],
    changed_keys: set[str],
) -> None:
    if len(before_rows) != len(after_rows):
        raise ValueError("row count changed")
    for before, after in zip(before_rows, after_rows):
        key = before.get("span_key", "")
        if key not in changed_keys and before != after:
            raise ValueError(f"unrelated row changed: {key}")


def _audit_row(
    *,
    before: Mapping[str, str],
    after: Mapping[str, str],
    review: Mapping[str, str],
) -> dict[str, str]:
    fields = (
        "span_key",
        "review_verdict",
        "recommended_action",
        "proposed_replacement_span_key",
        "proposed_selected_oewn_synset",
        "decision_reason",
        "manual_action",
        "replacement_span_key",
        "selected_query",
        "selected_oewn_synset",
        "selected_oewn_lexfile",
        "objectness_gate",
        "canonical_surface",
        "parent_lemmas",
        "all_oewn_synsets",
        "all_oewn_lexfiles",
    )
    output: dict[str, str] = {
        "span_key": before.get("span_key", ""),
        "review_verdict": review.get("review_verdict", ""),
        "recommended_action": review.get("recommended_action", ""),
        "proposed_replacement_span_key": review.get("proposed_replacement_span_key", ""),
        "proposed_selected_oewn_synset": review.get("proposed_selected_oewn_synset", ""),
    }
    for field in fields[5:]:
        output[f"before_{field}"] = before.get(field, "")
        output[f"after_{field}"] = after.get(field, "")
    return output


def _audit_fieldnames(rows: list[Mapping[str, str]]) -> list[str]:
    fieldnames: list[str] = []
    for row in rows:
        for field in row:
            if field not in fieldnames:
                fieldnames.append(field)
    return fieldnames


def _normalized_review_replacement_key(review: Mapping[str, str]) -> str:
    value = review.get("proposed_replacement_span_key", "").strip()
    if not value:
        value = review.get("replacement_span_key", "").strip()
    key = _surface_key(value)
    if not key:
        raise ValueError(f"review row missing replacement span key: {review}")
    return key


def _objectness_gate(lexfile: str) -> str:
    if lexfile in OBJECT_COMPATIBLE_LEXFILES:
        return "object_compatible"
    if lexfile in CONDITIONAL_OBJECT_LEXFILES:
        return "conditional"
    return "hard_conflict"


def _separator_key(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").replace("-", " ").split())


def _surface_key(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _split_pipe(value: str) -> list[str]:
    return [part.strip() for part in value.split("|") if part.strip()]


def _append_pipe(current: str, addition: str) -> str:
    parts = [part for part in current.split("|") if part]
    if addition not in parts:
        parts.append(addition)
    return "|".join(parts)


def _append_note(current: str, addition: str) -> str:
    addition = " ".join(addition.strip().split())
    if not addition:
        return current
    if not current:
        return addition
    if addition in current:
        return current
    return f"{current} | {addition}"


def _count_by(rows: list[dict[str, str]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(row.get(field, "") for row in rows).items()))


def _unique_by_span_key(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    by_key: dict[str, dict[str, str]] = {}
    duplicates: set[str] = set()
    for row in rows:
        key = row.get("span_key", "")
        if not key:
            raise ValueError("inventory row missing span_key")
        if key in by_key:
            duplicates.add(key)
        by_key[key] = row
    if duplicates:
        raise ValueError(f"duplicate inventory span_key: {sorted(duplicates)[:20]}")
    return by_key


def _unique_review_by_span_key(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    by_key: dict[str, dict[str, str]] = {}
    duplicates: set[str] = set()
    for row in rows:
        key = row.get("span_key", "")
        if not key:
            raise ValueError("review row missing span_key")
        if key in by_key:
            duplicates.add(key)
        by_key[key] = row
    if duplicates:
        raise ValueError(f"duplicate review span_key: {sorted(duplicates)[:20]}")
    return by_key


def _read_tsv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [dict(row) for row in reader], list(reader.fieldnames or [])


def _write_tsv(path: Path, rows: list[Mapping[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_text_writer(path, newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


if __name__ == "__main__":
    raise SystemExit(guarded_entrypoint("apply_object_mwe_review_patch", main))
