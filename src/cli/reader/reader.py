"""Read CSV data lazily while preserving original values and source row numbers."""

import csv
from pathlib import Path

import polars as pl

from .config import Config
from .schema import COLUMNS


def scan(path: Path, config: Config) -> pl.LazyFrame:
    """Read pipe-delimited records, counting the header as record 1 and data from record 2.

    A multiline field still belongs to one CSV record, so record numbers are not
    physical line numbers. Full-file processing stops on malformed CSV instead of
    skipping records.
    """

    # Read just the header first so unexpected or repeated columns fail before scanning
    # the data.
    with path.open(newline="", encoding="utf-8-sig") as source:
        header = next(
            csv.reader(
                source,
                delimiter="|",
                strict=True,
                quoting=csv.QUOTE_MINIMAL if config.quote_char else csv.QUOTE_NONE,
            ),
            [],
        )

    if len(header) != len(set(header)) or set(header) != set(COLUMNS):
        raise ValueError(
            f"Unexpected header. Missing: {sorted(set(COLUMNS) - set(header))}; extra: {sorted(set(header) - set(COLUMNS))}"
        )

    # Keep an untouched copy of each source column for before-and-after reports.
    return pl.scan_csv(
        path,
        separator="|",
        quote_char=config.quote_char,
        infer_schema=False,
        glob=False,
        row_index_name="source_record_number",
        row_index_offset=2,
        ignore_errors=False,
        truncate_ragged_lines=False,
        raise_if_empty=True,
    ).with_columns([pl.col(c).alias(f"raw_{c}") for c in COLUMNS])


def check_structure(path: Path, config: Config) -> None:
    """Check the number of fields in each row before processing the full file; do not
    silently pad short rows.

    The provider's literal-text format is checked with a streaming line scan. Quoted CSV
    is optional and uses Python's strict CSV parser to check row structure.
    """

    # Quoted CSV can contain multiline fields, so use the CSV parser to count complete
    # records.
    if config.quote_char:
        with path.open(newline="", encoding="utf-8-sig") as source:
            for number, row in enumerate(
                csv.reader(source, delimiter="|", strict=True), 1
            ):
                if len(row) != len(COLUMNS):
                    raise ValueError(
                        f"Malformed CSV record {number}: expected {len(COLUMNS)} columns, found {len(row)}"
                    )

        return

    # Literal-text input has one record per line. Count its separators without changing
    # field values.
    lines = pl.scan_csv(
        path,
        separator="\x1f",
        quote_char=None,
        has_header=False,
        schema={"record": pl.String},
        glob=False,
        row_index_name="source_record_number",
        row_index_offset=1,
    )
    bad = lines.filter(
        pl.col("record").str.count_matches("|", literal=True).fill_null(0)
        != len(COLUMNS) - 1
    )
    first = bad.select("source_record_number").head(1).collect(engine="streaming")
    if first.height:
        raise ValueError(
            f"Malformed CSV record {first.item()}: expected {len(COLUMNS)} pipe-delimited columns"
        )
