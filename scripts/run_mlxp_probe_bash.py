from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from run_mlxp_bash import (  # noqa: E402
    DEFAULT_KUBECTL,
    DEFAULT_NAMESPACE,
    DEFAULT_POD_ENV,
    DEFAULT_POD_PREFIX_ENV,
    DEFAULT_POD,
    _normalize_bash_newlines,
    _preflight_pod_running,
    _prepend_mlxp_runtime_env,
    _resolve_target_pod,
    _strip_utf_bom,
)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a bounded read-only MLXP probe script without opening an "
            "incident on transient observation failures. Formal pipeline work "
            "must still use scripts/run_mlxp_bash.py."
        ),
    )
    parser.add_argument("script", type=Path)
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument(
        "--pod",
        default=DEFAULT_POD,
        help=f"Target MLXP pod. Required unless {DEFAULT_POD_ENV} is set.",
    )
    parser.add_argument(
        "--pod-prefix",
        default=None,
        help=(
            "Resolve the single currently Running pod whose name starts with this "
            f"prefix. Can also be set with {DEFAULT_POD_PREFIX_ENV}."
        ),
    )
    parser.add_argument("--kubectl", default=DEFAULT_KUBECTL)
    parser.add_argument("--timeout-seconds", type=int, default=60)
    parser.add_argument(
        "--no-runtime-env",
        action="store_true",
        help="Do not prepend the GPIC MLXP runtime LD_LIBRARY_PATH guard.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.timeout_seconds < 1:
        raise SystemExit("--timeout-seconds must be greater than zero")
    pod = _resolve_target_pod(
        explicit_pod=args.pod or os.environ.get(DEFAULT_POD_ENV),
        pod_prefix=args.pod_prefix or os.environ.get(DEFAULT_POD_PREFIX_ENV),
        namespace=args.namespace,
        kubectl=args.kubectl,
    )
    if not args.script.exists():
        raise SystemExit(f"missing script: {args.script}")
    if not args.script.name.startswith("probe_"):
        raise SystemExit(
            "run_mlxp_probe_bash.py only accepts probe_*.sh scripts; "
            "use run_mlxp_bash.py for formal remote work."
        )

    payload = _normalize_bash_newlines(_strip_utf_bom(args.script.read_bytes()))
    if not args.no_runtime_env:
        payload = _prepend_mlxp_runtime_env(payload)
    pod_preflight_returncode = _preflight_pod_running(
        kubectl=args.kubectl,
        namespace=args.namespace,
        pod=pod,
    )
    if pod_preflight_returncode != 0:
        return pod_preflight_returncode
    command = [
        "wsl",
        "-e",
        args.kubectl,
        "-n",
        args.namespace,
        "exec",
        "-i",
        pod,
        "--",
        "bash",
        "-s",
    ]
    try:
        completed = subprocess.run(command, input=payload, timeout=args.timeout_seconds)
    except subprocess.TimeoutExpired:
        print(
            f"probe timed out after {args.timeout_seconds}s: {args.script}",
            file=sys.stderr,
        )
        return 124
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
