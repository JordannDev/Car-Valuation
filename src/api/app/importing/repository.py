"""Find or create catalog records in batches and save each source snapshot."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC
from decimal import Decimal

from sqlalchemy import Connection, Table, select, tuple_
from sqlalchemy.dialects.mysql import insert

from app.models import (
    Color,
    Dealer,
    Drivetrain,
    FuelType,
    Listing,
    ListingObservation,
    Make,
    Model,
    ModelYear,
    Trim,
    Vehicle,
)

from .contracts import ChunkResult, ImportRow, ImportSpec, RowIssue
from .vocabulary import LOOKUPS

STATEMENT_ROWS = 500
Key = tuple[object, ...]
Values = dict[str, object]
IssueHandler = Callable[[ImportRow, str], None]
Catalog = dict[int, tuple[int, int | None]]


@dataclass(frozen=True)
class RowIdentity:
    """Dealer and listing keys calculated once for a valid source row."""

    dealer: str
    listing: Key


def batches[T](items: Sequence[T]) -> Iterator[Sequence[T]]:
    """Keep database statements small, even when the import chunk is large."""

    for start in range(0, len(items), STATEMENT_ROWS):
        yield items[start : start + STATEMENT_ROWS]


def normalized(value: str | None) -> str:
    """Clean up keys for matching while keeping the original display names."""

    return (value or "").strip().lower()


def identity_hash(parts: Sequence[object]) -> str:
    """Hash the identity parts together without letting separators in the text cause collisions."""

    # Encode the parts as JSON so separators inside a name cannot produce the same hash for different parts.
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def dealer_key(row: ImportRow) -> str:
    """Identify a dealer location by its cleaned-up name and full address."""

    # Country can be added later, so it must not change which dealer location this row
    # matches.
    return identity_hash(
        [
            normalized(value)
            for value in (
                row.dealer_name,
                row.dealer_street,
                row.dealer_city,
                row.dealer_state,
                row.dealer_zip,
            )
        ]
    )


def validation_errors(row: ImportRow) -> list[str]:
    """Check row values before finding or creating related database records."""

    # Keep errors from the source reader and add any problems that would prevent saving this row.
    errors = list(row.errors)
    if not row.vin or not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", row.vin):
        errors.append("VIN must contain 17 valid uppercase characters")

    if row.year is None or not 1886 <= row.year <= 2200:
        errors.append("year must be between 1886 and 2200")

    for field, maximum in (
        ("make", 120),
        ("model", 160),
        ("trim", 200),
        ("dealer_name", 255),
        ("dealer_street", 255),
        ("dealer_city", 120),
        ("dealer_state", 80),
        ("dealer_zip", 20),
        ("seller_website", 2048),
        ("source_listing_id", 255),
    ):
        value = getattr(row, field)
        if value is not None and len(value) > maximum:
            errors.append(f"{field} exceeds {maximum} characters")

    if not normalized(row.make) or not normalized(row.model):
        errors.append("make and model are required")

    if not all(
        normalized(getattr(row, field))
        for field in (
            "dealer_name",
            "dealer_street",
            "dealer_city",
            "dealer_state",
            "dealer_zip",
        )
    ):
        errors.append("complete dealer name and location are required")

    if not normalized(row.source_listing_id) and row.first_seen_date is None:
        errors.append("first seen date is required without a provider listing ID")

    # Reject values the database cannot store before they cause the entire chunk to fail.
    if row.listing_price is not None:
        price = row.listing_price
        if not price.is_finite() or price < 0 or price > Decimal("9999999999999999.99"):
            errors.append("price is outside database precision")
        elif price != price.quantize(Decimal("0.01")):
            errors.append("price has more than two decimal places")

    if (
        row.listing_mileage is not None
        and not 0 <= row.listing_mileage <= 9223372036854775807
    ):
        errors.append("mileage is outside database range")

    if row.listing_status not in (None, "in_transit"):
        errors.append("unknown listing status")

    if (
        row.first_seen_date
        and row.last_seen_date
        and row.last_seen_date < row.first_seen_date
    ):
        errors.append("last seen date precedes first seen date")

    return errors


class Resolver:
    """Find or create records in batches. Dry runs use temporary IDs without saving anything."""

    def __init__(self, connection: Connection, dry_run: bool):
        self.connection = connection
        self.dry_run = dry_run
        self.next_id = -1

    def fetch(
        self, table: Table, columns: tuple[str, ...], keys: Sequence[Key]
    ) -> dict[Key, Values]:
        """Look up existing records in small batches to keep queries manageable."""

        found: dict[Key, Values] = {}

        for chunk in batches(keys):
            predicate = tuple_(*(table.c[name] for name in columns)).in_(chunk)

            for record in self.connection.execute(
                select(table).where(predicate)
            ).mappings():
                found[tuple(record[name] for name in columns)] = dict(record)

        return found

    def write(
        self, table: Table, rows: Sequence[Values], updates: tuple[str, ...] = ()
    ) -> None:
        """Insert rows or update the allowed fields, keeping existing identity fields intact."""

        if self.dry_run:
            return

        for chunk in batches(rows):
            # Let the driver send rows in batches instead of building thousands of
            # repeated SQL expressions.
            statement = insert(table)

            # When nothing needs updating, leave existing names and creation times
            # alone.
            assignments = {name: statement.inserted[name] for name in updates}
            self.connection.execute(
                statement.on_duplicate_key_update(
                    assignments
                    or {
                        next(iter(table.primary_key.columns)).name: next(
                            iter(table.primary_key.columns)
                        )
                    }
                ),
                list(chunk),
            )

    def ensure(
        self, table: Table, columns: tuple[str, ...], values: dict[Key, Values]
    ) -> dict[Key, Values]:
        """Reuse matching records and fill in missing ones, including during dry runs."""

        # Look up existing records first. Only missing ones need an insert or a
        # temporary dry-run ID.
        found = self.fetch(table, columns, list(values))
        missing = {key: data for key, data in values.items() if key not in found}
        if self.dry_run:
            for key, data in missing.items():
                found[key] = {**data, "id": self.next_id}
                self.next_id -= 1
        else:
            self.write(table, list(missing.values()))
            found.update(self.fetch(table, columns, list(missing)))

        return found


def resolve_catalog(resolver: Resolver, rows: list[ImportRow]) -> Catalog:
    """Find or create makes, models, years, and trims before linking vehicles to them."""

    makes = resolver.ensure(
        Make.metadata.tables[Make.__tablename__],
        ("normalized_name",),
        {
            (normalized(row.make),): {
                "name": row.make.strip(),
                "normalized_name": normalized(row.make),
            }
            for row in rows
        },
    )

    # Match each name within its parent so the same model name under different makes
    # stays separate.
    model_key = lambda row: (
        makes[(normalized(row.make),)]["id"],
        normalized(row.model),
    )
    models = resolver.ensure(
        Model.__table__,
        ("make_id", "normalized_name"),
        {
            model_key(row): {
                "make_id": model_key(row)[0],
                "name": row.model.strip(),
                "normalized_name": normalized(row.model),
            }
            for row in rows
        },
    )
    year_key = lambda row: (models[model_key(row)]["id"], row.year)
    years = resolver.ensure(
        ModelYear.__table__,
        ("model_id", "year"),
        {
            year_key(row): {"model_id": year_key(row)[0], "year": row.year}
            for row in rows
        },
    )
    trim_key = lambda row: (years[year_key(row)]["id"], normalized(row.trim))
    trims = resolver.ensure(
        Trim.__table__,
        ("model_year_id", "normalized_name"),
        {
            trim_key(row): {
                "model_year_id": trim_key(row)[0],
                "name": row.trim.strip(),
                "normalized_name": normalized(row.trim),
            }
            for row in rows
            if normalized(row.trim)
        },
    )

    return {
        row.source_record_number: (
            years[year_key(row)]["id"],
            trims[trim_key(row)]["id"] if normalized(row.trim) else None,
        )
        for row in rows
    }


def existing_vin_identities(
    connection: Connection, rows: list[ImportRow]
) -> dict[str, Key]:
    """Look up each VIN's existing make, model, and year before creating catalog records."""

    statement = select(
        Vehicle.vin, ModelYear.year, Make.normalized_name, Model.normalized_name
    ).select_from(Vehicle.__table__.join(ModelYear).join(Model).join(Make))
    found = {}

    for chunk in batches(list({row.vin for row in rows})):
        for record in connection.execute(statement.where(Vehicle.vin.in_(chunk))):
            found[record[0]] = tuple(record[1:])

    return found


