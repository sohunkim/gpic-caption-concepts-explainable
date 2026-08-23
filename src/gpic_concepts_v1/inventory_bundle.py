"""Inventory bundle manifest helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from gpic_concepts_v1.atomic_io import atomic_text_writer


BUNDLE_DIR_PATH_BASE = "bundle_dir"


class InventoryBundleError(ValueError):
    """Raised when an inventory bundle is missing required formal inputs."""


@dataclass(frozen=True)
class InventoryBundle:
    path: Path
    object_inventory: Path
    attribute_inventory: Path
    action_inventory: Path
    lexicon_dir: Path
    action_canonical_inventory: Path | None = None


def load_inventory_bundle(path: str | Path) -> InventoryBundle:
    bundle_path = Path(path)
    data = _read_bundle_json(bundle_path)
    artifact_type = str(data.get("artifact_type", ""))
    if artifact_type == "stage35_inventory_workflow":
        return _bundle_from_stage35_state(bundle_path, data)
    if artifact_type == "gpic_inventory_bundle":
        return _bundle_from_bundle_state(bundle_path, data)
    raise InventoryBundleError(
        f"unsupported_inventory_bundle_artifact_type: {artifact_type or '<empty>'} path={bundle_path}"
    )


def build_inventory_bundle_state(
    *,
    object_inventory: str | Path,
    attribute_inventory: str | Path,
    action_inventory: str | Path,
    lexicon_dir: str | Path,
    action_canonical_inventory: str | Path | None = None,
    source_workflow_state: str | Path | None = None,
    bundle_dir: str | Path | None = None,
) -> dict[str, Any]:
    bundle_base = Path(bundle_dir) if bundle_dir is not None else None
    state: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "gpic_inventory_bundle",
        "stage": "3.5-6",
        "status": "complete",
        "preview_mode": False,
        "object_inventory": _serialized_path(object_inventory, bundle_base),
        "attribute_inventory": _serialized_path(attribute_inventory, bundle_base),
        "action_inventory": _serialized_path(action_inventory, bundle_base),
        "lexicon_dir": _serialized_path(lexicon_dir, bundle_base),
    }
    if bundle_base is not None:
        state["path_base"] = BUNDLE_DIR_PATH_BASE
    if action_canonical_inventory is not None:
        state["action_canonical_inventory"] = _serialized_path(
            action_canonical_inventory,
            bundle_base,
        )
    if source_workflow_state is not None:
        state["source_workflow_state"] = str(source_workflow_state)
    return state


def write_inventory_bundle(path: str | Path, state: Mapping[str, Any]) -> None:
    with atomic_text_writer(Path(path), newline="\n") as handle:
        handle.write(json.dumps(dict(state), ensure_ascii=False, indent=2, sort_keys=True))
        handle.write("\n")


def merge_bundle_path(
    *,
    field_name: str,
    explicit_path: str | Path | None,
    bundled_path: Path | None,
) -> Path | None:
    if explicit_path is None:
        return bundled_path
    explicit = Path(explicit_path)
    if bundled_path is None:
        return explicit
    if _same_path(explicit, bundled_path):
        return explicit
    raise InventoryBundleError(
        "inventory_bundle_path_mismatch: "
        f"{field_name} explicit={explicit} bundle={bundled_path}"
    )


def _read_bundle_json(path: Path) -> Mapping[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InventoryBundleError(f"missing_inventory_bundle: {path}") from exc
    except json.JSONDecodeError as exc:
        raise InventoryBundleError(f"invalid_inventory_bundle_json: {path}") from exc
    if not isinstance(data, Mapping):
        raise InventoryBundleError(f"inventory_bundle_must_be_object: {path}")
    return data


def _bundle_from_stage35_state(path: Path, data: Mapping[str, Any]) -> InventoryBundle:
    if data.get("status") != "complete":
        raise InventoryBundleError(f"stage35_workflow_not_complete: {path}")
    object_inventory = _required_path(path, data, "object_inventory")
    attribute_inventory = _required_path(path, data, "attribute_canonical_inventory")
    action_inventory = _required_path(path, data, "action_resolved_inventory")
    lexicon_dir = _required_any_path(path, data, ("lexicon_output_dir", "lexicon_dir"))
    action_canonical = _optional_path(path, data, data.get("action_canonical_inventory"))
    return InventoryBundle(
        path=path,
        object_inventory=object_inventory,
        attribute_inventory=attribute_inventory,
        action_inventory=action_inventory,
        action_canonical_inventory=action_canonical,
        lexicon_dir=lexicon_dir,
    )


def _bundle_from_bundle_state(path: Path, data: Mapping[str, Any]) -> InventoryBundle:
    if data.get("status") != "complete":
        raise InventoryBundleError(f"inventory_bundle_not_complete: {path}")
    return InventoryBundle(
        path=path,
        object_inventory=_required_path(path, data, "object_inventory"),
        attribute_inventory=_required_path(path, data, "attribute_inventory"),
        action_inventory=_required_path(path, data, "action_inventory"),
        action_canonical_inventory=_optional_path(
            path,
            data,
            data.get("action_canonical_inventory"),
        ),
        lexicon_dir=_required_path(path, data, "lexicon_dir"),
    )


def _required_any_path(path: Path, data: Mapping[str, Any], keys: tuple[str, ...]) -> Path:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_path(path, data, value)
    raise InventoryBundleError(f"missing_inventory_bundle_field: {'/'.join(keys)} path={path}")


def _required_path(path: Path, data: Mapping[str, Any], key: str) -> Path:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InventoryBundleError(f"missing_inventory_bundle_field: {key} path={path}")
    return _normalize_path(path, data, value)


def _optional_path(path: Path, data: Mapping[str, Any], value: Any) -> Path | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise InventoryBundleError(f"invalid_inventory_bundle_path_value: {path}")
    return _normalize_path(path, data, value)


def _normalize_path(bundle_path: Path, data: Mapping[str, Any], value: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute() or data.get("path_base") != BUNDLE_DIR_PATH_BASE:
        return candidate
    return bundle_path.parent / candidate


def _serialized_path(value: str | Path, bundle_dir: Path | None) -> str:
    path = Path(value)
    if bundle_dir is None:
        return str(path)
    path_absolute = path.absolute()
    bundle_absolute = bundle_dir.absolute()
    try:
        return path_absolute.relative_to(bundle_absolute).as_posix()
    except ValueError:
        return str(path)


def _same_path(left: Path, right: Path) -> bool:
    return _path_key(left) == _path_key(right)


def _path_key(path: Path) -> str:
    return str(path.absolute()).replace("/", "\\").casefold()
