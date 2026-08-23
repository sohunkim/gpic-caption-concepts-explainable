from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import shutil
import sys
import uuid
from typing import Any, Mapping


ROOT = Path(__file__).absolute().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from incident_gate import guarded_entrypoint

from gpic_concepts_v1.inventory_bundle import (
    build_inventory_bundle_state,
    load_inventory_bundle,
    write_inventory_bundle,
)
from gpic_concepts_v1.inventory_validation import final_manual_resolution_blockers
from gpic_concepts_v1.inventory_publish import (
    inventory_row_counts,
    lexicon_row_counts,
    now_utc,
    write_json_atomic,
)


DEFAULT_TARGET_DIR = ROOT / "resources" / "gpic_inventory" / "current"
COMPONENT_TARGET_NAMES = {
    "object": "object_inventory.tsv",
    "attribute": "attribute_inventory.tsv",
    "action": "action_inventory.tsv",
    "action_canonical": "action_canonical_inventory.tsv",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Publish a completed object inventory into resources/gpic_inventory/current. "
            "Attribute and action changes require a synchronized full-bundle publish."
        ),
    )
    parser.add_argument(
        "--component",
        required=True,
        choices=("object",),
        help="Current inventory component to update; only object is independently publishable.",
    )
    parser.add_argument("--source", required=True, help="Resolved/canonical component TSV to publish.")
    parser.add_argument("--target-dir", default=str(DEFAULT_TARGET_DIR))
    parser.add_argument("--snapshot-label", default="")
    parser.add_argument("--source-stage3-records", default="")
    parser.add_argument("--summary", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = publish_current_inventory_component(
        component=args.component,
        source=Path(args.source),
        target_dir=Path(args.target_dir),
        snapshot_label=args.snapshot_label,
        source_stage3_records=args.source_stage3_records,
        summary_path=Path(args.summary) if args.summary else None,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def publish_current_inventory_component(
    *,
    component: str,
    source: Path,
    target_dir: Path = DEFAULT_TARGET_DIR,
    snapshot_label: str = "",
    source_stage3_records: str = "",
    summary_path: Path | None = None,
) -> dict[str, Any]:
    if component not in COMPONENT_TARGET_NAMES:
        raise ValueError(f"unsupported_component: {component}")
    if component != "object":
        raise ValueError(
            "component_requires_synchronized_lexicon_publish: "
            f"{component}; publish a complete inventory bundle with its matching "
            "Stage 5 lexicon directory via publish_inventory_bundle.py"
        )
    if not source.is_file():
        raise FileNotFoundError(f"missing_component_source: {source}")

    central_bundle = target_dir / "inventory_bundle.json"
    bundle = load_inventory_bundle(central_bundle)
    if component == "object":
        _raise_if_object_blockers(source)

    target_inventory_dir = target_dir / "inventory"
    target_path = target_inventory_dir / COMPONENT_TARGET_NAMES[component]
    _atomic_copy_file(source, target_path)

    object_inventory = bundle.object_inventory
    attribute_inventory = bundle.attribute_inventory
    action_inventory = bundle.action_inventory
    action_canonical_inventory = bundle.action_canonical_inventory

    object_inventory = target_path

    state = build_inventory_bundle_state(
        object_inventory=object_inventory,
        attribute_inventory=attribute_inventory,
        action_inventory=action_inventory,
        action_canonical_inventory=action_canonical_inventory,
        lexicon_dir=bundle.lexicon_dir,
        source_workflow_state=bundle.path,
        bundle_dir=target_dir,
    )
    previous_state = _read_json_object(central_bundle)
    state.update(_preserved_bundle_metadata(previous_state))
    published_at_utc = now_utc()
    component_sources = dict(previous_state.get("component_sources", {}))
    component_sources[component] = {
        "published_at_utc": published_at_utc,
        "snapshot_label": snapshot_label,
        "source": str(source),
        "source_stage3_records": source_stage3_records,
    }
    state.update(
        {
            "published_at_utc": published_at_utc,
            "published_from_component": component,
            "snapshot_label": previous_state.get("snapshot_label", "component_current_mixed"),
            "source_stage3_records": previous_state.get("source_stage3_records", ""),
            "last_component_update": component_sources[component],
            "component_sources": component_sources,
        }
    )
    rows = inventory_row_counts(
        object_inventory=object_inventory,
        attribute_inventory=attribute_inventory,
        action_inventory=action_inventory,
        action_canonical_inventory=action_canonical_inventory,
    )
    state["inventory_rows"] = rows
    write_inventory_bundle(central_bundle, state)

    summary = {
        "status": "published_component",
        "component": component,
        "source": str(source),
        "target": str(target_path),
        "target_bundle": str(central_bundle),
        "snapshot_label": snapshot_label,
        "component_rows": rows["object_inventory"],
        "rows": rows,
        "lexicon_rows": lexicon_row_counts(bundle.lexicon_dir),
    }
    canonical_summary_path = target_dir / "publish_summary.json"
    write_json_atomic(canonical_summary_path, summary)
    if summary_path is not None and summary_path.absolute() != canonical_summary_path.absolute():
        write_json_atomic(summary_path, summary)
    return summary


def _raise_if_object_blockers(path: Path) -> None:
    rows = _read_tsv(path)
    blockers = final_manual_resolution_blockers(
        rows,
        require_canonical_surface_for_selected_synset=True,
    )
    if blockers:
        examples = [
            f"{row.get('span_key', '')}:{row.get('decision_status', '')}"
            for row in blockers[:20]
        ]
        raise ValueError(
            "object component has unresolved blockers and cannot be published: "
            + ", ".join(examples)
        )


def _preserved_bundle_metadata(previous_state: Mapping[str, Any]) -> dict[str, Any]:
    preserved: dict[str, Any] = {}
    for key in (
        "preview_mode",
        "stage",
        "status",
        "published_from_bundle",
        "source_workflow_state",
    ):
        if key in previous_state:
            preserved[key] = previous_state[key]
    return preserved


def _atomic_copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with source.open("rb") as src, temp.open("xb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(temp, target)
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [dict(row) for row in reader]


def _read_json_object(path: Path) -> Mapping[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError(f"bundle_state_must_be_object: {path}")
    return data


if __name__ == "__main__":
    raise SystemExit(guarded_entrypoint("publish_current_inventory_component", main))
