"""Profile a full import on local MySQL with the experimental optimizations enabled.

Run with PYTHONPATH=src/cli and the CLI virtualenv. This script never resets a schema;
that requires a separately authorized action. Keep credentials in memory and source
files outside the repository.
"""

import argparse
import hashlib
import json
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

import candidates
import ingestion.prepare as preparation
from alembic import command
from alembic.config import Config
from app import create_app
from app.extensions import db
from app.importing import repository, service
from app.models import ImportBatch, ImportChunk, Listing, ListingObservation, Vehicle
from database import LocalDatabase
from ingestion import cli
from main import parser as cli_parser
from metrics import Timings
from progress import Progress, peak_mib
from sampler import Sampler
from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[2]


def vin_first(connection, rows):
    """Avoid the catalog-first query plan seen in the benchmark while keeping identity checks."""
    from app.models import Make, Model, ModelYear

    statement = (
        select(Vehicle.vin, ModelYear.year, Make.normalized_name, Model.normalized_name)
        .select_from(Vehicle.__table__.join(ModelYear).join(Model).join(Make))
        .prefix_with("STRAIGHT_JOIN", dialect="mysql")
    )
    found = {}
    for chunk in repository.batches(list({row.vin for row in rows})):
        for record in connection.execute(statement.where(Vehicle.vin.in_(chunk))):
            found[record[0]] = tuple(record[1:])
    return found


def harness_fingerprint(statement_rows):
    digest = hashlib.sha256(str(statement_rows).encode())
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def install_provenance(fingerprint, information):
    original_hash = preparation.implementation_hash
    original_manifest = cli.atomic_manifest

    def effective_hash(settings):
        return hashlib.sha256(
            (original_hash(settings) + fingerprint).encode()
        ).hexdigest()

    def manifest(path, data):
        original_manifest(path, {**data, "performance_experiment": information})

    preparation.implementation_hash = effective_hash
    cli.implementation_hash = effective_hash
    cli.atomic_manifest = manifest


def configure_instrumentation(metrics, progress, sampler):
    metrics.install()
    metrics.plan_callback = lambda cursor, sql, params, operation, seconds: (
        sampler.explain(cursor, sql, params, operation, seconds, metrics.checkpoint - 1)
    )
    for name in (
        "existing_vin_identities",
        "listing_owners",
        "resolve_catalog",
        "resolve_vehicles",
        "resolve_dealers",
        "resolve_listings",
        "lookup_codes",
    ):
        metrics.wrap(repository, name)
    metrics.wrap(cli, "prepare", "source_preparation")
    metrics.wrap(cli, "digest", "external_file_hashes")
    metrics.wrap(cli, "atomic_manifest", "manifest_write")
    metrics.wrap(service, "_write_chunk_report", "issue_reports")
    original_chunks = cli.chunks

    def chunks(*args, **kwargs):
        iterator = iter(original_chunks(*args, **kwargs))
        while True:
            with metrics.stage("parquet_decode_and_row_mapping"):
                try:
                    batch = next(iterator)
                except StopIteration:
                    return
            yield batch

    cli.chunks = chunks
    for method in ("fetch", "write"):
        metrics.wrap(repository.Resolver, method, "resolver_" + method)
    original_import = service.run_import

    def run_import(*args, **kwargs):
        progress.start_ingestion(kwargs.get("resume_batch_id"))
        return original_import(*args, **kwargs)

    service.run_import = run_import
    service.process_rows = progress.wrap_process(service.process_rows)
    cli.emit_progress = progress.on_chunk


def import_arguments(args, artifact_dir):
    if args.resume_batch:
        return cli_parser().parse_args(
            ["import-resume", str(args.resume_batch), "--batch-size", "5000"]
        )
    return cli_parser().parse_args(
        [
            "import",
            str(args.source),
            "--source",
            "inventory",
            "--batch-size",
            "5000",
            "--work-dir",
            str(artifact_dir / "retained"),
            "--aliases",
            str(ROOT / "src/cli/aliases.example.json"),
        ]
    )