def listing_owners(
    connection: Connection, source: str, rows: list[ImportRow]
) -> dict[str, Key]:
    """Check who owns a provider listing ID before creating records for the row."""

    statement = (
        select(Listing.source_listing_id, Vehicle.vin, Dealer.identity_key)
        .select_from(Listing.__table__.join(Vehicle).join(Dealer))
        .where(Listing.source_name == source)
    )
    owners = {}

    for chunk in batches(
        list({row.source_listing_id for row in rows if row.source_listing_id})
    ):
        for record in connection.execute(
            statement.where(Listing.source_listing_id.in_(chunk))
        ):
            owners[record[0]] = tuple(record[1:])

    return owners


def lookup_codes(resolver: Resolver) -> dict[str, set[str]]:
    """Add supported codes and leave unrecognized labels unmatched."""

    for model, vocabulary in LOOKUPS.items():
        resolver.write(
            model.__table__,
            [
                {"code": code, "display_name": label, "is_specific": specific}
                for code, (label, specific) in vocabulary.items()
            ],
        )

    return {
        field: set(resolver.connection.scalars(select(model.code)))
        | set(LOOKUPS[model])
        for field, model in (
            ("drivetrain_code", Drivetrain),
            ("fuel_type_code", FuelType),
            ("exterior_color_code", Color),
            ("interior_color_code", Color),
        )
    }


