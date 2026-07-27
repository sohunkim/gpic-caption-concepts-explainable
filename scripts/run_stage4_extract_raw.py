from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from incident_gate import guarded_entrypoint

from gpic_concepts_v1.inventory_validation import (
    final_manual_resolution_blockers,
    normalize_inventory_decision_status,
    read_inventory_rows,
)
from gpic_concepts_v1.cli_memory import add_memory_safety_args, memory_safety_kwargs
from gpic_concepts_v1.pipeline_state import (
    PipelineStateError,
    artifact_state_path,
    require_action_inventory_sidecar_state,
)
from gpic_concepts_v1.stage4_extract_raw import (
    _load_object_lookup_runtime,
    load_gpic_action_inventory,
    load_gpic_attribute_mwe_inventory,
    load_gpic_object_inventory,
    load_preposition_mwe_lexicon,
    run_stage4_extract_raw,
)
from run_stage5_canonicalize import _raise_if_attribute_inventory_not_ready


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run v1 Stage 4 raw concept extraction over Stage 3 records.",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Input stage3_records.jsonl.",
    )
    parser.add_argument(
        "--raw-mentions",
        required=True,
        help="Output raw_mentions.jsonl path.",
    )
    parser.add_argument(
        "--raw-edges",
        required=True,
        help="Output raw_edges.jsonl path.",
    )
    parser.add_argument(
        "--summary",
        help="Optional output JSONL path for one Stage 4 summary row.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional maximum number of Stage 3 rows to process.",
    )
    parser.add_argument(
        "--object-inventory",
        help=(
            "GPIC-observed object span inventory TSV. This must be generated "
            "from GPIC captions, not external source-label datasets."
        ),
    )
    parser.add_argument(
        "--attribute-inventory",
        help=(
            "Resolved GPIC-observed attribute inventory TSV. Formal Stage 4 "
            "uses its MWE rows as the attribute span matcher."
        ),
    )
    parser.add_argument(
        "--action-inventory",
        help=(
            "Resolved GPIC-observed action inventory TSV. Rows with "
            "decision_status=needs_manual block Stage 4."
        ),
    )
    parser.add_argument(
        "--preposition-mwe-lexicon",
        help=(
            "Optional active preposition MWE TSV. If omitted, Stage 4 uses "
            "resources/lexicons/preposition_mwes.tsv when it exists."
        ),
    )
    parser.add_argument(
        "--allow-runtime-oewn-lookup",
        action="store_true",
        help=(
            "Probe/debug mode only: allow Stage 4 to use live OEWN lookup "
            "instead of a GPIC observed inventory."
        ),
    )
    parser.add_argument(
        "--allow-runtime-action-lookup-preview",
        action="store_true",
        help=(
            "Preview/debug mode only: allow Stage 4 to use live OEWN verb "
            "lookup instead of a resolved GPIC observed action inventory."
        ),
    )
    add_memory_safety_args(parser, stage_name="Stage 4")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.action_inventory and not args.allow_runtime_action_lookup_preview:
        raise SystemExit(
            "--action-inventory is required for formal Stage 4 extraction. "
            "Use --allow-runtime-action-lookup-preview only for OEWN "
            "action lookup preview/debug runs."
        )
    if not args.attribute_inventory:
        raise SystemExit(
            "--attribute-inventory is required for formal Stage 4 extraction."
        )
    if args.object_inventory:
        object_inventory_path = Path(args.object_inventory)
        _raise_if_object_inventory_not_ready(object_inventory_path)
        object_lookup = load_gpic_object_inventory(object_inventory_path)
    elif args.allow_runtime_oewn_lookup:
        object_lookup = None
    else:
        raise SystemExit(
            "--object-inventory is required for Stage 4 extraction. "
            "Use --allow-runtime-oewn-lookup only for OEWN probe/debug runs."
        )
    if args.action_inventory:
        action_inventory_path = Path(args.action_inventory)
        _raise_if_action_inventory_not_ready(action_inventory_path)
        action_lookup = load_gpic_action_inventory(action_inventory_path)
    elif args.allow_runtime_action_lookup_preview:
        action_lookup = _load_object_lookup_runtime()
    attribute_inventory_path = Path(args.attribute_inventory)
    _raise_if_attribute_inventory_not_ready(attribute_inventory_path)
    attribute_mwe_lookup = load_gpic_attribute_mwe_inventory(
        attribute_inventory_path
    )
    preposition_mwe_lookup = (
        load_preposition_mwe_lexicon(Path(args.preposition_mwe_lexicon))
        if args.preposition_mwe_lexicon
        else None
    )
    summary = run_stage4_extract_raw(
        args.input,
        raw_mentions_path=args.raw_mentions,
        raw_edges_path=args.raw_edges,
        summary_path=args.summary,
        limit=args.limit,
        object_lookup=object_lookup,
        attribute_mwe_lookup=attribute_mwe_lookup,
        action_lookup=action_lookup,
        preposition_mwe_lookup=preposition_mwe_lookup,
        **memory_safety_kwargs(args),
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def _raise_if_object_inventory_not_ready(path: Path) -> None:
    rows = read_inventory_rows(path)
    blockers = final_manual_resolution_blockers(
        rows,
        require_canonical_surface_for_selected_synset=True,
    )
    if not blockers:
        return
    raise SystemExit(
        json.dumps(
            {
                "status": "blocked_object_inventory_before_stage4",
                "object_inventory": str(path),
                "rows": len(rows),
                "blocked_rows": len(blockers),
                "blocked_examples": blockers[:10],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def _raise_if_action_inventory_not_ready(path: Path) -> None:
    try:
        action_state = require_action_inventory_sidecar_state(path)
    except PipelineStateError as exc:
        raise SystemExit(
            json.dumps(
                {
                    "status": "blocked_action_inventory_pipeline_state_before_stage4",
                    "action_inventory": str(path),
                    "pipeline_state": str(artifact_state_path(path)),
                    "blocker_reason": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        ) from exc
    rows = read_inventory_rows(path)
    blockers: list[dict[str, str]] = []
    for row in rows:
        explicit_status = row.get("decision_status", "").strip()
        selected_synset = row.get("selected_oewn_synset", "").strip()
        reason = ""
        if explicit_status == "raw_fallback":
            if selected_synset:
                reason = "raw_fallback_must_not_have_selected_synset"
        else:
            status = normalize_inventory_decision_status(row)
            if status == "needs_manual":
                reason = "pending_manual_decision_status"
            elif status == "chosen" and not selected_synset:
                reason = "chosen_action_missing_selected_synset"
        if not reason:
            continue
        blockers.append(
            {
                "blocker_reason": reason,
                "span_key": row.get("span_key", ""),
                "observed_surface": row.get("observed_surface", ""),
                "decision_status": row.get("decision_status", ""),
                "decision_reason": row.get("decision_reason", ""),
                "selected_query": row.get("selected_query", ""),
                "selected_oewn_synset": row.get("selected_oewn_synset", ""),
                "synset_selection_tag": row.get("synset_selection_tag", ""),
            }
        )
    if not blockers:
        return
    raise SystemExit(
        json.dumps(
            {
                "status": "blocked_action_inventory_before_stage4",
                "action_inventory": str(path),
                "pipeline_state_status": action_state.get("status", ""),
                "rows": len(rows),
                "blocked_rows": len(blockers),
                "blocked_examples": blockers[:10],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    raise SystemExit(guarded_entrypoint("stage4_extract_raw", main))