def require_empty(engine):
    for model in (ImportBatch, Vehicle, Listing, ListingObservation):
        with engine.connect() as connection:
            if connection.execute(select(model.id).limit(1)).first() is not None:
                raise ValueError(
                    "Full initial-load profiling requires an empty schema or --resume-batch"
                )


def validate(engine, result):
    with engine.connect() as connection:
        batch_id = result["batch_id"]
        observations = connection.scalar(
            select(func.count())
            .select_from(ListingObservation)
            .where(ListingObservation.import_batch_id == batch_id)
        )
        chunks = connection.scalar(
            select(func.count())
            .select_from(ImportChunk)
            .where(ImportChunk.import_batch_id == batch_id)
        )
        assert observations == result["accepted_rows"]
        assert result["accepted_rows"] + result["rejected_rows"] == result["total_rows"]
        assert result["checkpoint"] == result["total_rows"] + 1
        counts = {
            model.__tablename__: connection.scalar(
                select(func.count()).select_from(model)
            )
            for model in (Vehicle, Listing, ListingObservation, ImportChunk)
        }
    return {
        "accepted_observations_match": True,
        "committed_chunk_reports": chunks,
        "table_counts": counts,
    }


def write_notes(report_dir, summary):
    sql = sorted(
        summary["timings"]["sql"].items(), key=lambda pair: -pair[1]["seconds"]
    )[:15]
    lines = [
        "# Full import profile",
        "",
        f"- Target: `{summary['target']}`",
        f"- Status: **{summary['result']['status']}**",
        f"- Records: {summary['result']['total_rows']:,}; accepted: {summary['result']['accepted_rows']:,}; rejected: {summary['result']['rejected_rows']:,}.",
        f"- Wall time: {summary['wall_seconds']:.2f}s; throughput: {summary['source_records_this_run'] / summary['wall_seconds']:.0f} source records/s.",
        f"- Peak process memory: {summary['peak_process_mib']:.1f} MiB.",
        f"- Diagnostic SQL time: {summary['diagnostic_seconds']:.2f}s. Sampled CPU chunks add profiling overhead.",
        "",
        "## SQL execution and buffered transfer",
        "",
        "| Operation | Seconds | Calls | Returned rows |",
        "|---|---:|---:|---:|",
    ]
    lines += [
        f"| `{key}` | {value['seconds']:.2f} | {value['calls']:,} | {value['returned_rows']:,} |"
        for key, value in sql
    ]
    lines += [
        "",
        "## Throughput as tables grow",
        "",
        "| Records processed | Window records/s | Physical reads / requests | Current process MiB |",
        "|---:|---:|---:|---:|",
    ]
    for window in summary["windows"]:
        lines.append(
            f"| {window['records_processed']:,} | {window['records_per_second']:.0f} | {window['buffer_pool_physical_read_ratio']:.3%} | {window['current_process_mib']:.1f} |"
        )
    lines += [
        "",
        "## Interpretation boundaries",
        "",
        "- Stage timers are inclusive and overlap SQL/driver time; do not add them together.",
        "- SQL execution includes driver processing, server wait and buffered result transfer.",
        "- Server counters include diagnostic and other local-server activity.",
        "- EXPLAIN samples are collected immediately after selected reads in the same transaction.",
        "- No durability settings, foreign keys, uniqueness checks or observation history were disabled.",
        "- Candidate optimizations are runtime profiling overrides, not deployed application changes.",
        "",
        "See `summary.json`, `windows.json`, `chunks.csv`, `query-plans.json`, and `cpu-samples.json` for evidence.",
    ]
    (report_dir / "REPORT.md").write_text("\n".join(lines) + "\n")


