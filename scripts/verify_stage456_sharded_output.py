from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify the completed artifact contract of run_stage456_sharded.py."
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--expected-table-count", type=int, default=12)
    parser.add_argument(
        "--require-report-helper",
        action="store_true",
        help=(
            "Also require patient_action_agent_triple_counts.tsv, which is built "
            "from Stage 5 after the standard 12 Stage 6 count tables."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = verify_stage456_sharded_output(
        Path(args.output_dir),
        expected_table_count=args.expected_table_count,
        require_report_helper=args.require_report_helper,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def verify_stage456_sharded_output(
    output_dir: Path,
    *,
    expected_table_count: int,
    require_report_helper: bool = False,
) -> dict[str, Any]:
    summary_path = output_dir / "summary.json"
    merged_dir = output_dir / "stage6_merged"
    if not summary_path.is_file():
        raise ValueError(f"missing sharded summary: {summary_path}")
    if not merged_dir.is_dir():
        raise ValueError(f"missing sharded Stage6 directory: {merged_dir}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("status") != "completed":
        raise ValueError(f"sharded run is not completed: {summary.get('status')!r}")
    stage6 = summary.get("stage6_merged")
    if not isinstance(stage6, dict):
        raise ValueError("summary is missing stage6_merged")
    table_paths = stage6.get("table_paths")
    row_counts = stage6.get("table_row_counts")
    if not isinstance(table_paths, dict) or not isinstance(row_counts, dict):
        raise ValueError("stage6_merged is missing table paths or row counts")
    if len(table_paths) != expected_table_count:
        raise ValueError(
            f"unexpected Stage6 table count: expected={expected_table_count} "
            f"actual={len(table_paths)}"
        )
    if set(table_paths) != set(row_counts):
        raise ValueError("Stage6 table path/count keys differ")
    missing_or_empty = []
    for filename in sorted(table_paths):
        path = merged_dir / filename
        if not path.is_file() or path.stat().st_size <= 0:
            missing_or_empty.append(filename)
    if missing_or_empty:
        raise ValueError(f"missing or empty Stage6 tables: {missing_or_empty}")
    report_helper_path = merged_dir / "patient_action_agent_triple_counts.tsv"
    if require_report_helper and (
        not report_helper_path.is_file() or report_helper_path.stat().st_size <= 0
    ):
        raise ValueError(
            "missing report helper: patient_action_agent_triple_counts.tsv; "
            "build it with scripts/build_patient_action_agent_triples_from_stage5.py"
        )
    return {
        "status": "complete",
        "output_dir": str(output_dir),
        "summary": str(summary_path),
        "stage6_dir": str(merged_dir),
        "table_count": len(table_paths),
        "table_row_counts": row_counts,
        "report_helper": (
            str(report_helper_path)
            if report_helper_path.is_file() and report_helper_path.stat().st_size > 0
            else None
        ),
        "timing_seconds": summary.get("timing_seconds", {}),
    }


if __name__ == "__main__":
    main()
