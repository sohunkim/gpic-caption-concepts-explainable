from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import uuid
from typing import Any

ROOT = Path(__file__).absolute().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from incident_gate import guarded_entrypoint

from gpic_concepts_v1.atomic_io import atomic_text_writer
from gpic_concepts_v1.inventory_bundle import (
    build_inventory_bundle_state,
    load_inventory_bundle,
    write_inventory_bundle,
)
from gpic_concepts_v1.inventory_publish import (
    inventory_row_counts,
    lexicon_row_counts,
    repoint_stage5_lexicon_state,
    write_json_atomic,
)
from gpic_concepts_v1.pipeline_state import artifact_state_path
from gpic_concepts_v1.pipeline_state import require_stage5_lexicon_bundle_state


DEFAULT_TARGET_DIR = ROOT / "resources" / "gpic_inventory" / "current"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Publish a completed Stage 3.5 inventory bundle into the stable "
            "current GPIC inventory location."
        )
    )
    parser.add_argument("--source-bundle", required=True)
    parser.add_argument("--target-dir", default=str(DEFAULT_TARGET_DIR))
    parser.add_argument("--snapshot-label", default="")
    parser.add_argument("--source-stage3-records", default="")
    parser.add_argument("--summary", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = publish_inventory_bundle(
        source_bundle=Path(args.source_bundle),
        target_dir=Path(args.target_dir),
        snapshot_label=args.snapshot_label,
        source_stage3_records=args.source_stage3_records,
        summary_path=Path(args.summary) if args.summary else None,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def publish_inventory_bundle(
    *,
    source_bundle: Path,
    target_dir: Path,
    snapshot_label: str = "",
    source_stage3_records: str = "",
    summary_path: Path | None = None,
) -> dict[str, Any]:
    bundle = load_inventory_bundle(source_bundle)
    target_bundle = target_dir / "inventory_bundle.json"
    target_lexicon_dir = target_dir / "lexicons"
    if _same_path(source_bundle, target_bundle) or _same_path(
        bundle.lexicon_dir,
        target_lexicon_dir,
    ):
        raise ValueError(
            "refuse_in_place_bundle_publish: source and target resolve to the "
            "same current bundle; use refresh_current_inventory_metadata.py "
            "for metadata-only repair"
        )
    _require_file(bundle.object_inventory, "object_inventory")
    _require_file(bundle.attribute_inventory, "attribute_inventory")
    _require_file(bundle.action_inventory, "action_inventory")
    action_inventory_state = _require_sidecar(bundle.action_inventory, "action_inventory")
    if bundle.action_canonical_inventory is not None:
        _require_file(bundle.action_canonical_inventory, "action_canonical_inventory")
    if not bundle.lexicon_dir.is_dir():
        raise FileNotFoundError(f"missing_lexicon_dir: {bundle.lexicon_dir}")
    require_stage5_lexicon_bundle_state(bundle.lexicon_dir)

    target_inventory_dir = target_dir / "inventory"
    object_inventory = target_inventory_dir / "object_inventory.tsv"
    attribute_inventory = target_inventory_dir / "attribute_inventory.tsv"
    action_inventory = target_inventory_dir / "action_inventory.tsv"
    action_canonical_inventory = (
        target_inventory_dir / "action_canonical_inventory.tsv"
        if bundle.action_canonical_inventory is not None
        else None
    )

    _atomic_copy_file(bundle.object_inventory, object_inventory)
    _atomic_copy_file(bundle.attribute_inventory, attribute_inventory)
    _atomic_copy_file(bundle.action_inventory, action_inventory)
    _copy_pipeline_state(
        action_inventory_state,
        artifact_state_path(action_inventory),
        replacements={"output": str(action_inventory)},
    )
    if bundle.action_canonical_inventory is not None and action_canonical_inventory is not None:
        _atomic_copy_file(bundle.action_canonical_inventory, action_canonical_inventory)

    _replace_tree(bundle.lexicon_dir, target_lexicon_dir)
    published_at_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    repoint_stage5_lexicon_state(
        lexicon_dir=target_lexicon_dir,
        attribute_inventory=attribute_inventory,
        action_canonical_inventory=action_canonical_inventory,
        published_from_lexicon_dir=bundle.lexicon_dir,
        published_at_utc=published_at_utc,
    )

    rows = inventory_row_counts(
        object_inventory=object_inventory,
        attribute_inventory=attribute_inventory,
        action_inventory=action_inventory,
        action_canonical_inventory=action_canonical_inventory,
    )

    central_bundle = target_dir / "inventory_bundle.json"
    state = build_inventory_bundle_state(
        object_inventory=object_inventory,
        attribute_inventory=attribute_inventory,
        action_inventory=action_inventory,
        action_canonical_inventory=action_canonical_inventory,
        lexicon_dir=target_lexicon_dir,
        source_workflow_state=bundle.path,
        bundle_dir=target_dir,
    )
    state.update(
        {
            "published_at_utc": published_at_utc,
            "published_from_bundle": str(source_bundle),
            "snapshot_label": snapshot_label,
            "source_stage3_records": source_stage3_records,
            "inventory_rows": rows,
        }
    )
    write_inventory_bundle(central_bundle, state)

    summary = {
        "status": "published",
        "target_bundle": str(central_bundle),
        "snapshot_label": snapshot_label,
        "published_at_utc": published_at_utc,
        "rows": rows,
        "lexicon_rows": lexicon_row_counts(target_lexicon_dir),
        "source_bundle": str(source_bundle),
        "target_dir": str(target_dir),
    }
    canonical_summary_path = target_dir / "publish_summary.json"
    write_json_atomic(canonical_summary_path, summary)
    if summary_path is not None and summary_path.absolute() != canonical_summary_path.absolute():
        write_json_atomic(summary_path, summary)
    return summary


def _require_file(path: Path, field_name: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"missing_{field_name}: {path}")


def _same_path(left: Path, right: Path) -> bool:
    return str(left.absolute()).replace("/", "\\").casefold() == str(
        right.absolute()
    ).replace("/", "\\").casefold()


def _require_sidecar(path: Path, field_name: str) -> Path:
    state_path = artifact_state_path(path)
    if not state_path.is_file():
        raise FileNotFoundError(f"missing_{field_name}_pipeline_state: {state_path}")
    return state_path


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


def _copy_pipeline_state(source: Path, target: Path, *, replacements: dict[str, str]) -> None:
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"pipeline_state_must_be_object: {source}")
    data.update(replacements)
    with atomic_text_writer(target, newline="\n") as handle:
        handle.write(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")


def _replace_tree(source_dir: Path, target_dir: Path) -> None:
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = target_dir.with_name(f".{target_dir.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    backup_dir = target_dir.with_name(
        f".{target_dir.name}.{os.getpid()}.{uuid.uuid4().hex}.backup"
    )
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    shutil.copytree(source_dir, temp_dir)
    moved_existing = False
    if target_dir.exists():
        _assert_safe_replace_tree(target_dir)
        os.replace(target_dir, backup_dir)
        moved_existing = True
    try:
        os.replace(temp_dir, target_dir)
    except Exception:
        if moved_existing and backup_dir.exists() and not target_dir.exists():
            os.replace(backup_dir, target_dir)
        raise
    else:
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def _assert_safe_replace_tree(path: Path) -> None:
    key = str(path.absolute()).replace("/", "\\").casefold()
    root_key = str(ROOT.absolute()).replace("/", "\\").casefold()
    if not key.startswith(root_key + "\\"):
        raise ValueError(f"refuse_to_replace_tree_outside_repo: {path}")


if __name__ == "__main__":
    raise SystemExit(guarded_entrypoint("publish_inventory_bundle", main))