def resolve_vehicles(
    resolver: Resolver,
    rows: list[ImportRow],
    catalog: Catalog,
    warn: IssueHandler,
) -> dict[Key, Values]:
    """Fill in missing vehicle details and report conflicts without overwriting known values."""

    table = Vehicle.__table__

    # Load what we already know about each VIN before filling in missing details.
    found = resolver.fetch(table, ("vin",), list({(row.vin,) for row in rows}))
    codes = lookup_codes(resolver)
    proposed: dict[Key, Values] = {}

    for row in rows:
        key = (row.vin,)
        year_id, trim_id = catalog[row.source_record_number]
        incoming = {"trim_id": trim_id}

        for field, allowed in codes.items():
            value = getattr(row, field)
            incoming[field] = value if value in allowed else None
            if value is not None and value not in allowed:
                warn(row, f"unknown {field} {value}; left unresolved")

        values = proposed.setdefault(
            key,
            {
                **found.get(key, {}),
                "vin": row.vin,
                "model_year_id": year_id,
            },
        )

        for field, value in incoming.items():
            previous = values.get(field)
            if previous is None:
                values[field] = value
            elif value is not None and previous != value:
                warn(row, f"conflicting {field}; preserved established value")

    # Limit updates to these fields and let MySQL keep unchanged rows' audit times
    # intact.
    fields = ("trim_id", *codes)
    payload = [
        {
            "vin": key[0],
            "model_year_id": value["model_year_id"],
            **{field: value.get(field) for field in fields},
        }
        for key, value in proposed.items()
    ]
    resolver.write(table, payload, tuple(fields))

    return resolver.ensure(table, ("vin",), proposed)


