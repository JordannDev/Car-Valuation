"""The data passed into an import and the results it returns."""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class ImportRow:
    """One cleaned-up source row, with its original row number kept for tracking progress."""

    source_record_number: int
    vin: str | None = None
    year: int | None = None
    make: str | None = None
    model: str | None = None
    trim: str | None = None

    # Match dealers by their name and full address, not their website alone.
    dealer_name: str | None = None
    dealer_street: str | None = None
    dealer_city: str | None = None
    dealer_state: str | None = None
    dealer_zip: str | None = None
    seller_website: str | None = None
    source_listing_id: str | None = None

    # Keep the dates and values from the source; leave missing information empty.
    first_seen_date: date | None = None
    last_seen_date: date | None = None
    dealer_vdp_last_seen_date: date | None = None
    listing_price: Decimal | None = None
    listing_mileage: int | None = None
    used: bool | None = None
    certified: bool | None = None
    listing_status: str | None = None

    # The importer checks these codes against the lists of supported values.
    drivetrain_code: str | None = None
    fuel_type_code: str | None = None
    exterior_color_code: str | None = None
    interior_color_code: str | None = None

    # Keep any parsing errors and warnings with the row they came from.
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImportSpec:
    """Details of the source file and how it was prepared; these must match when resuming."""

    source_name: str
    source_file_name: str
    source_file_hash: str
    source_file_uri: str
    normalization_version: str
    rules_hash: str
    manifest_uri: str
    artifact_uri: str
    artifact_hash: str
    total_rows: int

    # Use these source details when provided. Valuation uses its configured defaults for missing currency and mileage units.
    observed_at: datetime | None = None
    currency_code: str | None = None
    mileage_unit: str | None = None
    country_code: str | None = None


@dataclass(frozen=True)
class RowIssue:
    """An error or warning saved in the issue report for a specific source row."""

    source_record_number: int
    reasons: tuple[str, ...]
    severity: str = "error"


@dataclass
class ChunkResult:
    """Count accepted and rejected source rows, rather than database inserts or updates."""

    accepted: int = 0
    rejected: int = 0
    issues: list[RowIssue] = field(default_factory=list)
