from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.request import urlopen


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract the visible Canary analysis entity/attribute terms from the "
            "8756 HTML page. Attribute labels use the display surface first, "
            "not the internal canonical name."
        ),
    )
    parser.add_argument("--canary-url", required=True)
    parser.add_argument("--output-json", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    html = urlopen(args.canary_url, timeout=20).read().decode("utf-8", "replace")
    match = re.search(r"const D = (.*?);\n", html, flags=re.S)
    if not match:
        raise SystemExit("Could not find `const D = ...;` canary data in HTML.")
    data = json.loads(match.group(1))

    entities = [str(item["name"]).strip().lower() for item in data["concepts"]]
    attributes: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    examples: dict[tuple[str, str], list[str]] = defaultdict(list)
    attribute_names: dict[str, set[str]] = defaultdict(set)

    for row in data["canaries"]:
        entity = str(row["concept"]).strip().lower()
        for attr in (row.get("sg") or {}).get("attributes") or []:
            label = _visible_attribute_label(attr)
            if not label:
                continue
            attributes.add(label)
            name = str(attr.get("name") or "").strip().lower()
            if name:
                attribute_names[label].add(name)
            pairs.add((entity, label))
            key = (entity, label)
            if len(examples[key]) < 3:
                examples[key].append(str(row.get("id") or ""))

    out = {
        "source": args.canary_url,
        "attribute_label_policy": "surface_first_visible_8756_chip_label",
        "entities": entities,
        "attributes": sorted(attributes),
        "attribute_records": [
            {
                "surface": surface,
                "internal_names": sorted(attribute_names[surface]),
            }
            for surface in sorted(attributes)
        ],
        "pairs": [
            {
                "entity": entity,
                "attribute": attribute,
                "attribute_internal_names": sorted(attribute_names[attribute]),
                "example_ids": examples[(entity, attribute)],
            }
            for entity, attribute in sorted(pairs)
        ],
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output_json": str(args.output_json),
                "entities": len(entities),
                "attributes": len(attributes),
                "pairs": len(pairs),
                "has_illuminated": "illuminated" in attributes,
                "has_illuminate": "illuminate" in attributes,
            },
            ensure_ascii=False,
        ),
    )
    return 0


def _visible_attribute_label(attr: dict[str, object]) -> str:
    value = attr.get("surface") or attr.get("name") or ""
    return str(value).strip().lower()


if __name__ == "__main__":
    raise SystemExit(main())