def resolve_dealers(
    resolver: Resolver,
    rows: list[ImportRow],
    spec: ImportSpec,
    identities: dict[int, RowIdentity],
    warn: IssueHandler,
) -> dict[Key, Values]:
    """Match dealer locations by their full address, not their website."""

    values = {
        (identities[row.source_record_number].dealer,): {
            "identity_key": identities[row.source_record_number].dealer,
            "name": row.dealer_name.strip(),
            "street": row.dealer_street.strip(),
            "city": row.dealer_city.strip(),
            "state_or_province": row.dealer_state.strip(),
            "postal_code": row.dealer_zip.strip(),
            "website": row.seller_website,
            "country_code": spec.country_code,
        }
        for row in rows
    }

    # Reuse the dealer location and fill in missing details without replacing
    # conflicting known values.
    found = resolver.ensure(Dealer.__table__, ("identity_key",), values)

    for row in rows:
        dealer = found[(identities[row.source_record_number].dealer,)]

        for field, incoming in (
            ("website", row.seller_website),
            ("country_code", spec.country_code),
        ):
            if dealer[field] is None:
                dealer[field] = incoming
            elif incoming is not None and incoming != dealer[field]:
                warn(row, f"conflicting dealer {field}; preserved established value")

    columns = tuple(next(iter(values.values())))
    resolver.write(
        Dealer.__table__,
        [{field: record[field] for field in columns} for record in found.values()],
        ("website", "country_code"),
    )

    return found


def listing_key(
    row: ImportRow, spec: ImportSpec, *, dealer_identity: str | None = None
) -> Key:
    """Use the provider's listing ID when available; otherwise build a key from the source details."""

    # Use the provider's ID when available. Otherwise combine the source, VIN, dealer,
    # and first-seen date.
    if row.source_listing_id:
        return ("source_listing_id", spec.source_name, row.source_listing_id)

    return (
        "fallback_identity_key",
        spec.source_name,
        identity_hash(
            [
                spec.source_name,
                row.vin,
                dealer_identity if dealer_identity is not None else dealer_key(row),
                row.first_seen_date.isoformat(),
            ]
        ),
    )


def row_identities(rows: list[ImportRow], spec: ImportSpec) -> dict[int, RowIdentity]:
    """Calculate each row's keys once and keep them only for this chunk."""

    identities = {}

    for row in rows:
        dealer = dealer_key(row)

        # Reuse the dealer hash when building the fallback listing key.
        listing = listing_key(row, spec, dealer_identity=dealer)
        identities[row.source_record_number] = RowIdentity(dealer, listing)

    return identities


def resolve_listings(
    resolver: Resolver,
    rows: list[ImportRow],
    spec: ImportSpec,
    vehicles: dict[Key, Values],
    dealers: dict[Key, Values],
    identities: dict[int, RowIdentity],
    reject: IssueHandler,
) -> dict[Key, Values]:
    """Keep listings tied to the same vehicle and dealer while extending their known date range."""

    table = Listing.__table__
    resolved: dict[Key, Values] = {}

    # Handle provider IDs and fallback keys separately because the database enforces
    # uniqueness for each.
    for identity_column in ("source_listing_id", "fallback_identity_key"):
        selected = [
            row
            for row in rows
            if identities[row.source_record_number].listing[0] == identity_column
        ]
        keys = list(
            {identities[row.source_record_number].listing[1:] for row in selected}
        )
        existing = resolver.fetch(table, ("source_name", identity_column), keys)
        proposed: dict[Key, Values] = {}

        for row in selected:
            identity = identities[row.source_record_number]
            key = identity.listing[1:]
            vehicle_id = vehicles[(row.vin,)]["id"]
            dealer_id = dealers[(identity.dealer,)]["id"]

            # Check both saved records and earlier rows in this chunk for conflicting
            # ownership.
            prior = proposed.get(key) or existing.get(key)
            if prior and (
                prior["vehicle_id"] != vehicle_id or prior["dealer_id"] != dealer_id
            ):
                reject(row, "listing identity conflicts with established VIN or dealer")
                continue

            values = proposed.setdefault(
                key,
                (
                    dict(prior)
                    if prior
                    else {
                        "source_name": spec.source_name,
                        "source_listing_id": row.source_listing_id,
                        "fallback_identity_key": (
                            key[1]
                            if identity_column == "fallback_identity_key"
                            else None
                        ),
                        "vehicle_id": vehicle_id,
                        "dealer_id": dealer_id,
                        "first_seen_date": None,
                        "last_seen_date": None,
                    }
                ),
            )

            # Keep the earliest first-seen date and the latest last-seen date reported
            # so far.
            for field, choose in (("first_seen_date", min), ("last_seen_date", max)):
                dates = [
                    value
                    for value in (values[field], getattr(row, field))
                    if value is not None
                ]
                values[field] = choose(dates) if dates else None

        columns = (
            "source_name",
            "source_listing_id",
            "fallback_identity_key",
            "vehicle_id",
            "dealer_id",
            "first_seen_date",
            "last_seen_date",
        )
        resolver.write(
            table,
            [{name: value[name] for name in columns} for value in proposed.values()],
            ("first_seen_date", "last_seen_date"),
        )
        found = resolver.ensure(table, ("source_name", identity_column), proposed)
        resolved.update(
            {(identity_column, *key): value for key, value in found.items()}
        )

    return resolved


