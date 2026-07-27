from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from incident_gate import guarded_entrypoint

from gpic_concepts_v1.attribute_units import (
    ATTRIBUTE_MWE_RULE_VERSION,
    ATTRIBUTE_UNIT_MWE,
)
from gpic_concepts_v1.inventory_bundle import (
    build_inventory_bundle_state,
    load_inventory_bundle,
    write_inventory_bundle,
)
from gpic_concepts_v1.pipeline_state import artifact_state_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build an immutable full inventory bundle by replacing only the "
            "attribute inventory and its matching Stage 5 lexicons."
        )
    )
    parser.add_argument("--base-bundle", required=True)
    parser.add_argument("--attribute-inventory", required=True)
    parser.add_argument("--lexicon-dir", required=True)
    parser.add_argument("--verification", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--snapshot-label", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = stage_attribute_mwe_inventory_bundle(
        base_bundle_path=Path(args.base_bundle),
        attribute_inventory=Path(args.attribute_inventory),
        lexicon_dir=Path(args.lexicon_dir),
        verification_path=Path(args.verification),
        output_dir=Path(args.output_dir),
        snapshot_label=args.snapshot_label,
    )
    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def stage_attribute_mwe_inventory_bundle(
    *,
    base_bundle_path: Path,
    attribute_inventory: Path,
    lexicon_dir: Path,
    verification_path: Path,
    output_dir: Path,
    snapshot_label: str = "",
) -> dict[str, Any]:
    verification = _read_json(verification_path)
    if verification.get("status") != "ok" or verification.get("failures"):
        raise ValueError(f"attribute_mwe_verification_not_clear: {verification_path}")

    base = load_inventory_bundle(base_bundle_path)
    for path in (
        base.object_inventory,
        base.action_inventory,
        attribute_inventory,
        verification_path,
    ):
        _require_file(path)
    if base.action_canonical_inventory is not None:
        _require_file(base.action_canonical_inventory)
    if not lexicon_dir.is_dir():
        raise FileNotFoundError(f"missing_lexicon_dir: {lexicon_dir}")

    attribute_rows = _read_tsv(attribute_inventory)
    mwe_rows = _validated_mwe_rows(attribute_rows)
    chosen_mwe_rows = [
        row for row in mwe_rows if row.get("decision_status") == "chosen"
    ]
    excluded_mwe_rows = [
        row for row in mwe_rows if row.get("decision_status") == "excluded"
    ]
    expected_mwe_rows = int(verification.get("mwe_inventory_rows", -1))
    if expected_mwe_rows < 0 or len(chosen_mwe_rows) != expected_mwe_rows:
        raise ValueError(
            "verification_mwe_row_count_mismatch: "
            f"verification={expected_mwe_rows} inventory={len(chosen_mwe_rows)}"
        )
    _validate_lexicons(lexicon_dir, attribute_inventory, chosen_mwe_rows)

    if output_dir.exists():
        raise FileExistsError(f"staging_output_already_exists: {output_dir}")
    temp_dir = output_dir.with_name(
        f".{output_dir.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    inventory_dir = temp_dir / "inventory"
    staged_lexicon_dir = temp_dir / "lexicons"
    try:
        inventory_dir.mkdir(parents=True)
        staged_object = inventory_dir / "object_inventory.tsv"
        staged_attribute = inventory_dir / "attribute_inventory.tsv"
        staged_action = inventory_dir / "action_inventory.tsv"
        staged_action_canonical = (
            inventory_dir / "action_canonical_inventory.tsv"
            if base.action_canonical_inventory is not None
            else None
        )

        _copy_file(base.object_inventory, staged_object)
        _copy_file(attribute_inventory, staged_attribute)
        _copy_file(base.action_inventory, staged_action)
        action_state = artifact_state_path(base.action_inventory)
        _require_file(action_state)
        _copy_file(action_state, artifact_state_path(staged_action))
        if base.action_canonical_inventory is not None and staged_action_canonical is not None:
            _copy_file(base.action_canonical_inventory, staged_action_canonical)
        shutil.copytree(lexicon_dir, staged_lexicon_dir)

        preserved_hashes = {
            "object_inventory": _assert_same_hash(base.object_inventory, staged_object),
            "action_inventory": _assert_same_hash(base.action_inventory, staged_action),
        }
        if base.action_canonical_inventory is not None and staged_action_canonical is not None:
            preserved_hashes["action_canonical_inventory"] = _assert_same_hash(
                base.action_canonical_inventory,
                staged_action_canonical,
            )
        _assert_same_hash(attribute_inventory, staged_attribute)

        bundle_path = temp_dir / "inventory_bundle.json"
        state = build_inventory_bundle_state(
            object_inventory=staged_object,
            attribute_inventory=staged_attribute,
            action_inventory=staged_action,
            action_canonical_inventory=staged_action_canonical,
            lexicon_dir=staged_lexicon_dir,
            source_workflow_state=verification_path,
            bundle_dir=temp_dir,
        )
        state.update(
            {
                "snapshot_label": snapshot_label,
                "attribute_mwe_rule_version": ATTRIBUTE_MWE_RULE_VERSION,
                "attribute_mwe_verification": str(verification_path),
                "base_bundle": str(base_bundle_path),
            }
        )
        write_inventory_bundle(bundle_path, state)
        os.replace(temp_dir, output_dir)
    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise

    return {
        "status": "staged",
        "bundle": str(output_dir / "inventory_bundle.json"),
        "base_bundle": str(base_bundle_path),
        "attribute_inventory": str(attribute_inventory),
        "attribute_rows": len(attribute_rows),
        "mwe_rows": len(mwe_rows),
        "chosen_mwe_rows": len(chosen_mwe_rows),
        "excluded_mwe_rows": len(excluded_mwe_rows),
        "single_token_rows": len(attribute_rows) - len(mwe_rows),
        "lexicon_dir": str(lexicon_dir),
        "verification": str(verification_path),
        "preserved_component_hashes": preserved_hashes,
        "snapshot_label": snapshot_label,
    }


def _validated_mwe_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    required_columns = {
        "attribute_unit_type",
        "span_token_count",
        "anchor_token_offset",
        "lookup_forms",
        "attribute_mwe_rule_version",
    }
    if rows and not required_columns.issubset(rows[0]):
        missing = sorted(required_columns - set(rows[0]))
        raise ValueError(f"attribute_mwe_schema_missing_columns: {missing}")

    mwe_rows = [
        row for row in rows if row.get("attribute_unit_type", "") == ATTRIBUTE_UNIT_MWE
    ]
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row.get("attribute_unit_type", "single_token"), row.get("span_key", ""))
        if key in seen:
            raise ValueError(f"duplicate_attribute_inventory_key: {key}")
        seen.add(key)
    for row in mwe_rows:
        surface = row.get("span_key", "")
        if row.get("attribute_mwe_rule_version") != ATTRIBUTE_MWE_RULE_VERSION:
            raise ValueError(f"stale_attribute_mwe_rule_version: {surface}")
        if int(row.get("span_token_count", "0")) < 2:
            raise ValueError(f"attribute_mwe_invalid_token_count: {surface}")
        status = row.get("decision_status")
        if status == "chosen":
            if not row.get("selected_oewn_synset"):
                raise ValueError(f"attribute_mwe_missing_synset: {surface}")
            if not row.get("canonical_surface"):
                raise ValueError(f"attribute_mwe_missing_canonical: {surface}")
        elif status == "excluded":
            if row.get("selected_oewn_synset"):
                raise ValueError(f"excluded_attribute_mwe_has_synset: {surface}")
            if row.get("canonical_surface"):
                raise ValueError(f"excluded_attribute_mwe_has_canonical: {surface}")
        else:
            raise ValueError(f"unresolved_attribute_mwe: {surface}")
    return mwe_rows


