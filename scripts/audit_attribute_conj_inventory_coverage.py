from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gpic_concepts_v1.atomic_io import atomic_text_writer
from gpic_concepts_v1.io_jsonl import iter_jsonl


CONJ_MODIFIER_SOURCE = "conj_of_attribute_modifier"
OUTPUT_COLUMNS = (
    "span_key",
    "observed_surfaces",
    "mention_count",
    "caption_count",
    "example_caption_ids",
    "pos_values",
    "tag_values",
    "current_lexicon_status",
    "current_canonical",
    "oewn_lookup_status",
    "oewn_lookup_case",
    "oewn_query",
    "oewn_synset_count",
    "oewn_synset_ids",
    "oewn_lexfiles",
    "oewn_lemmas",
    "decision_status",
    "decision_reason",
    "selected_oewn_synset",
    "selected_oewn_lexfile",
)


@dataclass(slots=True)
class ConjAttributeAccumulator:
    span_key: str
    mention_count: int = 0
    caption_ids: set[str] = field(default_factory=set)
    surfaces: Counter[str] = field(default_factory=Counter)
    pos_values: set[str] = field(default_factory=set)
    tag_values: set[str] = field(default_factory=set)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit Stage 4 conjunct attributes against the exact Stage 5 "
            "attribute synonym lookup, optionally probing missing keys in OEWN."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--raw-mentions")
    source.add_argument(
        "--coverage-input",
        help="Reuse a previously collected coverage TSV and only run enrichment.",
    )
    parser.add_argument("--attribute-synonyms")
    parser.add_argument("--source-summary")
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--oewn-hit-output")
    parser.add_argument("--oewn-auto-chosen-output")
    parser.add_argument("--oewn-needs-manual-output")
    parser.add_argument("--progress-output")
    parser.add_argument("--progress-interval-lines", type=int, default=250_000)
    parser.add_argument(
        "--probe-oewn",
        action="store_true",
        help="Probe current-lexicon misses with the formal attribute OEWN lookup.",
    )
    return parser.parse_args(argv)


def load_attribute_synonyms(path: str | Path) -> dict[str, str]:
    result: dict[str, str] = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None or not {"raw", "canonical"}.issubset(reader.fieldnames):
            raise ValueError(f"invalid attribute synonym schema: {path}")
        for row in reader:
            key = stage5_key(row.get("raw", ""))
            canonical = str(row.get("canonical", "")).strip()
            if key and canonical:
                result[key] = canonical
    return result


def collect_conj_attribute_coverage(
    records: Iterable[Mapping[str, Any]],
    *,
    progress_callback: Any | None = None,
    progress_interval_lines: int = 250_000,
) -> tuple[dict[str, ConjAttributeAccumulator], int]:
    if progress_interval_lines < 1:
        raise ValueError("progress_interval_lines must be greater than zero")
    inventory: dict[str, ConjAttributeAccumulator] = {}
    scanned_lines = 0
    for record in records:
        scanned_lines += 1
        if _is_conj_attribute(record):
            surface = str(record.get("text", "")).strip()
            span_key = stage5_key(surface)
            if span_key:
                accumulator = inventory.setdefault(
                    span_key,
                    ConjAttributeAccumulator(span_key=span_key),
                )
                accumulator.mention_count += 1
                accumulator.surfaces[surface] += 1
                caption_id = str(record.get("caption_id", "")).strip()
                if caption_id:
                    accumulator.caption_ids.add(caption_id)
                source_detail = record.get("source_detail")
                if isinstance(source_detail, Mapping):
                    pos = str(source_detail.get("pos", "")).strip()
                    tag = str(source_detail.get("tag", "")).strip()
                    if pos:
                        accumulator.pos_values.add(pos)
                    if tag:
                        accumulator.tag_values.add(tag)
        if progress_callback is not None and scanned_lines % progress_interval_lines == 0:
            progress_callback(scanned_lines, len(inventory))
    return inventory, scanned_lines


