"""Build a reusable data pipeline for CLI commands and a future database import service."""

from pathlib import Path

import polars as pl

from .config import Config
from .reader import scan
from .rules import RULES


def build(path: Path, config: Config) -> pl.LazyFrame:
    """Set up the enabled rules in order without reading the file yet."""

    frame = scan(path, config)

    # Apply enabled rules in their declared order, checking required columns at each
    # step.
    for rule in RULES:
        if rule.id in config.enabled:
            columns(frame, list(rule.requires))
            frame = rule.apply(frame, config)

    return frame


def columns(frame: pl.LazyFrame, names: list[str]) -> None:
    """Check requested columns before running the pipeline."""

    missing = set(names) - set(frame.collect_schema().names())
    if missing:
        raise ValueError(f"Unknown columns: {sorted(missing)}")


def filtered(frame: pl.LazyFrame, filters: list[str]) -> pl.LazyFrame:
    """Apply exact text filters written as COLUMN=VALUE."""

    for item in filters:
        # Split only at the first equals sign so the filter value can contain additional
        # ones.
        column, separator, value = item.partition("=")
        if not separator:
            raise ValueError("Filters must use COLUMN=VALUE")

        columns(frame, [column])
        frame = frame.filter(pl.col(column).cast(pl.String) == value)

    return frame
