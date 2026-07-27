from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


REQUIRED_RELATIVE_FILES = (
    Path("inventory_bundle.json"),
    Path("inventory/object_inventory.tsv"),
    Path("inventory/attribute_inventory.tsv"),
    Path("inventory/action_inventory.tsv"),
    Path("inventory/action_inventory.tsv.pipeline_state.json"),
    Path("inventory/action_canonical_inventory.tsv"),
    Path("lexicons/attribute_synonyms.tsv"),
    Path("lexicons/action_synonyms.tsv"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a complete current-inventory transfer archive without a "
            "hand-maintained file list."
        )
    )
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = build_inventory_transfer_archive(
        source_dir=Path(args.source_dir),
        output=Path(args.output),
        manifest=Path(args.manifest),
        repo_root=Path.cwd(),
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


def build_inventory_transfer_archive(
    *,
    source_dir: Path,
    output: Path,
    manifest: Path,
    repo_root: Path,
) -> dict[str, object]:
    source_dir = source_dir.resolve()
    repo_root = repo_root.resolve()
    try:
        source_prefix = source_dir.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError("source-dir must be inside the repository") from exc
    missing = [
        str(relative)
        for relative in REQUIRED_RELATIVE_FILES
        if not (source_dir / relative).is_file()
    ]
    if missing:
        raise ValueError(f"current inventory transfer is missing required files: {missing}")

    files = sorted(
        path
        for path in source_dir.rglob("*")
        if path.is_file()
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(
                path,
                (source_prefix / path.relative_to(source_dir)).as_posix(),
            )

    entries: list[dict[str, object]] = []
    with zipfile.ZipFile(output, "r") as archive:
        names = archive.namelist()
        expected_names = [
            (source_prefix / path.relative_to(source_dir)).as_posix()
            for path in files
        ]
        if names != expected_names:
            raise ValueError("archive entry list differs from source file list")
        for info in archive.infolist():
            entries.append(
                {
                    "path": info.filename,
                    "size": info.file_size,
                    "compressed_size": info.compress_size,
                }
            )

    summary: dict[str, object] = {
        "status": "complete",
        "source_dir": str(source_dir),
        "output": str(output),
        "archive_sha256": _sha256(output),
        "file_count": len(entries),
        "entries": entries,
    }
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
