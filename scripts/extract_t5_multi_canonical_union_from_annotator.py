from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve 8756 attribute surfaces that have multiple explicit T5 "
            "canonical names by unioning 8767 caption IDs. Single-name surfaces "
            "are intentionally omitted because their exact counts come from the "
            "1M canonical-name scan."
        ),
    )
    parser.add_argument("--display-terms-json", required=True, type=Path)
    parser.add_argument("--annotator-url", default="http://127.0.0.1:8767")
    parser.add_argument("--annotator", default="sohun")
    parser.add_argument("--output-json", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    terms = json.loads(args.display_terms_json.read_text(encoding="utf-8"))
    multi_aliases = {
        _exact_key(item["surface"]): tuple(
            sorted({_exact_key(value) for value in item.get("internal_names", []) if value}),
        )
        for item in terms.get("attribute_records", [])
        if len({_exact_key(value) for value in item.get("internal_names", []) if value}) > 1
    }
    requested_pairs = {
        (_exact_key(item["entity"]), _exact_key(item["attribute"]))
        for item in terms.get("pairs", [])
        if _exact_key(item["attribute"]) in multi_aliases
    }
    attribute_caption_ids: dict[str, set[str]] = defaultdict(set)
    pair_caption_ids: dict[tuple[str, str], set[str]] = defaultdict(set)
    term_cache: dict[str, list[dict[str, Any]]] = {}

    for surface, canonical_names in multi_aliases.items():
        for canonical_name in canonical_names:
            instances = term_cache.setdefault(
                canonical_name,
                _fetch_all_instances(
                    args.annotator_url,
                    annotator=args.annotator,
                    term=canonical_name,
                ),
            )
            for instance in instances:
                caption_id = str(instance.get("caption_id") or "")
                if not caption_id:
                    continue
                attribute_caption_ids[surface].add(caption_id)
                entity = _instance_entity_name(instance)
                pair = (entity, surface)
                if pair in requested_pairs:
                    pair_caption_ids[pair].add(caption_id)

    result = {
        "count_basis": "unique_caption_id",
        "attribute_label_policy": "union_of_explicit_8756_internal_canonical_names",
        "generated_variants": False,
        "attribute_canonical_names": {
            surface: list(names) for surface, names in sorted(multi_aliases.items())
        },
        "attribute_caption_counts": {
            surface: len(attribute_caption_ids.get(surface, set()))
            for surface in sorted(multi_aliases)
        },
        "entity_attribute_pair_caption_counts": {
            f"{entity}\t{surface}": len(pair_caption_ids.get((entity, surface), set()))
            for entity, surface in sorted(requested_pairs)
        },
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output_json.with_suffix(args.output_json.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, args.output_json)
    print(json.dumps({"output_json": str(args.output_json), **result}, ensure_ascii=False))
    return 0


def _fetch_all_instances(
    base_url: str,
    *,
    annotator: str,
    term: str,
) -> list[dict[str, Any]]:
    offset = 0
    instances: list[dict[str, Any]] = []
    while True:
        query = urlencode(
            {
                "annotator": annotator,
                "kind": "attribute",
                "term": term,
                "decision": "all",
                "exclude_faces": "0",
                "limit": 100,
                "offset": offset,
            },
        )
        with urlopen(f"{base_url.rstrip('/')}/api/instances?{query}", timeout=30) as response:
            payload = json.load(response)
        page = payload.get("instances") or []
        instances.extend(page)
        offset += len(page)
        total = int(payload.get("total") or 0)
        if not page or offset >= total:
            break
    return instances


def _instance_entity_name(instance: dict[str, Any]) -> str:
    payload = instance.get("payload") or instance.get("entity_json") or {}
    return _exact_key(payload.get("entity_name"))


def _exact_key(value: Any) -> str:
    return str(value or "").strip().lower()


if __name__ == "__main__":
    raise SystemExit(main())
