"""Read Parquet batches into the records expected by the API import service."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from app.importing.contracts import ImportRow


def chunks(path: str, batch_size: int) -> Iterator[list[ImportRow]]:
    """Keep only one batch of Python row objects in memory, including rejected rows."""

    from app.importing.contracts import ImportRow
    from pyarrow import parquet

    reader = parquet.ParquetFile(path)

    # Read one Parquet batch at a time so a large file does not become a large list of
    # Python objects.
    for batch in reader.iter_batches(batch_size=batch_size):
        frame = pl.DataFrame(batch)
        flags = [
            name for name in frame.columns if name.startswith(("error_", "warning_"))
        ]
        rows = []

        # Keep rejected rows and their original record numbers so import checkpoints
        # cover the entire source.
        for record in frame.iter_rows(named=True):
            rows.append(
                ImportRow(
                    source_record_number=record["source_record_number"],
                    vin=record["vin"],
                    year=record["year"],
                    make=record["make"],
                    model=record["model"],
                    trim=record["trim"],
                    dealer_name=record["dealer_name"],
                    dealer_street=record["dealer_street"],
                    dealer_city=record["dealer_city"],
                    dealer_state=record["dealer_state"],
                    dealer_zip=record["dealer_zip"],
                    seller_website=record["seller_website"],
                    first_seen_date=record["first_seen_date"],
                    last_seen_date=record["last_seen_date"],
                    dealer_vdp_last_seen_date=record["dealer_vdp_last_seen_date"],
                    listing_price=record["listing_price"],
                    listing_mileage=record["listing_mileage"],
                    used=record["used"],
                    certified=record["certified"],
                    listing_status=record["listing_status"],
                    drivetrain_code=record.get("driven_wheels_code"),
                    fuel_type_code=record.get("fuel_type_code"),
                    exterior_color_code=record.get("exterior_color_code"),
                    interior_color_code=record.get("interior_color_code"),
                    errors=tuple(
                        name
                        for name in flags
                        if name.startswith("error_") and record[name]
                    ),
                    warnings=tuple(
                        name
                        for name in flags
                        if name.startswith("warning_") and record[name]
                    ),
                )
            )

        yield rows
