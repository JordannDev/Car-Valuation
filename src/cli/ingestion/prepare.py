"""Save the source, cleaned-up data, and manifest needed to resume an import."""

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path

import polars as pl

from reader import Config, build
from reader.reader import check_structure
from reader.schema import COLUMNS


def digest(path: Path) -> str:
    """Hash a file in pieces so it does not have to fit in memory."""

    result = hashlib.sha256()

    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)

    return result.hexdigest()


def prepare(
    source: Path, work_dir: Path | None, config: Config
) -> tuple[Path, Path, Path, str, int, str]:
    """Save and clean up a source file, rejecting it if it changes during preparation."""

    # Create a separate preparation directory for this run so its files cannot replace
    # an earlier run.
    root = (
        (work_dir or Path.home() / ".local/share/valueapi/imports")
        .expanduser()
        .resolve()
    )
    root.mkdir(parents=True, exist_ok=True)
    source = source.expanduser().resolve(strict=True)

    # Hash the source before and after copying it to catch changes during preparation.
    before = digest(source)
    batch_dir = Path(tempfile.mkdtemp(prefix="prepare-", dir=root))
    retained = batch_dir / "source.csv"
    shutil.copyfile(source, retained)
    if digest(source) != before or digest(retained) != before:
        shutil.rmtree(batch_dir)
        raise ValueError("Source changed while it was being retained")

    retained.chmod(0o444)

    # Validate the saved copy and build the pipeline from it, not from a source file
    # that may keep changing.
    check_structure(retained, config)
    frame = build(retained, config)
    expected = {"source_record_number", *COLUMNS}
    missing = expected - set(frame.collect_schema().names())
    if missing:
        raise ValueError(
            f"Reader did not produce required import fields: {sorted(missing)}"
        )

    # Keep original values and issue flags so reports can explain what happened to each
    # row.
    artifact = batch_dir / "normalized.parquet"
    settings = asdict(config)
    settings["enabled"] = sorted(config.enabled)
    frame.sink_parquet(
        artifact,
        engine="streaming",
        metadata={
            "normalization_config": json.dumps(settings, sort_keys=True),
            "source_file_hash": before,
            "record_number_convention": "header=1; first data record=2",
        },
    )

    # Make the prepared data read-only, then check that the original source still
    # matches.
    artifact.chmod(0o444)
    if digest(source) != before:
        shutil.rmtree(batch_dir)
        raise ValueError("Source changed during normalization")

    total = pl.scan_parquet(artifact).select(pl.len()).collect().item()
    rules_hash = implementation_hash(settings)
    manifest = batch_dir / "manifest.json"

    return retained, artifact, manifest, before, total, rules_hash


def implementation_hash(settings: dict[str, object]) -> str:
    """Hash the effective settings and every reader module that can change the resulting
    rows.
    """

    root = Path(__file__).resolve().parents[1] / "reader"

    # Hash file contents as well as settings; even comment edits change this resume
    # fingerprint.
    hasher = hashlib.sha256(json.dumps(settings, sort_keys=True).encode())

    for path in sorted(root.rglob("*.py")):
        hasher.update(path.relative_to(root).as_posix().encode())
        hasher.update(path.read_bytes())

    from app import importing

    # Row mapping and save rules also affect how resumed rows are interpreted, so
    # include them in the hash.
    api_root = Path(importing.__file__).parent

    for path in [Path(__file__).with_name("rows.py"), *sorted(api_root.glob("*.py"))]:
        hasher.update(path.name.encode())
        hasher.update(path.read_bytes())

    return hasher.hexdigest()


def atomic_manifest(path: Path, data: dict[str, object]) -> None:
    """Write the full manifest before renaming it into place, without replacing an existing
    manifest.
    """

    # Flush the complete file before linking it into place. The link fails if a manifest
    # already exists.
    temporary = path.with_suffix(".tmp")

    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, sort_keys=True, indent=2)
        stream.flush()
        os.fsync(stream.fileno())

    os.link(temporary, path)
    temporary.unlink()
    path.chmod(0o444)
