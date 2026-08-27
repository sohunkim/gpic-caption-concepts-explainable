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
from gpic_concepts_v1.cli_memory import add_memory_safety_args, memory_safety_kwargs

from gpic_concepts_v1.stage3_annotate import (
    DEFAULT_STAGE3_BATCH_SIZE,
    DEFAULT_STAGE3_DISABLED_COMPONENTS,
    DEFAULT_STAGE3_MODEL,
    normalize_stage3_disabled_components,
    run_stage3_annotate,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run v1 Stage 3 annotation over Stage 1 sentence rows.",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Input sentence_rows.jsonl or tag_rows.jsonl from Stage 1.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output stage3_records.jsonl path.",
    )
    parser.add_argument(
        "--summary",
        help="Optional output JSONL path for one Stage 3 summary row.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_STAGE3_MODEL,
        help=f"spaCy model name. Default: {DEFAULT_STAGE3_MODEL}",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional maximum number of sentence rows to process.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_STAGE3_BATCH_SIZE,
        help=f"spaCy nlp.pipe batch size. Default: {DEFAULT_STAGE3_BATCH_SIZE}",
    )
    parser.add_argument(
        "--disable-components",
        default=",".join(DEFAULT_STAGE3_DISABLED_COMPONENTS),
        help=(
            "Comma-separated spaCy pipeline components to disable for Stage 3. "
            f"Default: {','.join(DEFAULT_STAGE3_DISABLED_COMPONENTS)}."
        ),
    )
    parser.add_argument(
        "--caption-shape",
        choices=("sentence", "tag_list"),
        default="sentence",
        help="Input row shape. Use tag_list for Stage 1 tag rows.",
    )
    parser.add_argument(
        "--progress-output",
        help="Optional JSON path updated while Stage 3 annotation runs.",
    )
    parser.add_argument(
        "--progress-interval-records",
        type=int,
        default=5000,
        help="Record interval for progress JSON updates. Default: 5000.",
    )
    gpu_group = parser.add_mutually_exclusive_group()
    gpu_group.add_argument(
        "--prefer-gpu",
        action="store_true",
        help="Use spaCy GPU if CuPy/CUDA is available; continue on CPU otherwise.",
    )
    gpu_group.add_argument(
        "--require-gpu",
        action="store_true",
        help="Require spaCy GPU; fail early if CuPy/CUDA is unavailable.",
    )
    add_memory_safety_args(parser, stage_name="Stage 3")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    gpu_mode = "require" if args.require_gpu else "prefer" if args.prefer_gpu else "none"
    summary = run_stage3_annotate(
        args.input,
        output_path=args.output,
        summary_path=args.summary,
        model=args.model,
        limit=args.limit,
        batch_size=args.batch_size,
        gpu_mode=gpu_mode,
        disabled_components=normalize_stage3_disabled_components(args.disable_components),
        caption_shape=args.caption_shape,
        progress_output=args.progress_output,
        progress_interval_records=args.progress_interval_records,
        memory_kwargs=memory_safety_kwargs(args),
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(guarded_entrypoint("stage3_annotate", main))
