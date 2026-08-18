from __future__ import annotations

import argparse
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Print runtime evidence for CPU/GPU, PyTorch CUDA, spaCy GPU, "
            "CuPy, and optional spaCy model loading."
        ),
    )
    parser.add_argument(
        "--spacy-model",
        default=None,
        help="Optional spaCy model name to load for verification.",
    )
    parser.add_argument(
        "--require-spacy-gpu",
        action="store_true",
        help="Run spacy.require_gpu() and report the result.",
    )
    parser.add_argument(
        "--nvidia-smi",
        action="store_true",
        help="Run nvidia-smi if it is available on PATH.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report: dict[str, Any] = {
        "scope": "current Python environment",
        "python": {
            "executable": sys.executable,
            "version": sys.version,
            "platform": platform.platform(),
        },
        "process": {
            "cwd": str(Path.cwd()),
            "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES"),
        },
        "torch": _check_torch(),
        "cupy": _check_cupy(),
        "spacy": _check_spacy(
            model_name=args.spacy_model,
            require_gpu=args.require_spacy_gpu,
        ),
    }
    if args.nvidia_smi:
        report["nvidia_smi"] = _check_nvidia_smi()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(_exit_code_from_report(report, args))


def _exit_code_from_report(report: dict[str, Any], args: argparse.Namespace) -> int:
    spacy_report = report.get("spacy")
    if not isinstance(spacy_report, dict):
        return 1 if args.require_spacy_gpu or args.spacy_model else 0
    if args.require_spacy_gpu and spacy_report.get("require_gpu") is not True:
        return 1
    if args.spacy_model:
        model_report = spacy_report.get("model")
        if not isinstance(model_report, dict) or model_report.get("loaded") is not True:
            return 1
    return 0


def _check_torch() -> dict[str, Any]:
    result: dict[str, Any] = {"installed": False}
    try:
        import torch  # type: ignore[import-not-found]
    except Exception as exc:
        result["import_error"] = repr(exc)
        return result

    result["installed"] = True
    result["version"] = getattr(torch, "__version__", None)
    cuda = getattr(torch, "cuda", None)
    if cuda is None:
        result["cuda_available"] = False
        result["cuda_error"] = "torch.cuda is unavailable"
        return result

    try:
        cuda_available = bool(cuda.is_available())
        result["cuda_available"] = cuda_available
        result["cuda_version"] = getattr(torch.version, "cuda", None)
        result["device_count"] = int(cuda.device_count()) if cuda_available else 0
        devices = []
        for index in range(result["device_count"]):
            devices.append(
                {
                    "index": index,
                    "name": cuda.get_device_name(index),
                }
            )
        result["devices"] = devices
    except Exception as exc:
        result["cuda_available"] = False
        result["cuda_error"] = repr(exc)
    return result


def _check_cupy() -> dict[str, Any]:
    result: dict[str, Any] = {"installed": False}
    try:
        import cupy  # type: ignore[import-not-found]
    except Exception as exc:
        result["import_error"] = repr(exc)
        return result

    result["installed"] = True
    result["version"] = getattr(cupy, "__version__", None)
    try:
        runtime = cupy.cuda.runtime
        result["runtime_device_count"] = int(runtime.getDeviceCount())
        result["runtime_version"] = int(runtime.runtimeGetVersion())
    except Exception as exc:
        result["runtime_error"] = repr(exc)
    return result


def _check_spacy(
    *,
    model_name: str | None,
    require_gpu: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {"installed": False}
    try:
        import spacy  # type: ignore[import-not-found]
    except Exception as exc:
        result["import_error"] = repr(exc)
        return result

    result["installed"] = True
    result["version"] = getattr(spacy, "__version__", None)
    try:
        result["prefer_gpu"] = bool(spacy.prefer_gpu())
    except Exception as exc:
        result["prefer_gpu_error"] = repr(exc)

    if require_gpu:
        try:
            spacy.require_gpu()
            result["require_gpu"] = True
        except Exception as exc:
            result["require_gpu"] = False
            result["require_gpu_error"] = repr(exc)
    else:
        result["require_gpu"] = "not_requested"

    if model_name:
        try:
            nlp = spacy.load(model_name, disable=["ner"])
            result["model"] = {
                "name": model_name,
                "loaded": True,
                "pipe_names": list(nlp.pipe_names),
            }
        except Exception as exc:
            result["model"] = {
                "name": model_name,
                "loaded": False,
                "load_error": repr(exc),
            }
    return result


def _check_nvidia_smi() -> dict[str, Any]:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return {"available": False}
    try:
        completed = subprocess.run(
            [executable, "--query-gpu=index,name,memory.total", "--format=csv,noheader"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return {
            "available": True,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    except Exception as exc:
        return {"available": True, "error": repr(exc)}


if __name__ == "__main__":
    main()
