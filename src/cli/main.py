"""Explore and check source data, with separate commands for importing it into the database."""

import argparse
import csv
import json
import resource
import sys
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

import polars as pl

from reader import Config, build
from reader.pipeline import columns, filtered
from reader.reader import check_structure
from reader.reporting import (
    invalid_rows,
    issue_columns,
    print_diff,
    print_final_summary,
    summary,
    unmatched_values,
)
from reader.schema import COLUMNS


def parser() -> argparse.ArgumentParser:
    """Define the CLI commands without adding a command-line framework."""

    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    # Reader commands share the same rule settings, but only expose options they
    # actually use.
    for name in ("preview", "profile", "validate", "diff", "benchmark"):
        command = commands.add_parser(name)
        command.add_argument("file", type=Path)
        command.add_argument("--config", type=Path)
        command.add_argument(
            "--aliases", type=Path, help="JSON alias overrides merged with defaults"
        )
        command.add_argument("--enable", action="append", default=[])
        command.add_argument("--disable", action="append", default=[])
        command.set_defaults(filter=[])
        if name != "benchmark":
            command.add_argument(
                "--filter", action="append", default=[], metavar="COLUMN=VALUE"
            )

        command.add_argument(
            "--limit", type=int, default=20, help="Maximum displayed rows/examples"
        )
        if name == "preview":
            command.add_argument(
                "--columns",
                help="Comma-separated columns; raw_COLUMN shows original values",
            )

        if name == "profile":
            command.add_argument("--group-by", help="Comma-separated grouping columns")

        if name == "validate":
            command.add_argument(
                "--output", type=Path, help="Normalized Parquet output"
            )
            command.add_argument(
                "--issues", type=Path, help="Full issue records as Parquet"
            )

        if name in ("validate", "diff"):
            command.add_argument(
                "--verbose",
                action="store_true",
                help="Show raw rejected rows, field differences, and effective configuration",
            )
            command.add_argument(
                "--unmatched",
                type=Path,
                help="All unmatched values and counts as JSONL",
            )
            command.add_argument(
                "--invalid-rows",
                type=Path,
                help="All invalid source rows and field diffs as JSONL",
            )

        if name == "benchmark":
            command.add_argument("--runs", type=int, default=3)

    # Import commands prepare resumable files and then hand database writes to the API
    # service.
    file_import = commands.add_parser("import")
    file_import.set_defaults(import_action="file")
    file_import.add_argument("source_file", type=Path)
    file_import.add_argument("--source", default="inventory")
    file_import.add_argument("--batch-size", type=int, default=5000)
    file_import.add_argument("--dry-run", action="store_true")
    file_import.add_argument("--work-dir", type=Path)
    file_import.add_argument("--observed-at")
    file_import.add_argument("--currency")
    file_import.add_argument("--mileage-unit", choices=("MI", "KM"))
    file_import.add_argument("--country-code")
    file_import.add_argument("--config", type=Path)
    file_import.add_argument("--aliases", type=Path)
    file_import.add_argument("--enable", action="append", default=[])
    file_import.add_argument("--disable", action="append", default=[])

    # Resuming and checking status reuse the saved batch rather than preparing the
    # source again.
    for action in ("resume", "status"):
        command = commands.add_parser(f"import-{action}")
        command.set_defaults(import_action=action)
        command.add_argument("batch_id", type=int)
        if action == "resume":
            command.add_argument("--batch-size", type=int, default=5000)

    return root


def import_command(args: argparse.Namespace) -> int:
    """Load database dependencies only when an import command needs them."""

    if getattr(args, "batch_size", 5000) < 1:
        raise ValueError("batch-size must be positive")

    try:
        # Reader commands can run without the API package or SQLAlchemy installed.
        from ingestion.cli import execute

        return execute(args)
    except ImportError:
        print(
            "Error: install the API package and CLI import extra; see src/cli/README.md",
            file=sys.stderr,
        )

        return 2
    except (
        ValueError,
        OSError,
        RuntimeError,
        csv.Error,
        pl.exceptions.PolarsError,
    ) as error:
        print(f"Error: {error}", file=sys.stderr)

        return 2
    except Exception:
        # Database errors can include connection URLs, so keep their details out of CLI
        # output to avoid exposing credentials.
        print(
            "Error: import failed; verify database connectivity and migrations, then inspect import-status before resuming",
            file=sys.stderr,
        )

        return 2


