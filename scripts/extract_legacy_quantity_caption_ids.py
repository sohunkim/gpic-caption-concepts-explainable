from __future__ import annotations

import argparse
import json
import os
import re
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

LEGACY_QUANTITY_FACT_TYPES = {"quantity_exists", "has_quantity"}


def normalize_surface(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", " ")
    return re.sub(r"\s+", " ", text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract unique caption IDs for requested legacy Stage 6 quantity "
            "labels and object-quantity pairs."
        ),
    )
    parser.add_argument("--display-terms-json", required=True, type=Path)
    parser.add_argument("--facts-jsonl", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--progress-json", required=True, type=Path)
    parser.add_argument("--progress-every", type=int, default=1_000_000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    terms = json.loads(args.display_terms_json.read_text(encoding="utf-8"))
    wanted_attributes = {
        normalize_surface(value)
        for value in terms.get("attributes", [])
        if normalize_surface(value)
    }
    wanted_pairs = {
        (
            normalize_surface(item.get("entity")),
            normalize_surface(item.get("attribute")),
        )
        for item in terms.get("pairs", [])
        if normalize_surface(item.get("entity"))
        and normalize_surface(item.get("attribute"))
    }
    result = extract_legacy_quantity_caption_ids(
        facts_path=args.facts_jsonl,
        wanted_attributes=wanted_attributes,
        wanted_pairs=wanted_pairs,
        progress_path=args.progress_json,
        progress_every=args.progress_every,
    )
    _write_json(args.output_json, result)
    print(json.dumps(result["summary"], ensure_ascii=False), flush=True)
    return 0


def extract_legacy_quantity_caption_ids(
    *,
    facts_path: Path,
    wanted_attributes: set[str],
    wanted_pairs: set[tuple[str, str]],
    progress_path: Path | None = None,
    progress_every: int = 1_000_000,
) -> dict[str, Any]:
    started = time.monotonic()
    attribute_ids: dict[str, set[str]] = defaultdict(set)
    pair_ids: dict[str, set[str]] = defaultdict(set)
    lines_scanned = 0
    relevant_facts = 0
    malformed_lines = 0

    with facts_path.open("rb") as handle:
        for raw_line in handle:
            lines_scanned += 1
            if b'"quantity_exists"' not in raw_line and b'"has_quantity"' not in raw_line:
                if progress_path is not None and lines_scanned % progress_every == 0:
                    _write_progress(
                        progress_path,
                        status="running",
                        lines_scanned=lines_scanned,
                        relevant_facts=relevant_facts,
                        malformed_lines=malformed_lines,
                        started=started,
                    )
                continue
            try:
                fact = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                malformed_lines += 1
                continue
            fact_type = str(fact.get("fact_type") or "")
            if fact_type not in LEGACY_QUANTITY_FACT_TYPES:
                continue
            values = fact.get("values")
            if not isinstance(values, dict):
                continue
            caption_id = str(fact.get("caption_id") or "").strip()
            quantity = normalize_surface(values.get("quantity"))
            if not caption_id or not quantity:
                continue
            relevant_facts += 1
            if fact_type == "quantity_exists" and quantity in wanted_attributes:
                attribute_ids[quantity].add(caption_id)
            elif fact_type == "has_quantity":
                entity = normalize_surface(values.get("object"))
                pair = (entity, quantity)
                if pair in wanted_pairs:
                    pair_ids[f"{entity}\t{quantity}"].add(caption_id)

            if progress_path is not None and lines_scanned % progress_every == 0:
                _write_progress(
                    progress_path,
                    status="running",
                    lines_scanned=lines_scanned,
                    relevant_facts=relevant_facts,
                    malformed_lines=malformed_lines,
                    started=started,
                )

    elapsed = time.monotonic() - started
    payload = {
        "count_basis": "unique_caption_id",
        "source_fact_types": sorted(LEGACY_QUANTITY_FACT_TYPES),
        "attribute_caption_ids": {
            key: sorted(value) for key, value in sorted(attribute_ids.items())
        },
        "entity_attribute_pair_caption_ids": {
            key: sorted(value) for key, value in sorted(pair_ids.items())
        },
        "summary": {
            "status": "complete",
            "facts_jsonl": str(facts_path),
            "lines_scanned": lines_scanned,
            "relevant_facts": relevant_facts,
            "malformed_lines": malformed_lines,
            "attribute_labels_found": len(attribute_ids),
            "pair_labels_found": len(pair_ids),
            "elapsed_seconds": elapsed,
        },
    }
    if progress_path is not None:
        _write_json(progress_path, payload["summary"])
    return payload


def _write_progress(
    path: Path,
    *,
    status: str,
    lines_scanned: int,
    relevant_facts: int,
    malformed_lines: int,
    started: float,
) -> None:
    _write_json(
        path,
        {
            "status": status,
            "lines_scanned": lines_scanned,
            "relevant_facts": relevant_facts,
            "malformed_lines": malformed_lines,
            "elapsed_seconds": time.monotonic() - started,
        },
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
