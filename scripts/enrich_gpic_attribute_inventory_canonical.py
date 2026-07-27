from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPT_DIR = Path(__file__).resolve().parent
for path in (SRC, SCRIPT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import nltk
import wn
from wn.morphy import Morphy

from enrich_gpic_inventory_canonical import (
    _clear_canonical_columns,
    _count_by,
    _decide_canonical,
    _fieldnames_with_canonical_columns,
    _read_ngram_evidence,
    _read_tsv,
    _write_tsv,
)
from gpic_concepts_v1.atomic_io import atomic_text_writer
from gpic_concepts_v1.stage4_extract_raw import NLTK_DATA_DIR, OEWN_SPEC, WN_DATA_DIR


ATTRIBUTE_CANONICAL_READY_STATUSES = frozenset(("chosen", "excluded"))
ATTRIBUTE_CANONICAL_MORPHY_POS = ("a", "n", "v", "r")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add canonical attribute surface decisions to a GPIC observed attribute inventory TSV.",
    )
    parser.add_argument("--input", required=True, help="Input GPIC observed attribute inventory TSV")
    parser.add_argument("--output", required=True, help="Output inventory TSV with canonical columns")
    parser.add_argument("--ngram-evidence", help="Optional Google Ngram evidence TSV")
    parser.add_argument("--ambiguous-output", help="Optional TSV for rows with unresolved canonical surface")
    parser.add_argument("--summary", help="Optional JSON summary path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows, fieldnames = _read_tsv(Path(args.input))
    fieldnames = _fieldnames_with_canonical_columns(fieldnames)
    normalized_legacy_no_synset_rows = _normalize_legacy_no_synset_status(rows)
    _raise_if_manual_rows_exist(args, rows)

    wn.config.data_directory = str(WN_DATA_DIR)
    nltk.data.path.insert(0, str(NLTK_DATA_DIR))
    oewn = wn.Wordnet(OEWN_SPEC, expand="")
    morphy = Morphy(oewn)
    ngram_evidence = _read_ngram_evidence(Path(args.ngram_evidence)) if args.ngram_evidence else {}

    selected = 0
    excluded_not_applicable = 0
    missing_synset = 0
    ambiguous_rows: list[dict[str, str]] = []
    lookup_errors: list[dict[str, str]] = []
    for row in rows:
        if row.get("decision_status", "").strip() == "excluded":
            _clear_canonical_columns(row)
            row["canonical_selection_tag"] = "not_applicable_excluded"
            excluded_not_applicable += 1
            continue
        synset_id = row.get("selected_oewn_synset", "").strip()
        if not synset_id:
            _clear_canonical_columns(row)
            row["canonical_selection_tag"] = "not_applicable_no_selected_synset"
            missing_synset += 1
            continue
        try:
            synset = oewn.synset(synset_id)
        except Exception as exc:
            _clear_canonical_columns(row)
            row["canonical_selection_tag"] = "ambiguous_selected_synset_lookup_error"
            lookup_errors.append({"selected_oewn_synset": synset_id, "error": repr(exc)})
            ambiguous_rows.append(row)
            continue

        decision = _decide_canonical(
            row,
            synset=synset,
            morphy=morphy,
            ngram_evidence=ngram_evidence,
            morphy_pos=ATTRIBUTE_CANONICAL_MORPHY_POS,
            attribute_mwe_mode=row.get("attribute_unit_type", "").strip() == "mwe",
        )
        row.update(decision)
        if row["canonical_surface"]:
            selected += 1
        else:
            ambiguous_rows.append(row)

    _write_tsv(Path(args.output), rows, fieldnames)
    if args.ambiguous_output:
        _write_tsv(Path(args.ambiguous_output), ambiguous_rows, fieldnames)

    summary: dict[str, Any] = {
        "input": args.input,
        "output": args.output,
        "rows": len(rows),
        "normalized_legacy_no_synset_rows": normalized_legacy_no_synset_rows,
        "excluded_not_applicable_rows": excluded_not_applicable,
        "selected_synset_missing_rows": missing_synset,
        "canonical_selected_rows": selected,
        "canonical_ambiguous_rows": len(ambiguous_rows),
        "canonical_lookup_error_rows": len(lookup_errors),
        "canonical_lookup_errors": lookup_errors[:10],
        "canonical_selection_tag_counts": _count_by(rows, "canonical_selection_tag"),
    }
    if args.summary:
        with atomic_text_writer(Path(args.summary)) as handle:
            handle.write(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            handle.write("\n")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if ambiguous_rows:
        raise SystemExit(
            "attribute canonical ambiguous rows require manual resolution: "
            f"canonical_ambiguous_rows={len(ambiguous_rows)}; "
            f"ambiguous_output={args.ambiguous_output or ''}"
        )


def _normalize_legacy_no_synset_status(rows: list[dict[str, str]]) -> int:
    normalized = 0
    for row in rows:
        status = row.get("decision_status", "").strip()
        if status != "no_synset":
            continue
        row["decision_status"] = "chosen"
        if not row.get("decision_reason", "").strip():
            row["decision_reason"] = "no_oewn_attribute_synset"
        normalized += 1
    return normalized


def _raise_if_manual_rows_exist(args: argparse.Namespace, rows: list[dict[str, str]]) -> None:
    blockers = _attribute_canonical_blockers(rows)
    if not blockers:
        return
    summary: dict[str, Any] = {
        "input": args.input,
        "output": args.output,
        "rows": len(rows),
        "status": "blocked_manual_attribute_resolution_before_canonical",
        "blocked_rows": len(blockers),
        "blocked_examples": blockers[:10],
    }
    if args.summary:
        with atomic_text_writer(Path(args.summary)) as handle:
            handle.write(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            handle.write("\n")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    raise SystemExit(
        "manual attribute resolution required before canonical enrichment: "
        f"blocked_rows={len(blockers)}"
    )


def _attribute_canonical_blockers(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    for row in rows:
        status = row.get("decision_status", "").strip()
        if status in ATTRIBUTE_CANONICAL_READY_STATUSES:
            continue
        blockers.append(
            {
                "blocker_reason": "pending_attribute_manual_decision_status",
                "observed_surface": row.get("observed_surface", ""),
                "span_key": row.get("span_key", ""),
                "decision_status": status,
                "decision_reason": row.get("decision_reason", ""),
                "selected_query": row.get("selected_query", ""),
                "selected_oewn_synset": row.get("selected_oewn_synset", ""),
                "synset_selection_tag": row.get("synset_selection_tag", ""),
                "attribute_gate": row.get("attribute_gate", ""),
            }
        )
    return blockers

if __name__ == "__main__":
    main()
