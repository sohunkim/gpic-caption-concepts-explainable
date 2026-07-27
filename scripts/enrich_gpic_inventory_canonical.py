from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
import sys
from typing import Any
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import nltk
import wn
from nltk.corpus import wordnet as wn30
from wn.morphy import Morphy

from gpic_concepts_v1.atomic_io import atomic_text_writer
from gpic_concepts_v1.inventory_validation import (
    final_manual_resolution_blockers,
    is_manual_no_synset_head_fallback,
)
from gpic_concepts_v1.stage4_extract_raw import NLTK_DATA_DIR, OEWN_SPEC, WN_DATA_DIR


CANONICAL_COLUMNS = [
    "canonical_surface",
    "canonical_label_key",
    "canonical_selection_tag",
    "canonical_candidate_lemmas",
    "canonical_candidate_lemma_counts",
    "google_ngram_candidate_surfaces",
    "google_ngram_candidate_mean_frequencies",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add canonical object surface decisions to a GPIC observed object inventory TSV.",
    )
    parser.add_argument("--input", required=True, help="Input GPIC observed object inventory TSV")
    parser.add_argument("--output", required=True, help="Output inventory TSV with canonical columns")
    parser.add_argument("--ngram-evidence", help="Optional Google Ngram evidence TSV")
    parser.add_argument("--ambiguous-output", help="Optional TSV for rows with unresolved canonical surface")
    parser.add_argument("--summary", help="Optional JSON summary path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows, fieldnames = _read_tsv(Path(args.input))
    fieldnames = _fieldnames_with_canonical_columns(fieldnames)
    _raise_if_manual_rows_exist(args, rows)

    wn.config.data_directory = str(WN_DATA_DIR)
    nltk.data.path.insert(0, str(NLTK_DATA_DIR))
    oewn = wn.Wordnet(OEWN_SPEC, expand="")
    morphy = Morphy(oewn)
    ngram_evidence = _read_ngram_evidence(Path(args.ngram_evidence)) if args.ngram_evidence else {}

    selected = 0
    missing_synset = 0
    ambiguous_rows: list[dict[str, str]] = []
    lookup_errors: list[dict[str, str]] = []
    for row in rows:
        synset_id = row.get("selected_oewn_synset", "").strip()
        if not synset_id:
            if is_manual_no_synset_head_fallback(row) and row.get("canonical_surface", "").strip():
                row["canonical_label_key"] = _surface_key(row["canonical_surface"])
                row["canonical_selection_tag"] = (
                    row.get("canonical_selection_tag", "").strip()
                    or "manual_no_synset_head_canonical"
                )
            else:
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

        decision = _decide_canonical(row, synset=synset, morphy=morphy, ngram_evidence=ngram_evidence)
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
            "canonical ambiguous rows require manual resolution before Stage 4: "
            f"canonical_ambiguous_rows={len(ambiguous_rows)}; "
            f"ambiguous_output={args.ambiguous_output or ''}"
        )