def _validate_lexicons(
    lexicon_dir: Path,
    attribute_inventory: Path,
    mwe_rows: list[dict[str, str]],
) -> None:
    state = _read_json(lexicon_dir / "pipeline_state.json")
    if state.get("status") != "ready" or state.get("preview_mode") is not False:
        raise ValueError(f"stage5_lexicon_not_formal_ready: {lexicon_dir}")
    state_inventory = Path(str(state.get("attribute_inventory", "")))
    if _path_key(state_inventory) != _path_key(attribute_inventory):
        raise ValueError(
            "stage5_lexicon_attribute_inventory_mismatch: "
            f"state={state_inventory} expected={attribute_inventory}"
        )

    synonyms_path = lexicon_dir / "attribute_synonyms.tsv"
    synonym_rows = _read_tsv(synonyms_path)
    mappings: dict[str, set[str]] = {}
    for row in synonym_rows:
        raw = row.get("raw_attribute", row.get("raw", ""))
        canonical = row.get("canonical_attribute", row.get("canonical", ""))
        if raw:
            mappings.setdefault(raw, set()).add(canonical)
    for row in mwe_rows:
        raw = row["span_key"]
        canonical = row["canonical_surface"]
        if mappings.get(raw) != {canonical}:
            raise ValueError(
                "attribute_mwe_lexicon_mapping_mismatch: "
                f"raw={raw!r} expected={canonical!r} actual={sorted(mappings.get(raw, set()))}"
            )


def _read_tsv(path: Path) -> list[dict[str, str]]:
    _require_file(path)
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle, delimiter="\t")]


def _read_json(path: Path) -> dict[str, Any]:
    _require_file(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"json_must_be_object: {path}")
    return data


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _assert_same_hash(source: Path, target: Path) -> str:
    source_hash = _sha256(source)
    target_hash = _sha256(target)
    if source_hash != target_hash:
        raise ValueError(f"staged_component_hash_mismatch: {source} {target}")
    return source_hash


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_key(path: Path) -> str:
    return str(path.resolve()).replace("/", "\\").casefold()


def _require_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)


if __name__ == "__main__":
    raise SystemExit(guarded_entrypoint("stage_attribute_mwe_inventory_bundle", main))
