from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).absolute().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from incident_gate import guarded_entrypoint

from gpic_concepts_v1.inventory_bundle import load_inventory_bundle, write_inventory_bundle
from gpic_concepts_v1.inventory_publish import (
    inventory_row_counts,
    lexicon_row_counts,
    now_utc,
    repoint_stage5_lexicon_state,
    write_json_atomic,
)


DEFAULT_TARGET_DIR = ROOT / "resources" / "gpic_inventory" / "current"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh row counts and local provenance pointers for an existing "
            "current inventory bundle without replacing inventory content."
        )
    )
    parser.add_argument("--target-dir", type=Path, default=DEFAULT_TARGET_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = refresh_current_inventory_metadata(args.target_dir)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def refresh_current_inventory_metadata(target_dir: Path) -> dict[str, Any]:
    bundle_path = target_dir / "inventory_bundle.json"
    bundle = load_inventory_bundle(bundle_path)
    for path in (
        bundle.object_inventory,
        bundle.attribute_inventory,
        bundle.action_inventory,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)
    if bundle.action_canonical_inventory is not None:
        if not bundle.action_canonical_inventory.is_file():
            raise FileNotFoundError(bundle.action_canonical_inventory)
    if not bundle.lexicon_dir.is_dir():
        raise FileNotFoundError(bundle.lexicon_dir)

    refreshed_at = now_utc()
    repoint_stage5_lexicon_state(
        lexicon_dir=bundle.lexicon_dir,
        attribute_inventory=bundle.attribute_inventory,
        action_canonical_inventory=bundle.action_canonical_inventory,
        published_at_utc=refreshed_at,
    )
    rows = inventory_row_counts(
        object_inventory=bundle.object_inventory,
        attribute_inventory=bundle.attribute_inventory,
        action_inventory=bundle.action_inventory,
        action_canonical_inventory=bundle.action_canonical_inventory,
    )
    state = _read_json_object(bundle_path)
    if _same_path_text(state.get("source_workflow_state"), bundle_path):
        published_from_bundle = state.get("published_from_bundle")
        if isinstance(published_from_bundle, str) and published_from_bundle.strip():
            state["source_workflow_state"] = published_from_bundle
    state["inventory_rows"] = rows
    state["metadata_refreshed_at_utc"] = refreshed_at
    write_inventory_bundle(bundle_path, state)

    summary = {
        "status": "metadata_refreshed",
        "target_bundle": str(bundle_path),
        "target_dir": str(target_dir),
        "snapshot_label": state.get("snapshot_label", ""),
        "published_at_utc": state.get("published_at_utc", ""),
        "metadata_refreshed_at_utc": refreshed_at,
        "rows": rows,
        "lexicon_rows": lexicon_row_counts(bundle.lexicon_dir),
    }
    write_json_atomic(target_dir / "publish_summary.json", summary)
    return summary


def _read_json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError(f"bundle_state_must_be_object: {path}")
    return dict(data)


def _same_path_text(value: Any, path: Path) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    return str(Path(value).absolute()).replace("/", "\\").casefold() == str(
        path.absolute()
    ).replace("/", "\\").casefold()


if __name__ == "__main__":
    raise SystemExit(guarded_entrypoint("refresh_current_inventory_metadata", main))