def _raise_if_manual_rows_exist(args: argparse.Namespace, rows: list[dict[str, str]]) -> None:
    blockers = final_manual_resolution_blockers(rows)
    if not blockers:
        return
    summary: dict[str, Any] = {
        "input": args.input,
        "output": args.output,
        "rows": len(rows),
        "status": "blocked_manual_resolution_before_canonical",
        "blocked_rows": len(blockers),
        "blocked_examples": blockers[:10],
    }
    if args.summary:
        with atomic_text_writer(Path(args.summary)) as handle:
            handle.write(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            handle.write("\n")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    raise SystemExit(
        "manual resolution required before canonical enrichment: "
        f"blocked_rows={len(blockers)}"
    )


def _decide_canonical(
    row: dict[str, str],
    *,
    synset: Any,
    morphy: Morphy,
    ngram_evidence: dict[tuple[str, str], float],
    morphy_pos: tuple[str, ...] = ("n",),
    attribute_mwe_mode: bool = False,
) -> dict[str, str]:
    synset_id = row.get("selected_oewn_synset", "")
    all_lemmas = _ordered_unique(str(lemma) for lemma in synset.lemmas())
    support_keys = _observed_surface_variant_keys(
        row,
        morphy,
        morphy_pos=morphy_pos,
        attribute_mwe_mode=attribute_mwe_mode,
    )
    candidate_lemmas = [lemma for lemma in all_lemmas if _surface_key(lemma) in support_keys]
    if not candidate_lemmas and len(all_lemmas) == 1:
        candidate_lemmas = all_lemmas[:]

    count_rows = [(lemma, _selected_synset_lemma_count(synset, lemma)) for lemma in candidate_lemmas]
    ngram_candidates = _ngram_candidate_surfaces(
        candidate_lemmas=candidate_lemmas,
        all_lemmas=all_lemmas,
        row=row,
    )
    canonical, tag = _select_canonical(
        synset_id=synset_id,
        candidate_lemmas=candidate_lemmas,
        count_rows=count_rows,
        observed_exact_surfaces=_observed_exact_surfaces(row),
        observed_exact_surface_keys=_observed_exact_surface_keys(row),
        all_lemmas=all_lemmas,
        ngram_candidates=ngram_candidates,
        ngram_evidence=ngram_evidence,
    )
    return {
        "canonical_surface": canonical,
        "canonical_label_key": _surface_key(canonical) if canonical else "",
        "canonical_selection_tag": tag,
        "canonical_candidate_lemmas": "|".join(candidate_lemmas),
        "canonical_candidate_lemma_counts": "|".join(
            f"{lemma}:{count}" for lemma, count in count_rows
        ),
        "google_ngram_candidate_surfaces": "|".join(ngram_candidates),
        "google_ngram_candidate_mean_frequencies": "|".join(
            f"{surface}:{ngram_evidence.get((synset_id, _surface_key(surface)), -1):.12g}"
            for surface in ngram_candidates
        ),
    }


def _select_canonical(
    *,
    synset_id: str,
    candidate_lemmas: list[str],
    count_rows: list[tuple[str, int]],
    observed_exact_surfaces: set[str],
    observed_exact_surface_keys: set[str],
    all_lemmas: list[str],
    ngram_candidates: list[str],
    ngram_evidence: dict[tuple[str, str], float],
) -> tuple[str, str]:
    if not candidate_lemmas:
        if len(all_lemmas) == 1:
            return all_lemmas[0], "selected_single_synset_lemma_without_observed_surface_match"
        return _select_by_google_ngram(
            synset_id=synset_id,
            ngram_candidates=_ordered_unique(_surface_key(lemma) for lemma in all_lemmas),
            ngram_evidence=ngram_evidence,
            fallback_tag="ambiguous_no_observed_surface_variant_matched_oewn_lemma",
        )
    if len(candidate_lemmas) == 1:
        return candidate_lemmas[0], "selected_single_observed_variant_matched_synset_lemma"

    valid_rows = [(lemma, count) for lemma, count in count_rows if count >= 0]
    if valid_rows:
        max_count = max(count for _, count in valid_rows)
        winners = [lemma for lemma, count in valid_rows if count == max_count]
        if max_count > 0 and len(winners) == 1:
            return winners[0], "selected_by_wn30_lemma_count_unique_positive_max"

    exact_matches = [
        lemma for lemma in candidate_lemmas if _display_surface(lemma) in observed_exact_surfaces
    ]
    if len(exact_matches) == 1:
        return exact_matches[0], "selected_by_unique_observed_span_surface"

    normalized_exact_matches = [
        lemma for lemma in candidate_lemmas if _surface_key(lemma) in observed_exact_surface_keys
    ]
    if len(normalized_exact_matches) == 1:
        return normalized_exact_matches[0], "selected_by_unique_observed_span_surface_key"

    if valid_rows:
        max_count = max(count for _, count in valid_rows)
        fallback_tag = "ambiguous_wn30_all_zero_or_missing" if max_count <= 0 else "ambiguous_wn30_tie"
        return _select_by_google_ngram(
            synset_id=synset_id,
            ngram_candidates=ngram_candidates,
            ngram_evidence=ngram_evidence,
            fallback_tag=fallback_tag,
        )
    return _select_by_google_ngram(
        synset_id=synset_id,
        ngram_candidates=ngram_candidates,
        ngram_evidence=ngram_evidence,
        fallback_tag="ambiguous_wn30_mapping_missing",
    )


def _select_by_google_ngram(
    *,
    synset_id: str,
    ngram_candidates: list[str],
    ngram_evidence: dict[tuple[str, str], float],
    fallback_tag: str,
) -> tuple[str, str]:
    scored = [
        (surface, ngram_evidence.get((synset_id, _surface_key(surface)), -1.0))
        for surface in ngram_candidates
    ]
    valid = [(surface, score) for surface, score in scored if score >= 0.0]
    if len(valid) == 1:
        return valid[0][0], "selected_by_single_available_google_ngram"
    if len(valid) < 2:
        return "", f"{fallback_tag}_google_ngram_evidence_missing"
    max_score = max(score for _, score in valid)
    winners = [surface for surface, score in valid if score == max_score]
    if len(winners) == 1:
        return winners[0], "selected_by_google_ngram_frequency_unique_max"
    return "", f"{fallback_tag}_google_ngram_tie"


def _observed_surface_variant_keys(
    row: dict[str, str],
    morphy: Morphy,
    *,
    morphy_pos: tuple[str, ...] = ("n",),
    attribute_mwe_mode: bool = False,
) -> set[str]:
    keys: set[str] = set()
    surfaces = _observed_surfaces(row)
    variant_fn = _attribute_mwe_surface_variants if attribute_mwe_mode else _surface_variants
    for surface in surfaces:
        keys.update(variant_fn(surface))
    if attribute_mwe_mode:
        for surface in surfaces:
            words = [
                word
                for word in re.split(r"[\s_-]+", _surface_key(surface))
                if word
            ]
            if len(words) < 2:
                continue
            anchor = words[-1]
            for pos in morphy_pos:
                result = morphy(anchor, pos)
                lemmas = result.get(pos, set()) if result else set()
                for lemma in lemmas:
                    keys.update(
                        _attribute_mwe_surface_variants(
                            " ".join([*words[:-1], str(lemma)])
                        )
                    )
        return {key for key in keys if key}
    for key in list(keys):
        for pos in morphy_pos:
            result = morphy(key, pos)
            lemmas = result.get(pos, set()) if result else set()
            for lemma in lemmas:
                keys.update(_surface_variants(str(lemma)))
    return {key for key in keys if key}


def _observed_exact_surfaces(row: dict[str, str]) -> set[str]:
    return {_display_surface(surface) for surface in _observed_caption_surfaces(row) if surface}


def _observed_exact_surface_keys(row: dict[str, str]) -> set[str]:
    return {_surface_key(surface) for surface in _observed_caption_surfaces(row) if surface}


def _observed_surfaces(row: dict[str, str]) -> list[str]:
    surfaces = _observed_caption_surfaces(row)
    selected_query = row.get("selected_query", "")
    if selected_query:
        surfaces.append(selected_query)
    return _ordered_unique(surfaces)


def _observed_caption_surfaces(row: dict[str, str]) -> list[str]:
    surfaces = [row.get("observed_surface", "")]
    surfaces.extend(_split_pipe(row.get("example_surfaces", "")))
    return _ordered_unique(surfaces)


def _surface_variants(text: str) -> set[str]:
    exact = _surface_key(text)
    separator_variant = _surface_key(text.replace("-", " ").replace("_", " "))
    underscore_variant = _surface_key(text.replace("-", "_").replace(" ", "_"))
    joined_variant = "".join(exact.replace("-", " ").split())
    return {value for value in (exact, separator_variant, underscore_variant, joined_variant) if value}


def _attribute_mwe_surface_variants(text: str) -> set[str]:
    words = [
        word
        for word in re.split(r"[\s_-]+", _surface_key(text))
        if word
    ]
    if len(words) < 2:
        return {_surface_key(text)} if _surface_key(text) else set()
    return {
        value
        for value in (
            _surface_key(" ".join(words)),
            _surface_key("-".join(words)),
            _surface_key("_".join(words)),
        )
        if value
    }


def _ngram_candidate_surfaces(
    *,
    candidate_lemmas: list[str],
    all_lemmas: list[str],
    row: dict[str, str],
) -> list[str]:
    if candidate_lemmas:
        return _ordered_unique(_surface_key(lemma) for lemma in candidate_lemmas)
    observed = _ordered_unique(_surface_key(surface) for surface in _observed_surfaces(row))
    lemma_surfaces = _ordered_unique(_surface_key(lemma) for lemma in all_lemmas)
    return _ordered_unique(observed + lemma_surfaces)


def _selected_synset_lemma_count(synset: Any, lemma_name: str) -> int:
    target_key = _surface_key(lemma_name)
    count = 0
    mapped = 0
    for sense in synset.senses():
        sense_key = _sense_key_from_oewn_sense_id(sense.id)
        if not sense_key:
            continue
        try:
            lemma = wn30.lemma_from_key(sense_key)
        except Exception:
            continue
        if _surface_key(lemma.name()) != target_key:
            continue
        mapped += 1
        count += lemma.count()
    if mapped == 0:
        return -1
    return count


def _sense_key_from_oewn_sense_id(sense_id: str) -> str:
    match = re.match(r"^oewn-(.+)__(\d)\.(\d\d)\.(\d\d)\.\.$", sense_id)
    if match is None:
        return ""
    lemma, ss_type, lex_filenum, lex_id = match.groups()
    return f"{lemma}%{ss_type}:{lex_filenum}:{lex_id}::"


def _surface_key(text: str) -> str:
    normalized = text.strip().lower()
    normalized = normalized.translate(
        str.maketrans(
            {
                "’": "'",
                "‘": "'",
                "‛": "'",
                "＇": "'",
                "‐": "-",
                "‑": "-",
                "‒": "-",
                "–": "-",
                "—": "-",
                "―": "-",
                "−": "-",
            }
        )
    )
    normalized = "".join(
        char
        for char in unicodedata.normalize("NFKD", normalized)
        if not unicodedata.combining(char)
    )
    normalized = normalized.replace("_", " ")
    normalized = re.sub(r"(?<=\w)\s*-\s*(?=\w)", "-", normalized)
    normalized = re.sub(r"(?<=\w)\s*'\s*(?=\w)", "'", normalized)
    return " ".join(normalized.split())


def _display_surface(text: str) -> str:
    normalized = text.strip()
    normalized = normalized.translate(
        str.maketrans(
            {
                "’": "'",
                "‘": "'",
                "‛": "'",
                "＇": "'",
                "‐": "-",
                "‑": "-",
                "‒": "-",
                "–": "-",
                "—": "-",
                "―": "-",
                "−": "-",
            }
        )
    )
    normalized = normalized.replace("_", " ")
    normalized = re.sub(r"(?<=\w)\s*-\s*(?=\w)", "-", normalized)
    normalized = re.sub(r"(?<=\w)\s*'\s*(?=\w)", "'", normalized)
    return " ".join(normalized.split())


def _read_tsv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fieldnames = list(reader.fieldnames or [])
        rows = [
            {key: (value or "").strip() for key, value in row.items()}
            for row in reader
        ]
    return rows, fieldnames


def _read_ngram_evidence(path: Path) -> dict[tuple[str, str], float]:
    if not path.exists():
        return {}
    evidence: dict[tuple[str, str], float] = {}
    rows, _ = _read_tsv(path)
    for row in rows:
        synset_id = row.get("selected_oewn_synset", "")
        surface_key = row.get("surface_key", "")
        try:
            mean_frequency = float(row.get("mean_frequency", ""))
        except ValueError:
            continue
        if synset_id and surface_key:
            evidence[(synset_id, surface_key)] = mean_frequency
    return evidence


def _fieldnames_with_canonical_columns(fieldnames: list[str]) -> list[str]:
    existing = [field for field in fieldnames if field not in CANONICAL_COLUMNS]
    try:
        insert_at = existing.index("parent_selection_tag") + 1
    except ValueError:
        try:
            insert_at = existing.index("synset_lemmas") + 1
        except ValueError:
            insert_at = len(existing)
    return existing[:insert_at] + CANONICAL_COLUMNS + existing[insert_at:]


def _clear_canonical_columns(row: dict[str, str]) -> None:
    for column in CANONICAL_COLUMNS:
        row[column] = ""


def _write_tsv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with atomic_text_writer(path, newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames,
            delimiter="\t",
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def _split_pipe(value: str) -> list[str]:
    return [part for part in value.split("|") if part]


def _ordered_unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _count_by(rows: list[dict[str, str]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(key, "")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


if __name__ == "__main__":
    main()
