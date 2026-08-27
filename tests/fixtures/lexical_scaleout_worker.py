"""CPU-only pipeline substitute; exercise the real spawned worker and receipts."""

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src")]


def worker(gpu_id, tasks, events, settings, pause):
    control_path = Path(settings.output_root) / "fixture_control.json"
    if control_path.exists() and json.loads(control_path.read_text()).get("exit_early"):
        os._exit(0)
    import run_fixed_lexicon_scaleout as scaleout
    import run_mixed_caption_pipeline as mixed
    from planned_pause import request_pause

    def pipeline(**kwargs):
        output = kwargs["output_dir"]
        root = Path(settings.output_root)
        control_path = root / "fixture_control.json"
        control = json.loads(control_path.read_text()) if control_path.exists() else {}
        output.mkdir(parents=True, exist_ok=True)
        with (root / (output.name + ".calls")).open("a", encoding="utf-8") as handle:
            handle.write(gpu_id + "\n")
        if output.name in control.get("pause", []):
            request_pause(root)
        if output.name in control.get("fail", []):
            raise RuntimeError("fixture pipeline failure after pause request")
        rows = [json.loads(line) for path in kwargs["input_paths"]
                for line in Path(path).read_text().splitlines()]
        files = {
            "mixed_pipeline_summary.jsonl": json.dumps({"status": "completed", "stage1": {"total": len(rows)}}),
            "pipeline_state.json": "{}",
            "stage5/canonical_mentions.jsonl": "\n".join(json.dumps(row) for row in rows),
            "stage6/summary.jsonl": "{}",
            "stage6/objects.tsv": "id\tcount\n" + "\n".join(f"{row['id']}\t1" for row in rows),
        }
        for name, text in files.items():
            path = output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text + "\n", encoding="utf-8")

    mixed.run_mixed_caption_pipeline = pipeline
    scaleout._worker_main(gpu_id, tasks, events, settings, pause)