def process_rows(
    connection: Connection,
    rows: list[ImportRow],
    batch_id: int,
    spec: ImportSpec,
    *,
    dry_run: bool = False,
) -> ChunkResult:
    """Process a chunk without committing. Count accepted and rejected source rows."""

    result = ChunkResult()
    rejected: set[int] = set()

    def reject(row: ImportRow, reason: str) -> None:
        rejected.add(row.source_record_number)
        result.issues.append(RowIssue(row.source_record_number, (reason,)))

    def warn(row: ImportRow, reason: str) -> None:
        result.issues.append(RowIssue(row.source_record_number, (reason,), "warning"))

    # Validate rows first so rejected data cannot leave behind unused catalog records.
    eligible = []

    for row in rows:
        errors = validation_errors(row)
        if errors:
            rejected.add(row.source_record_number)
            result.issues.append(RowIssue(row.source_record_number, tuple(errors)))
        else:
            eligible.append(row)

        if row.warnings:
            result.issues.append(
                RowIssue(row.source_record_number, row.warnings, "warning")
            )

    # Check each VIN's make/model/year and each listing's owner before creating related
    # records.
    keys = row_identities(eligible, spec)
    identities = existing_vin_identities(connection, eligible)
    owners = listing_owners(connection, spec.source_name, eligible)

    for row in eligible:
        identity = (row.year, normalized(row.make), normalized(row.model))
        previous = identities.setdefault(row.vin, identity)
        if previous != identity:
            reject(row, "VIN conflicts with established make/model/year")

        if row.source_listing_id:
            owner = (row.vin, keys[row.source_record_number].dealer)
            if owners.setdefault(row.source_listing_id, owner) != owner:
                reject(row, "listing identity conflicts with established VIN or dealer")

    eligible = [row for row in eligible if row.source_record_number not in rejected]

    # Create or reuse the related records first, then save snapshots for accepted rows
    # only.
    if eligible:
        resolver = Resolver(connection, dry_run)
        catalog = resolve_catalog(resolver, eligible)
        vehicles = resolve_vehicles(resolver, eligible, catalog, warn)
        dealers = resolve_dealers(resolver, eligible, spec, keys, warn)
        listings = resolve_listings(
            resolver, eligible, spec, vehicles, dealers, keys, reject
        )
        observations = [
            {
                "listing_id": listings[keys[row.source_record_number].listing]["id"],
                "import_batch_id": batch_id,
                "source_record_number": row.source_record_number,
                "observed_at": (
                    spec.observed_at.astimezone(UTC).replace(tzinfo=None)
                    if spec.observed_at
                    else None
                ),
                "currency_code": spec.currency_code,
                "mileage_unit": spec.mileage_unit,
                **{
                    field: getattr(row, field)
                    for field in (
                        "listing_price",
                        "listing_mileage",
                        "used",
                        "certified",
                        "listing_status",
                        "first_seen_date",
                        "last_seen_date",
                        "dealer_vdp_last_seen_date",
                    )
                },
            }
            for row in eligible
            if row.source_record_number not in rejected
        ]
        resolver.write(
            ListingObservation.metadata.tables[ListingObservation.__tablename__],
            observations,
        )

    # Count source rows, not the number of database inserts or updates.
    result.rejected = len(rejected)
    result.accepted = len(rows) - result.rejected

    return result
