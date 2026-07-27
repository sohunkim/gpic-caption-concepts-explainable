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
    _preflight_pod_running,
    _preflight_wsl_access,
    _resolve_target_pod,
)
from run_mlxp_cp import _to_wsl_path  # noqa: E402


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Copy a file from the active MLXP pod to a local path through WSL "
            "kubectl. Use --pod-prefix to avoid stale exact pod names."
        ),
    )
    parser.add_argument("remote_source")
    parser.add_argument("local_destination", type=Path)
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument(
        "--pod",
        default=None,
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
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if ":" in args.remote_source:
        raise SystemExit("remote_source must be a path inside the pod, not pod:/path")

    destination = args.local_destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    pod = _resolve_target_pod(
        explicit_pod=args.pod or os.environ.get(DEFAULT_POD_ENV),
        pod_prefix=args.pod_prefix or os.environ.get(DEFAULT_POD_PREFIX_ENV),
        namespace=args.namespace,
        kubectl=args.kubectl,
    )
    wsl_preflight = _preflight_wsl_access()
    if wsl_preflight != 0:
        return wsl_preflight
    pod_preflight = _preflight_pod_running(
        kubectl=args.kubectl,
        namespace=args.namespace,
        pod=pod,
    )
    if pod_preflight != 0:
        return pod_preflight

    wsl_destination = _to_wsl_path(destination)
    command = [
        "wsl",
        "-e",
        args.kubectl,
        "-n",
        args.namespace,
        "cp",
        f"{pod}:{args.remote_source}",
        wsl_destination,
    ]
    completed = subprocess.run(command)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
