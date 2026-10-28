"""Compare identity implementations using separate new schemas and the same rows.

Run with PYTHONPATH=src/api:src/cli:scripts/performance and the CLI virtualenv. The
baseline module must contain the repository code from before identity reuse was added.
Neither version uses the experimental resolver overrides from the full-run profiler.
"""

import argparse
import hashlib
import importlib.util
import json
import statistics
import sys
import time
from dataclasses import replace
from itertools import islice
from pathlib import Path
from urllib.parse import unquote, urlparse

from app.extensions import db
from app.importing import repository, service
from app.importing.contracts import ImportSpec
from database import LocalDatabase
from ingestion.rows import chunks
from sqlalchemy import URL, create_engine, select


def load_baseline(path):
    spec = importlib.util.spec_from_file_location(
        "app.importing.benchmark_baseline", path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def snapshot(engine):
    """Compare saved values and issues, ignoring details that change between runs."""
    digest = hashlib.sha256()
    counts = {}
    with engine.connect() as connection:
        for table in db.metadata.sorted_tables:
            if table.name in ("import_batches", "import_chunks"):
                continue
            columns = [
                column
                for column in table.columns
                if column.name not in ("created_at", "updated_at")
            ]
            records = connection.execute(
                select(*columns).order_by(*table.primary_key.columns)
            )
            count = 0
            for record in records:
                digest.update(json.dumps(list(record), default=str).encode())
                count += 1
            counts[table.name] = count
        reports = connection.execute(
            select(db.metadata.tables["import_chunks"].c.report_uri).order_by(
                db.metadata.tables["import_chunks"].c.first_record
            )
        ).scalars()
        for report in reports:
            digest.update(Path(unquote(urlparse(report).path)).read_bytes())
    return {"sha256": digest.hexdigest(), "counts": counts}


def trial(local, module, rows, spec, output, name):
    """Create the schema and check results outside the timed import."""
    schema = "valueapi_identity_bench"
    diagnostic = local.diagnostics("mysql")
    engine = None
    created = False
    try:
        with diagnostic.cursor() as cursor:
            # Refuse to delete an existing schema, even one left by another benchmark.
            cursor.execute(
                "CREATE DATABASE valueapi_identity_bench "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin"
            )
            created = True
        url = URL.create(
            "mysql+pymysql",
            username="root",
            password=local.values["MYSQL_ROOT_PASSWORD"],
            host=local.host,
            port=local.port,
            database=schema,
        )
        engine = create_engine(url)
        db.metadata.create_all(engine)
        run_spec = replace(spec, manifest_uri=str(output / name / "manifest.json"))
        service.process_rows = module.process_rows
        started = time.perf_counter()
        result = service.run_import(engine, run_spec, iter(rows))
        elapsed = time.perf_counter() - started
        return {
            "name": name,
            "seconds": elapsed,
            "accepted": result["accepted_rows"],
            "rejected": result["rejected_rows"],
            "checkpoint": result["checkpoint"],
            "data": snapshot(engine),
        }
    finally:
        service.process_rows = repository.process_rows
        if engine is not None:
            engine.dispose()
        if created:
            with diagnostic.cursor() as cursor:
                cursor.execute("DROP DATABASE valueapi_identity_bench")
        diagnostic.close()


def main(args):
    manifest = json.loads(args.manifest.read_text())
    spec = ImportSpec(**manifest["spec"])
    # Decode the rows once so every timed run receives exactly the same records.
    rows = list(islice(chunks(spec.artifact_uri, 5000), args.rows // 5000))
    total = sum(map(len, rows))
    spec = replace(spec, total_rows=total)
    args.output.mkdir(parents=True, exist_ok=True)
    baseline = load_baseline(args.baseline)
    local = LocalDatabase.inspect("valueapi-mysql-1")
    variants = {"baseline": baseline, "reuse": repository}
    expected = None
    trials = []
    for pair in range(args.pairs):
        order = ("baseline", "reuse") if pair % 2 == 0 else ("reuse", "baseline")
        for variant in order:
            result = trial(
                local, variants[variant], rows, spec, args.output, f"{pair}-{variant}"
            )
            result["variant"] = variant
            signature = {
                key: result[key]
                for key in ("accepted", "rejected", "checkpoint", "data")
            }
            if expected is None:
                expected = signature
            assert signature == expected, "Persisted data or issue reports differ"
            trials.append(result)
            (args.output / "progress.json").write_text(json.dumps(trials, indent=2))
            print(
                json.dumps(
                    {key: value for key, value in result.items() if key != "data"}
                ),
                flush=True,
            )
    medians = {
        variant: statistics.median(
            item["seconds"] for item in trials if item["variant"] == variant
        )
        for variant in variants
    }
    report = {
        "source_records": total,
        "pairs": args.pairs,
        "method": "Alternating fresh-schema initial imports; predecoded normalized input; regular repository, no experimental overrides; commits and reports included; schema setup and verification excluded; no warmup pair discarded",
        "baseline_sha256": hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
        "reuse_sha256": hashlib.sha256(
            Path(repository.__file__).read_bytes()
        ).hexdigest(),
        "medians_seconds": medians,
        "percent_faster": 100 * (1 - medians["reuse"] / medians["baseline"]),
        "speedup": medians["baseline"] / medians["reuse"],
        "trials": trials,
    }
    (args.output / "summary.json").write_text(json.dumps(report, indent=2))
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "trials"}, indent=2
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=50_000)
    parser.add_argument("--pairs", type=int, default=5)
    arguments = parser.parse_args()
    if arguments.rows < 5000 or arguments.rows % 5000 or arguments.pairs < 1:
        parser.error("rows must be a positive multiple of 5000; pairs must be positive")
    main(arguments)