def build_audit_rows(
    inventory: Mapping[str, ConjAttributeAccumulator],
    *,
    attribute_synonyms: Mapping[str, str],
    probe_oewn: bool,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for span_key, accumulator in inventory.items():
        canonical = attribute_synonyms.get(span_key, "")
        row = {
            "span_key": span_key,
            "observed_surfaces": "|".join(
                surface
                for surface, _ in sorted(
                    accumulator.surfaces.items(),
                    key=lambda item: (-item[1], item[0]),
                )
            ),
            "mention_count": str(accumulator.mention_count),
            "caption_count": str(len(accumulator.caption_ids)),
            "example_caption_ids": "|".join(sorted(accumulator.caption_ids)[:5]),
            "pos_values": "|".join(sorted(accumulator.pos_values)),
            "tag_values": "|".join(sorted(accumulator.tag_values)),
            "current_lexicon_status": "hit" if canonical else "raw_fallback",
            "current_canonical": canonical,
            "oewn_lookup_status": "not_probed",
            "oewn_lookup_case": "",
            "oewn_query": "",
            "oewn_synset_count": "",
            "oewn_synset_ids": "",
            "oewn_lexfiles": "",
            "oewn_lemmas": "",
            "decision_status": "",
            "decision_reason": "",
            "selected_oewn_synset": "",
            "selected_oewn_lexfile": "",
        }
        rows.append(row)
    if probe_oewn:
        _probe_raw_fallback_rows(rows)
    return sorted(
        rows,
        key=lambda row: (
            row["current_lexicon_status"] != "raw_fallback",
            -int(row["mention_count"]),
            row["span_key"],
        ),
    )


def load_coverage_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None or not set(OUTPUT_COLUMNS).issubset(reader.fieldnames):
            raise ValueError(f"invalid conj attribute coverage schema: {path}")
        return [
            {column: str(row.get(column, "")) for column in OUTPUT_COLUMNS}
            for row in reader
        ]


def _probe_raw_fallback_rows(rows: Sequence[dict[str, str]]) -> None:
    from build_gpic_observed_attribute_inventory import _load_attribute_lookup_runtime

    runtime_lookup = _load_attribute_lookup_runtime()
    for row in rows:
        if row["current_lexicon_status"] != "raw_fallback":
            continue
        tag_values = set(row["tag_values"].split("|"))
        lookup = runtime_lookup(
            row["span_key"],
            require_surface_query_conflict_check=bool({"NNS", "NNPS"} & tag_values),
        )
        synsets = tuple(lookup.synsets) if lookup is not None else ()
        selected = lookup.selected_synset if lookup is not None else None
        row.update(
            {
                "oewn_lookup_status": "hit" if synsets else "no_synset",
                "oewn_lookup_case": lookup.lookup_case if lookup is not None else "",
                "oewn_query": lookup.query if lookup is not None else "",
                "oewn_synset_count": str(len(synsets)),
                "oewn_synset_ids": "|".join(str(synset.id) for synset in synsets),
                "oewn_lexfiles": "|".join(
                    sorted({str(synset.lexfile()) for synset in synsets})
                ),
                "oewn_lemmas": "|".join(
                    sorted(
                        {
                            str(lemma)
                            for synset in synsets
                            for lemma in synset.lemmas()
                        }
                    )
                ),
                "decision_status": lookup.decision_status if lookup is not None else "",
                "decision_reason": lookup.decision_reason if lookup is not None else "",
                "selected_oewn_synset": str(selected.id) if selected is not None else "",
                "selected_oewn_lexfile": (
                    str(selected.lexfile()) if selected is not None else ""
                ),
            }
        )


def summarize(
    rows: Sequence[Mapping[str, str]],
    *,
    scanned_lines: int,
) -> dict[str, Any]:
    fallback_rows = [row for row in rows if row["current_lexicon_status"] == "raw_fallback"]
    oewn_hit_rows = [row for row in fallback_rows if row["oewn_lookup_status"] == "hit"]
    return {
        "scanned_raw_mention_lines": scanned_lines,
        "conj_attribute_unique_surfaces": len(rows),
        "conj_attribute_mentions": sum(int(row["mention_count"]) for row in rows),
        "conj_attribute_captions_sum_by_surface": sum(
            int(row["caption_count"]) for row in rows
        ),
        "current_lexicon_hit_unique_surfaces": len(rows) - len(fallback_rows),
        "current_raw_fallback_unique_surfaces": len(fallback_rows),
        "current_raw_fallback_mentions": sum(
            int(row["mention_count"]) for row in fallback_rows
        ),
        "raw_fallback_oewn_hit_unique_surfaces": len(oewn_hit_rows),
        "raw_fallback_oewn_hit_mentions": sum(
            int(row["mention_count"]) for row in oewn_hit_rows
        ),
        "raw_fallback_oewn_hit_decision_status_counts": dict(
            sorted(Counter(row["decision_status"] for row in oewn_hit_rows).items())
        ),
    }


def stage5_key(value: Any) -> str:
    return str(value).strip().lower()


def _is_conj_attribute(record: Mapping[str, Any]) -> bool:
    if record.get("mention_type") != "attribute":
        return False
    source_detail = record.get("source_detail")
    return (
        isinstance(source_detail, Mapping)
        and source_detail.get("modifier_source") == CONJ_MODIFIER_SOURCE
    )


def _write_tsv(path: str | Path, rows: Sequence[Mapping[str, str]]) -> None:
    with atomic_text_writer(Path(path), newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=OUTPUT_COLUMNS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    with atomic_text_writer(Path(path)) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    progress_path = Path(args.progress_output) if args.progress_output else None

    def write_progress(scanned_lines: int, unique_surfaces: int) -> None:
        payload = {
            "status": "running",
            "phase": "scan_raw_mentions",
            "scanned_raw_mention_lines": scanned_lines,
            "conj_attribute_unique_surfaces": unique_surfaces,
        }
        if progress_path is not None:
            _write_json(progress_path, payload)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)

    if args.coverage_input:
        rows = load_coverage_rows(args.coverage_input)
        if args.probe_oewn:
            _probe_raw_fallback_rows(rows)
        scanned_lines = 0
        if args.source_summary:
            with Path(args.source_summary).open("r", encoding="utf-8") as handle:
                source_summary = json.load(handle)
            scanned_lines = int(source_summary.get("scanned_raw_mention_lines", 0))
    else:
        if not args.attribute_synonyms:
            raise SystemExit("--attribute-synonyms is required with --raw-mentions")
        attribute_synonyms = load_attribute_synonyms(args.attribute_synonyms)
        inventory, scanned_lines = collect_conj_attribute_coverage(
            iter_jsonl(args.raw_mentions),
            progress_callback=write_progress,
            progress_interval_lines=args.progress_interval_lines,
        )
        rows = build_audit_rows(
            inventory,
            attribute_synonyms=attribute_synonyms,
            probe_oewn=args.probe_oewn,
        )
    summary = summarize(rows, scanned_lines=scanned_lines)
    summary.update(
        {
            "status": "complete",
            "raw_mentions": str(args.raw_mentions or ""),
            "coverage_input": str(args.coverage_input or ""),
            "attribute_synonyms": str(args.attribute_synonyms or ""),
            "output": str(args.output),
            "probe_oewn": bool(args.probe_oewn),
        }
    )
    _write_tsv(args.output, rows)
    oewn_hit_rows = [
        row
        for row in rows
        if row["current_lexicon_status"] == "raw_fallback"
        and row["oewn_lookup_status"] == "hit"
    ]
    if args.oewn_hit_output:
        _write_tsv(args.oewn_hit_output, oewn_hit_rows)
    if args.oewn_auto_chosen_output:
        _write_tsv(
            args.oewn_auto_chosen_output,
            [row for row in oewn_hit_rows if row["decision_status"] == "chosen"],
        )
    if args.oewn_needs_manual_output:
        _write_tsv(
            args.oewn_needs_manual_output,
            [row for row in oewn_hit_rows if row["decision_status"] == "needs_manual"],
        )
    _write_json(args.summary, summary)
    if progress_path is not None:
        _write_json(progress_path, summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