def main() -> int:
    """Run a reader command. Return 1 for invalid rows and 2 if the command fails."""

    args = parser().parse_args()

    try:
        if args.command in ("import", "import-resume", "import-status"):
            return import_command(args)

        if args.limit < 1 or getattr(args, "runs", 1) < 1:
            raise ValueError("limit and runs must be positive")

        # Check rule settings before building a pipeline or creating any output files.
        config = Config.load(args.config, args.enable, args.disable, args.aliases)
        if args.command in ("validate", "diff") and "validate" not in config.enabled:
            raise ValueError("validate and diff commands require the validate rule")

        settings = asdict(config)
        settings["enabled"] = sorted(config.enabled)

        # Check all export paths up front so a later export cannot overwrite the input
        # or another report.
        targets = [
            target
            for target in (
                getattr(args, "output", None),
                getattr(args, "issues", None),
                getattr(args, "unmatched", None),
                getattr(args, "invalid_rows", None),
            )
            if target
        ]
        if len({target.resolve() for target in targets}) != len(targets):
            raise ValueError("Export paths must differ")

        for target in targets:
            if target.exists() or target.resolve() == args.file.resolve():
                raise ValueError(f"Refusing to overwrite: {target}")

            if not target.parent.is_dir():
                raise ValueError(f"Output directory does not exist: {target.parent}")

        # Build the lazy query once. Each command below decides how much data to read.
        started = perf_counter()
        frame = filtered(build(args.file, config), args.filter)

        # A preview only reads the requested sample; it does not validate the rest of
        # the file.
        if args.command == "preview":
            selected = (
                args.columns.split(",")
                if args.columns
                else ["source_record_number", *COLUMNS]
            )
            columns(frame, selected)
            print(frame.select(selected).head(args.limit).collect(engine="streaming"))
            print("Preview only; the remaining file has not been validated.")

            return 0

        # Full-file commands check row structure before collecting results or writing
        # reports.
        if args.command not in ("preview", "benchmark"):
            check_structure(args.file, config)

        # Profiles count missing values and issues, optionally grouped by the requested
        # columns.
        if args.command == "profile":
            if args.group_by:
                groups = args.group_by.split(",")
                columns(frame, groups)
                issues = issue_columns(frame)
                errors = [c for c in issues if c.startswith("error_")]
                warnings = [c for c in issues if c.startswith("warning_")]
                result = (
                    frame.group_by(groups)
                    .agg(
                        pl.len().alias("count"),
                        pl.any_horizontal([pl.col(c).is_null() for c in COLUMNS])
                        .sum()
                        .alias("records_with_missing_values"),
                        (
                            pl.any_horizontal([pl.col(c) for c in errors])
                            if errors
                            else pl.lit(False)
                        )
                        .cast(pl.UInt64)
                        .sum()
                        .alias("records_with_errors"),
                        (
                            pl.any_horizontal([pl.col(c) for c in warnings])
                            if warnings
                            else pl.lit(False)
                        )
                        .cast(pl.UInt64)
                        .sum()
                        .alias("records_with_unresolved_aliases"),
                    )
                    .sort(["count", *groups], descending=[True, *[False] * len(groups)])
                    .head(args.limit)
                )
            else:
                result = frame.select(
                    pl.len().alias("total"),
                    *(pl.col(c).null_count().alias(c) for c in COLUMNS),
                    *(
                        pl.col(c).cast(pl.UInt64).sum().alias(c)
                        for c in issue_columns(frame)
                    ),
                )
                print(
                    json.dumps(
                        result.collect(engine="streaming").row(0, named=True), indent=2
                    )
                )

                return 0

            print(result.collect(engine="streaming"))

            return 0

        # Time parsing separately from the full rule pipeline so their costs can be
        # compared.
        if args.command == "benchmark":
            print(
                json.dumps(
                    {
                        "file": str(args.file),
                        "file_bytes": args.file.stat().st_size,
                        "polars_version": pl.__version__,
                        "configuration": settings,
                    }
                )
            )

            for phase, candidate in [
                (
                    "parse",
                    filtered(
                        build(
                            args.file,
                            Config(quote_char=config.quote_char, enabled=frozenset()),
                        ),
                        args.filter,
                    ),
                ),
                ("pipeline", frame),
            ]:
                for run in range(args.runs):
                    tick = perf_counter()
                    check_structure(args.file, config)
                    result = summary(candidate).row(0, named=True)
                    elapsed = perf_counter() - tick
                    print(
                        json.dumps(
                            {
                                "phase": phase,
                                "run": run + 1,
                                "seconds": round(elapsed, 3),
                                "rows_per_second": round(result["total"] / elapsed),
                                **result,
                            }
                        )
                    )

            print(
                "Cache is not cleared. First run is not guaranteed cold; fingerprinting is included in timings."
            )
        else:
            # Validation and diff share the same full-file counts and issue reports.
            result = summary(frame).row(0, named=True)
            if args.verbose:
                print(json.dumps({"type": "configuration", "configuration": settings}))

            issues = issue_columns(frame)
            affected = frame.filter(pl.any_horizontal([pl.col(c) for c in issues]))
            print_diff(frame, args.limit, verbose=args.verbose, config=config)

            # Export every matching issue, even when terminal output only shows a
            # limited sample.
            for target, data in [
                (args.unmatched, unmatched_values(frame)),
                (args.invalid_rows, invalid_rows(frame)),
            ]:
                if target:
                    tick = perf_counter()
                    data.sink_ndjson(target, engine="streaming")
                    print(
                        f"Exported {target} in {perf_counter() - tick:.3f}s (additional scan)."
                    )

            # Parquet exports retain the effective settings and source row numbering for
            # later inspection.
            for target, data in [
                (getattr(args, "output", None), frame),
                (getattr(args, "issues", None), affected),
            ]:
                if target:
                    if target.resolve() == args.file.resolve() or target.exists():
                        raise ValueError(f"Refusing to overwrite: {target}")

                    tick = perf_counter()
                    data.sink_parquet(
                        target,
                        engine="streaming",
                        metadata={
                            "normalization_config": json.dumps(
                                settings, sort_keys=True
                            ),
                            "source_file": str(args.file),
                            "record_number_convention": "header=1; first data record=2",
                        },
                    )
                    print(
                        f"Exported {target} in {perf_counter() - tick:.3f}s (additional scan)."
                    )

            print_final_summary(result, perf_counter() - started)

            return 1 if result["rejected"] else 0

        return 0
    except (ValueError, OSError, csv.Error, pl.exceptions.PolarsError) as error:
        print(f"Error: {error}", file=sys.stderr)

        return 2
    finally:
        # Report peak memory even when a command fails so large-file runs can be
        # compared.
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        print(
            f"Process peak RSS: {peak / (1024**2 if sys.platform == 'darwin' else 1024):.1f} MiB",
            file=sys.stderr,
        )


if __name__ == "__main__":
    raise SystemExit(main())