def execute(args):
    local = LocalDatabase.inspect(args.container)
    local.configure_application(args.database)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_dir = ROOT / "docs/performance/runs" / (stamp + "-" + args.database)
    artifact_dir = Path.home() / ".local/share/valueapi/profiles" / report_dir.name
    report_dir.mkdir(parents=True)
    artifact_dir.mkdir(parents=True)
    fingerprint = harness_fingerprint(args.statement_rows)
    info = {
        "target": f"{local.host}:{local.port}/{args.database}",
        "started_at_utc": stamp,
        "harness_sha256": fingerprint,
        "statement_rows": args.statement_rows,
        "source_transaction_rows": 5000,
        "catalog_cache_limit": candidates.CACHE_LIMIT,
        "artifact_directory": str(artifact_dir),
        "source_file": str(args.source.resolve()),
        "experiment": "VIN-first join, bounded committed catalog cache, chunk ID reuse, projections, changed-entity writes",
    }
    (report_dir / "configuration.json").write_text(json.dumps(info, indent=2))
    print(
        json.dumps(
            {"type": "run_started", "report_directory": str(report_dir), **info}
        ),
        flush=True,
    )
    candidates.install(write_rows=args.statement_rows)
    candidates.reset_run()
    repository.existing_vin_identities = vin_first
    install_provenance(fingerprint, info)
    diagnostic = local.diagnostics(args.database)
    sampler = Sampler(diagnostic)
    info["mysql_configuration"] = sampler.variables()
    (report_dir / "configuration.json").write_text(json.dumps(info, indent=2))
    metrics = Timings()
    progress = Progress(
        metrics, sampler, report_dir, artifact_dir, candidates.statistics
    )
    try:
        run(args, report_dir, info, artifact_dir, metrics, sampler, progress)
    finally:
        progress.close()
        diagnostic.close()


def run(args, report_dir, info, artifact_dir, metrics, sampler, progress):
    with create_app().app_context():
        command.upgrade(Config(str(ROOT / "src/api/alembic.ini")), "head")
        engine = db.engine
        if not args.resume_batch:
            require_empty(engine)
        configure_instrumentation(metrics, progress, sampler)
        captured = {}

        def result(value):
            captured.update(value)
            return 1 if value.get("rejected_rows") else 0

        cli.emit_result = result
        metrics.start()
        arguments = import_arguments(args, artifact_dir)
        if args.resume_batch:
            cli.resume(arguments, engine)
        else:
            cli.import_file(arguments, engine)
        wall = time.perf_counter() - metrics.started
        metrics.active = False
        validation = validate(engine, captured)
        summary = {
            **info,
            "result": captured,
            "wall_seconds": wall,
            "initial_records": progress.initial_records,
            "source_records_this_run": captured["total_rows"]
            - progress.initial_records,
            "peak_process_mib": peak_mib(),
            "timings": metrics.snapshot(),
            "candidate_counters": candidates.statistics(),
            "validation": validation,
            "windows": progress.windows,
            "diagnostic_seconds": sampler.diagnostic_seconds,
            "cpu_samples": progress.cpu_samples,
            "server_statement_digests": sampler.statement_digests(),
        }
        (report_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, default=str)
        )
        (report_dir / "query-plans.json").write_text(
            json.dumps(sampler.plans, indent=2)
        )
        write_notes(report_dir, summary)
        print(
            json.dumps(
                {
                    "type": "completed",
                    "wall_seconds": wall,
                    "total_rows": captured["total_rows"],
                    "accepted_rows": captured["accepted_rows"],
                    "rejected_rows": captured["rejected_rows"],
                    "peak_process_mib": peak_mib(),
                    "report_directory": str(report_dir),
                    "validation": validation,
                }
            ),
            flush=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--container", default="valueapi-mysql-1")
    parser.add_argument(
        "--database", choices=("valueapi", "valueapi_profile_check"), default="valueapi"
    )
    parser.add_argument("--statement-rows", type=int, default=2000)
    parser.add_argument("--resume-batch", type=int)
    args = parser.parse_args()
    if not 1 <= args.statement_rows <= 2000:
        parser.error("statement-rows must be between 1 and 2000")
    try:
        execute(args)
    except Exception as error:  # noqa: BLE001 - avoid credential-bearing driver messages
        # Database errors can include URLs. Save stack locations without the error text
        # to keep credentials out of reports.
        traceback.print_tb(error.__traceback__)
        print(
            json.dumps({"type": "failed", "error_type": type(error).__name__}),
            flush=True,
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()
