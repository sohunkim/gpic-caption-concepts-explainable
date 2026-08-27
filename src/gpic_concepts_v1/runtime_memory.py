"""Runtime memory safety helpers for large pipeline stages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
import time
from typing import Any

from gpic_concepts_v1.atomic_io import atomic_text_writer


@dataclass(frozen=True, slots=True)
class MemorySafetyConfig:
    max_rss_gib: float | None = None
    memory_limit_gib: float | None = None
    rss_limit_fraction: float = 0.75
    rss_reserve_gib: float = 16.0
    memory_check_min_interval_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.memory_check_min_interval_seconds < 0:
            raise ValueError("memory_check_min_interval_seconds must be non-negative")

    @property
    def resolved_memory_limit_gib(self) -> float | None:
        if self.memory_limit_gib is not None:
            return self.memory_limit_gib
        return detect_cgroup_memory_limit_gib() or detect_system_memory_gib()

    @property
    def memory_limit_source(self) -> str:
        if self.memory_limit_gib is not None:
            return "explicit"
        if detect_cgroup_memory_limit_gib() is not None:
            return "cgroup"
        if self.resolved_memory_limit_gib is not None:
            return "system"
        return "unbounded_or_unavailable"

    @property
    def effective_max_rss_gib(self) -> float | None:
        return effective_max_rss_gib(
            explicit_max_rss_gib=self.max_rss_gib,
            memory_limit_gib=self.resolved_memory_limit_gib,
            rss_limit_fraction=self.rss_limit_fraction,
            rss_reserve_gib=self.rss_reserve_gib,
        )


class ProgressWriter:
    """Write a single JSON progress/checkpoint file by atomic replace."""

    def __init__(
        self,
        path: str | Path | None,
        *,
        stage_name: str,
        memory_config: MemorySafetyConfig,
    ) -> None:
        self._path = Path(path) if path is not None else None
        self.stage_name = stage_name
        self.memory_config = memory_config
        self.started_at = time.time()
        self._last_memory_check_at: float | None = None

    def write(
        self,
        *,
        status: str,
        phase: str,
        note: str,
        metrics: Mapping[str, Any] | None = None,
        outputs: Mapping[str, str | Path] | None = None,
        summary: Mapping[str, Any] | None = None,
    ) -> None:
        if self._path is None:
            return
        now = time.time()
        rss_kib = current_rss_kib()
        payload: dict[str, Any] = {
            "status": status,
            "stage": self.stage_name,
            "phase": phase,
            "note": note,
            "started_at_epoch": self.started_at,
            "updated_at_epoch": now,
            "elapsed_seconds": round(now - self.started_at, 3),
            "current_rss_kib": rss_kib,
            "current_rss_gib": (
                round(rss_kib / 1024 / 1024, 3)
                if rss_kib is not None
                else None
            ),
            "memory_limit_gib": self.memory_config.resolved_memory_limit_gib,
            "memory_limit_source": self.memory_config.memory_limit_source,
            "rss_limit_fraction": self.memory_config.rss_limit_fraction,
            "rss_reserve_gib": self.memory_config.rss_reserve_gib,
            "max_rss_gib": self.memory_config.effective_max_rss_gib,
            "memory_check_min_interval_seconds": (
                self.memory_config.memory_check_min_interval_seconds
            ),
        }
        if metrics:
            payload.update(dict(metrics))
        if outputs:
            payload["outputs"] = {
                name: {
                    "path": str(path),
                    "bytes": path_size(Path(path)),
                }
                for name, path in outputs.items()
            }
        if summary is not None:
            payload["summary"] = dict(summary)
        with atomic_text_writer(self._path) as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")

    def check_memory(
        self,
        *,
        phase: str,
        metrics: Mapping[str, Any] | None = None,
        force: bool = False,
    ) -> None:
        now = time.monotonic()
        interval = self.memory_config.memory_check_min_interval_seconds
        if (
            not force
            and interval > 0
            and self._last_memory_check_at is not None
            and now - self._last_memory_check_at < interval
        ):
            return
        raise_if_rss_limit_exceeded(
            max_rss_gib=self.memory_config.effective_max_rss_gib,
            context={"stage": self.stage_name, "phase": phase, **dict(metrics or {})},
        )
        self._last_memory_check_at = now


def effective_max_rss_gib(
    *,
    explicit_max_rss_gib: float | None,
    memory_limit_gib: float | None,
    rss_limit_fraction: float,
    rss_reserve_gib: float,
) -> float | None:
    if explicit_max_rss_gib is not None:
        if explicit_max_rss_gib <= 0:
            raise ValueError("--max-rss-gib must be greater than zero")
        return explicit_max_rss_gib
    if memory_limit_gib is None:
        return None
    if memory_limit_gib <= 0:
        raise ValueError("--memory-limit-gib must be greater than zero")
    if not 0 < rss_limit_fraction <= 1:
        raise ValueError("--rss-limit-fraction must be > 0 and <= 1")
    if rss_reserve_gib < 0:
        raise ValueError("--rss-reserve-gib must be non-negative")
    fraction_limit = memory_limit_gib * rss_limit_fraction
    reserve_limit = memory_limit_gib - rss_reserve_gib
    effective_limit = min(fraction_limit, reserve_limit)
    if effective_limit <= 0:
        raise ValueError(
            "computed RSS limit is not positive; lower --rss-reserve-gib "
            "or provide a larger --memory-limit-gib",
        )
    return effective_limit


def raise_if_rss_limit_exceeded(
    *,
    max_rss_gib: float | None,
    context: Mapping[str, Any] | None = None,
) -> None:
    if max_rss_gib is None:
        return
    rss_kib = current_rss_kib()
    if rss_kib is None:
        return
    rss_gib = rss_kib / 1024 / 1024
    if rss_gib <= max_rss_gib:
        return
    details = ", ".join(
        f"{key}={value}"
        for key, value in sorted(dict(context or {}).items())
    )
    raise MemoryError(
        "RSS safety limit exceeded before OOM: "
        f"rss_gib={rss_gib:.3f}, max_rss_gib={max_rss_gib:.3f}"
        + (f", {details}" if details else ""),
    )


def current_rss_kib() -> int | None:
    if os.name == "nt":
        import ctypes
        get_memory, process, counter_type = _windows_rss_reader()
        counters = counter_type()
        counters.cb = ctypes.sizeof(counters)
        if get_memory(process, ctypes.byref(counters), counters.cb):
            return counters.WorkingSetSize // 1024
        return None
    status_path = Path("/proc/self/status")
    if status_path.exists():
        for line in status_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("VmRSS:"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return int(parts[1])
    return None


@lru_cache(maxsize=1)
def _windows_rss_reader():
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
            )
        ]

    get_process = ctypes.windll.kernel32.GetCurrentProcess
    get_process.restype = wintypes.HANDLE
    get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
    get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    return get_memory, get_process(), Counters


@lru_cache(maxsize=1)
def detect_system_memory_gib() -> float | None:
    """Fallback for non-container hosts; a cgroup limit always takes priority."""
    if os.name == "nt":
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in (
                    "total", "available", "page_total", "page_available",
                    "virtual_total", "virtual_available", "extended",
                )
            ]

        status = Status()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return status.total / 1024**3
        return None
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1024**3
    except (AttributeError, OSError, ValueError):
        return None


def memory_config_from_kwargs(kwargs: Mapping[str, Any] | None = None) -> MemorySafetyConfig:
    return MemorySafetyConfig(**{
        name: value for name, value in (kwargs or {}).items()
        if name in MemorySafetyConfig.__dataclass_fields__
    })


def child_memory_kwargs(
    kwargs: Mapping[str, Any] | None, workers: int, *, separate_processes: bool = True,
) -> dict[str, Any]:
    """Divide one process-tree RSS budget, not the entire pod limit per child."""
    if workers < 1:
        raise ValueError("workers must be positive")
    config = memory_config_from_kwargs(kwargs)
    budget = config.effective_max_rss_gib
    result = dict(kwargs or {})
    if budget is None:
        raise ValueError("memory budget unavailable; provide --memory-limit-gib or --max-rss-gib")
    if separate_processes:
        parent_rss = current_rss_kib()
        if parent_rss is None:
            raise ValueError("parent RSS unavailable; cannot allocate a process-tree memory budget")
        budget -= parent_rss / 1024**2
    if budget <= 0:
        raise MemoryError("parent already exhausts the process-tree RSS budget")
    result["max_rss_gib"] = budget / workers
    return result


@lru_cache(maxsize=1)
def detect_cgroup_memory_limit_gib() -> float | None:
    """Return the Linux cgroup memory limit in GiB when one is configured.

    Kubernetes exposes the pod/container limit through cgroup files. This is
    the relevant bound for OOM safety; host-wide `free -h` can be much larger
    and is not a safe limit for a pod.
    """
    candidates = (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    )
    for path in candidates:
        if not path.exists():
            continue
        raw_value = path.read_text(encoding="utf-8", errors="replace").strip()
        if not raw_value or raw_value == "max":
            continue
        try:
            limit_bytes = int(raw_value)
        except ValueError:
            continue
        # cgroup v1 sometimes reports a near-LONG_MAX value for "unlimited".
        if limit_bytes <= 0 or limit_bytes >= 2**60:
            continue
        return limit_bytes / 1024**3
    return None


def path_size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return None
