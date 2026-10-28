"""Reports for data issues and benchmarks that process every output column."""

import polars as pl

from .config import Config


def issue_columns(frame: pl.LazyFrame) -> list[str]:
    """Find the error and warning flags added by the enabled rules."""

    return [
        c
        for c in frame.collect_schema().names()
        if c.startswith(("error_", "warning_"))
    ]


def summary(frame: pl.LazyFrame) -> pl.DataFrame:
    """Count data issues and hash every column so the benchmark runs every rule."""

    names = frame.collect_schema().names()
    issues = issue_columns(frame)
    errors = [c for c in issues if c.startswith("error_")]

    # Count each rejected row once, even if several validation checks failed for it.
    rejected = (
        pl.any_horizontal([pl.col(c) for c in errors]) if errors else pl.lit(False)
    )

    return frame.select(
        pl.len().alias("total"),
        rejected.cast(pl.UInt64).sum().alias("rejected"),
        *(pl.col(c).cast(pl.UInt64).sum().alias(c) for c in issues),
        pl.struct(names).hash(seed=0).sum().alias("execution_fingerprint"),
    ).collect(engine="streaming")


def unmatched_values(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Group unmatched source spellings and show their counts and first row numbers."""

    warnings = [c for c in issue_columns(frame) if c.startswith("warning_unresolved_")]
    if not warnings:
        return pl.LazyFrame(
            schema={
                "column": pl.String,
                "before": pl.String,
                "after": pl.String,
                "count": pl.UInt32,
                "first_source_record_number": pl.UInt32,
            }
        )

    # Group unmatched values by their original spelling so alias fixes can be traced
    # back to the source.
    groups = []

    for warning in warnings:
        column = warning.removeprefix("warning_unresolved_")
        groups.append(
            frame.filter(pl.col(warning))
            .group_by(pl.col(f"raw_{column}").alias("before"))
            .agg(
                pl.len().alias("count"),
                pl.col("source_record_number")
                .min()
                .alias("first_source_record_number"),
            )
            .with_columns(
                pl.lit(column).alias("column"),
                pl.lit(None, dtype=pl.String).alias("after"),
            )
            .select("column", "before", "after", "count", "first_source_record_number")
        )

    return pl.concat(groups).sort(
        ["count", "column", "before"], descending=[True, False, False]
    )


def invalid_rows(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Show rejected rows with the affected fields, original and cleaned-up values, and
    error codes.
    """

    from .schema import COLUMNS

    errors = [c for c in issue_columns(frame) if c.startswith("error_")]

    # Attach each error to its original and cleaned-up field values for the row report.
    changes = []

    for error in errors:
        column = error.split("_", 2)[2]
        column = {"vins": "model"}.get(column, column)
        changes.append(
            pl.when(pl.col(error)).then(
                pl.struct(
                    pl.lit(error).alias("issue"),
                    pl.lit(column).alias("column"),
                    pl.col(f"raw_{column}").alias("before"),
                    pl.col(column).cast(pl.String).alias("after"),
                )
            )
        )

    return frame.filter(
        pl.any_horizontal([pl.col(c) for c in errors]) if errors else pl.lit(False)
    ).select(
        "source_record_number",
        pl.struct(pl.col(f"raw_{c}").alias(c) for c in COLUMNS).alias("raw"),
        pl.concat_list(changes).list.drop_nulls().alias("changes")
        if changes
        else pl.lit([]).alias("changes"),
    )


def print_diff(
    frame: pl.LazyFrame,
    limit: int,
    *,
    verbose: bool = False,
    config: Config | None = None,
) -> None:
    """Show unmatched values and invalid rows separately, with a limit for each so warnings
    cannot hide errors.
    """

    import json

    # Give unmatched values and rejected rows separate display limits so neither section
    # hides the other.
    for category, records in [
        ("unmatched", unmatched_values(frame)),
        ("invalid_row", invalid_rows(frame)),
    ]:
        # Read one extra result to tell the caller whether the displayed sample was
        # truncated.
        sample = records.head(limit + 1).collect(engine="streaming")
        print(
            json.dumps(
                {
                    "type": "section",
                    "section": category,
                    "limit": limit,
                    "has_more": sample.height > limit,
                }
            )
        )

        for row in sample.head(limit).iter_rows(named=True):
            if category == "invalid_row":
                reasons = [
                    rejection_reason(change["issue"], config)
                    for change in row["changes"]
                ]
                if verbose:
                    row = {**row, "reasons": reasons}
                else:
                    row = {
                        "source_record_number": row["source_record_number"],
                        "vin": row["raw"]["vin"],
                        "reasons": reasons,
                    }

            print(json.dumps({"type": category, **row}))


def rejection_reason(issue: str, config: Config | None = None) -> str:
    """Turn a row's validation codes into short explanations."""

    _, category, column = issue.split("_", 2)
    label = "VIN" if column == "vin" else column.replace("_", " ")
    config = config or Config()

    match category:
        case "required":
            return f"Missing required {label}"
        case "type":
            return f"Invalid {label} value"
        case "negative":
            return f"{label.capitalize()} must be nonnegative"
        case "format":
            if column == "model":
                return "Model contains a JSON fragment with no unambiguous model name"

            return f"Invalid {label} format"
        case "models":
            return f"Make has fewer than {config.minimum_make_models} distinct supported models in this input file"
        case "model":
            return f"Model has fewer than {config.minimum_model_vins} distinct VINs in this input file"
        case _:
            return issue


def print_final_summary(result: dict[str, int], elapsed: float) -> None:
    """Report the number of errors separately from the number of rejected rows."""

    import json

    errors = {
        key: value
        for key, value in result.items()
        if key.startswith("error_") and value
    }

    # Count individual errors and warnings separately from rejected rows; one row can
    # have several issues.
    warnings = {
        key: value
        for key, value in result.items()
        if key.startswith("warning_") and value
    }
    print(
        json.dumps(
            {
                "type": "summary",
                "total": result["total"],
                "accepted": result["total"] - result["rejected"],
                "rejected": result["rejected"],
                "error_count": sum(errors.values()),
                "error_counts": errors,
                "warning_count": sum(warnings.values()),
                "warning_counts": warnings,
                "seconds": round(elapsed, 3),
            }
        )
    )
