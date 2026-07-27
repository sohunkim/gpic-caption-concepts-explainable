from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from gpic_concepts_v1.attribute_units import ATTRIBUTE_MWE_RULE_VERSION
from gpic_concepts_v1.inventory_bundle import load_inventory_bundle
from gpic_concepts_v1.pipeline_state import artifact_state_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build an immutable pre-verification Stage 4-6 execution candidate "
            "without modifying the published current inventory."
        ),
    )
    parser.add_argument("--base-bundle", required=True)
    parser.add_argument("--attribute-inventory", required=True)
    parser.add_argument("--lexicon-dir", required=True)
    parser.add_argument("--preposition-mwe-lexicon", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--snapshot-label", required=True)
    parser.add_argument("--summary", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = build_candidate(
        base_bundle_path=Path(args.base_bundle),
        attribute_inventory=Path(args.attribute_inventory),
        lexicon_dir=Path(args.lexicon_dir),
        preposition_mwe_lexicon=Path(args.preposition_mwe_lexicon),
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


def build_candidate(
    *,
    base_bundle_path: Path,
    attribute_inventory: Path,
    lexicon_dir: Path,
    preposition_mwe_lexicon: Path,
    output_dir: Path,
    snapshot_label: str,
) -> dict[str, object]:
    base = load_inventory_bundle(base_bundle_path)
    required_files = [
        base.object_inventory,
        base.action_inventory,
        attribute_inventory,
        preposition_mwe_lexicon,
        artifact_state_path(base.action_inventory),
    ]
    if base.action_canonical_inventory is not None:
        required_files.append(base.action_canonical_inventory)
    for path in required_files:
        if not path.is_file():
            raise FileNotFoundError(path)
    if not lexicon_dir.is_dir():
        raise FileNotFoundError(lexicon_dir)
    if output_dir.exists():
        raise FileExistsError(output_dir)

    temp_dir = output_dir.with_name(
        f".{output_dir.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    inventory_dir = temp_dir / "inventory"
    staged_lexicons = temp_dir / "lexicons"
    try:
        inventory_dir.mkdir(parents=True)
        staged_object = inventory_dir / "object_inventory.tsv"
        staged_attribute = inventory_dir / "attribute_inventory.tsv"
        staged_action = inventory_dir / "action_inventory.tsv"
        shutil.copy2(base.object_inventory, staged_object)
        shutil.copy2(attribute_inventory, staged_attribute)
        shutil.copy2(base.action_inventory, staged_action)
        shutil.copy2(
            artifact_state_path(base.action_inventory),
            artifact_state_path(staged_action),
        )

        staged_action_canonical: Path | None = None
        if base.action_canonical_inventory is not None:
            staged_action_canonical = inventory_dir / "action_canonical_inventory.tsv"
            shutil.copy2(base.action_canonical_inventory, staged_action_canonical)

        shutil.copytree(lexicon_dir, staged_lexicons)
        staged_prepositions = temp_dir / "preposition_mwes.tsv"
        shutil.copy2(preposition_mwe_lexicon, staged_prepositions)

        manifest = {
            "schema_version": 1,
            "artifact_type": "gpic_inventory_bundle_candidate",
            "stage": "candidate-before-stage456-verification",
            "status": "candidate",
            "preview_mode": False,
            "path_base": "bundle_dir",
            "object_inventory": "inventory/object_inventory.tsv",
            "attribute_inventory": "inventory/attribute_inventory.tsv",
            "action_inventory": "inventory/action_inventory.tsv",
            "lexicon_dir": "lexicons",
            "preposition_mwe_lexicon": "preposition_mwes.tsv",
            "snapshot_label": snapshot_label,
            "attribute_mwe_rule_version": ATTRIBUTE_MWE_RULE_VERSION,
        }
        if staged_action_canonical is not None:
            manifest["action_canonical_inventory"] = (
                "inventory/action_canonical_inventory.tsv"
            )
        (temp_dir / "inventory_bundle.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_dir, output_dir)
    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise

    hashes = {
        path.relative_to(output_dir).as_posix(): _sha256(path)
        for path in sorted(output_dir.rglob("*"))
        if path.is_file()
    }
    return {
        "status": "candidate",
        "output_dir": str(output_dir),
        "snapshot_label": snapshot_label,
        "base_bundle": str(base_bundle_path),
        "attribute_inventory": str(attribute_inventory),
        "lexicon_dir": str(lexicon_dir),
        "preposition_mwe_lexicon": str(preposition_mwe_lexicon),
        "file_count": len(hashes),
        "file_hashes": hashes,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
