"""CPU-only subprocess exercising the followup's pause forwarding contract."""
import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src")]
from planned_pause import PauseControl

parser = argparse.ArgumentParser()
parser.add_argument("--output-root", type=Path, required=True)
parser.add_argument("--fail-after-pause", action="store_true")
args = parser.parse_args()
control = PauseControl.start(args.output_root, "fixture")
deadline = time.monotonic() + 10
while not control.requested():
    if time.monotonic() > deadline:
        raise RuntimeError("test did not forward a pause within ten seconds")
    time.sleep(0.01)
if args.fail_after_pause:
    control.finish("failed")
    raise SystemExit(7)
control.finish("paused")
