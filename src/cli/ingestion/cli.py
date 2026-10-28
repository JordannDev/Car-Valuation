"""Connect CLI import commands to the API's import service."""

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from app.importing.contracts import ImportSpec
from sqlalchemy.engine import Engine

from reader import Config

from .prepare import atomic_manifest, digest, implementation_hash, prepare
from .rows import chunks


def execute(args: argparse.Namespace) -> int:
    """Run import, resume, or status commands, loading the API only when needed."""

    from app import create_app
    from app.extensions import db
    from app.importing.service import import_status

    if args.import_action == "file":
        parse_observed_at(args.observed_at)

    # Set up the database only after checking any supplied observation timestamp.
    app = create_app()

    with app.app_context():
        if args.import_action == "status":
            return emit_result(import_status(db.engine, int(args.batch_id)))

        if args.import_action == "resume":
            return resume(args, db.engine)

        return import_file(args, db.engine)


def import_file(
    args: argparse.Namespace,
    engine: Engine,
) -> int:
    """Prepare files that will stay unchanged and pass them to the API import service."""

    from app.importing.service import existing_import, run_import

    config = Config.load(args.config, args.enable, args.disable, args.aliases)
    if "validate" not in config.enabled:
        raise ValueError("import requires the validate rule")

    # Check the source hash first so an already imported file does not create another
    # prepared copy.
    existing = existing_import(
        engine, args.source, digest(args.source_file.expanduser())
    )
    if existing is not None:
        if existing["status"] == "completed":
            return emit_result({**existing, "no_op": True})

        raise ValueError(
            f"File already has an incomplete import; use import-resume {existing['batch_id']}"
        )

    # Prepare a stable source copy and cleaned-up data before recording the import
    # settings.
    retained, artifact, manifest, source_hash, total, rules_hash = prepare(
        args.source_file, args.work_dir, config
    )
    observed_at = parse_observed_at(args.observed_at)
    spec = ImportSpec(
        source_name=args.source,
        source_file_name=args.source_file.name,
        source_file_hash=source_hash,
        source_file_uri=str(retained),
        normalization_version=config.version,
        rules_hash=rules_hash,
        manifest_uri=str(manifest),
        artifact_uri=str(artifact),
        artifact_hash=digest(artifact),
        total_rows=total,
        observed_at=observed_at,
        currency_code=args.currency,
        mileage_unit=args.mileage_unit,
        country_code=args.country_code,
    )

    # Save the exact inputs and settings before starting database work so a failed run
    # can resume.
    atomic_manifest(
        manifest, {"spec": jsonable(asdict(spec)), "configuration": config_data(config)}
    )
    result = run_import(
        engine,
        spec,
        chunks(spec.artifact_uri, args.batch_size),
        dry_run=args.dry_run,
        on_progress=emit_progress,
    )

    return emit_result(result)


def resume(
    args: argparse.Namespace,
    engine: Engine,
) -> int:
    """Check the saved files and current reader code before resuming an import."""

    from app.importing.service import import_status, run_import

    batch_id = int(args.batch_id)
    status = import_status(engine, batch_id)

    # Load the saved manifest and verify both files before trusting a resume request.
    manifest_path = Path(status["manifest_uri"])
    data = json.loads(manifest_path.read_text())
    stored = data["spec"]
    source = Path(stored["source_file_uri"])
    artifact = Path(stored["artifact_uri"])
    if (
        digest(source) != stored["source_file_hash"]
        or digest(artifact) != stored["artifact_hash"]
    ):
        raise ValueError(
            "Retained source or normalized artifact hash does not match the import manifest"
        )

    # Rebuild the original settings and check the current code against the saved rules
    # hash.
    config_values = data["configuration"]
    config = Config(
        version=config_values["version"],
        quote_char=config_values["quote_char"],
        enabled=frozenset(config_values["enabled"]),
        placeholders=config_values["placeholders"],
        aliases=config_values["aliases"],
        minimum_model_vins=config_values.get("minimum_model_vins", 50),
        minimum_make_models=config_values.get("minimum_make_models", 5),
    )
    current_rules_hash = implementation_hash(config_data(config))
    if current_rules_hash != stored["rules_hash"]:
        raise ValueError(
            "Reader rules or effective configuration changed; this import cannot be resumed"
        )

    # Use the checked manifest to continue the same batch from its saved checkpoint.
    spec = ImportSpec(
        **{**stored, "observed_at": parse_observed_at(stored["observed_at"])}
    )
    result = run_import(
        engine,
        spec,
        chunks(str(artifact), args.batch_size),
        resume_batch_id=batch_id,
        on_progress=emit_progress,
    )

    return emit_result(result)


def config_data(config: Config) -> dict[str, object]:
    """Save the effective cleanup settings so retries use the same rules."""

    from dataclasses import asdict

    data = asdict(config)
    data["enabled"] = sorted(config.enabled)

    return data


def parse_observed_at(value: str | None) -> datetime | None:
    """Require observation timestamps to include their UTC offset."""

    if value is None:
        return None

    parsed = datetime.fromisoformat(value)
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
        or parsed.utcoffset().total_seconds() != 0
    ):
        raise ValueError("observed-at must use UTC, for example 2026-10-27T12:00:00Z")

    return parsed.astimezone(UTC)


def jsonable(value: object) -> object:
    """Convert dates and import settings into values JSON can store."""

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}

    if isinstance(value, (tuple, list, set, frozenset)):
        return [jsonable(item) for item in value]

    return value


def emit_result(result: dict[str, object]) -> int:
    """Print the result as JSON and return exit status 1 if any rows were rejected."""

    print(json.dumps({"type": "summary", **result}, default=str))

    # Use the same exit codes for imports and resumes: 0 for success, 1 for rejected
    # rows, and 2 for failure.
    if result.get("status") in ("failed", "error"):
        return 2

    if result.get("no_op"):
        return 0

    return 1 if result.get("rejected_rows", 0) else 0


def emit_progress(progress: dict[str, object]) -> None:
    """Report progress only after the chunk has been committed to the database."""

    print(json.dumps(progress), flush=True)
