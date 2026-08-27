"""Bounded synthetic RSS/spill check; does not load models or production data."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gpic_concepts_v1.atomic_io import atomic_text_writer
from gpic_concepts_v1.count_merge_store import CountMergeStore
from gpic_concepts_v1.runtime_memory import MemorySafetyConfig, current_rss_kib


def run(output: Path, *, rows: int, headroom_mib: int) -> dict:
    baseline = current_rss_kib()
    if baseline is None:
        raise RuntimeError("RSS measurement is required for this diagnostic")
    budget = baseline / 1024**2 + headroom_mib / 1024
    started = time.monotonic()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="merge-rss-probe-", dir=output.parent) as directory:
        with CountMergeStore(Path(directory) / "probe.tsv", value_fields=("object",),
                            memory_config=MemorySafetyConfig(
                                max_rss_gib=budget, memory_check_min_interval_seconds=0.01,
                            )) as store:
            for repeat in range(2):
                for index in range(rows):
                    key = f"key-{index:09d}"
                    row = store.row(key, {"object": key})
                    row.count += 1
                    row.caption_count += 1
                    row.example_caption_ids.add(f"c{repeat}")
                    row.rule_ids.add("R25")
                    row.pipe_field_values.setdefault("raw_variants", set()).add("z|a")
                    store.after_row()
            digest = hashlib.sha256()
            seen = 0
            for index, (key, row) in enumerate(store.sorted_rows()):
                if key != f"key-{index:09d}" or row.count != 2 or row.caption_count != 2:
                    raise AssertionError("missing/duplicate/out-of-order aggregate")
                if row.example_caption_ids != {"c0", "c1"} or row.pipe_field_values["raw_variants"] != {"z|a"}:
                    raise AssertionError("evidence changed across cache flush")
                digest.update(f"{key}\t{row.count}\t{row.caption_count}\n".encode())
                seen += 1
            if seen != rows or store.spills < 2:
                raise AssertionError("probe must exercise multiple real RSS-triggered spills")
            result = {"status": "ok", "rows": seen, "input_rows": rows * 2,
                      "sha256": digest.hexdigest(), "memory": store.complete(),
                      "start_rss_kib": baseline, "end_rss_kib": current_rss_kib(),
                      "diagnostic_headroom_mib": headroom_mib,
                      "elapsed_seconds": time.monotonic() - started}
            if sys.platform.startswith("linux"):
                import resource
                result["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                if result["peak_rss_kib"] / 1024**2 > budget:
                    raise AssertionError("diagnostic peak RSS exceeded assigned budget")
    with atomic_text_writer(output) as handle:
        json.dump(result, handle, sort_keys=True, indent=2)
        handle.write("\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=100000)
    parser.add_argument("--headroom-mib", type=int, default=32)
    args = parser.parse_args()
    if args.rows < 1 or args.headroom_mib < 1:
        parser.error("rows and diagnostic headroom must be positive")
    print(json.dumps(run(args.output, rows=args.rows, headroom_mib=args.headroom_mib), sort_keys=True))
