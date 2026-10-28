"""Saved listing snapshots that keep the history of asking prices and mileage."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

from ._mixins import TABLE_OPTIONS, TimestampMixin
from .enums import ListingStatus

if TYPE_CHECKING:
    from .import_batch import ImportBatch
    from .listing import Listing


class ListingObservation(TimestampMixin, db.Model):
    """One source row in one import batch, even if the import is retried."""

    __tablename__ = "listing_observations"
    __table_args__ = (
        UniqueConstraint(
            "import_batch_id",
            "source_record_number",
            name="uq_observations_batch_record",
        ),
        Index("ix_observations_listing_observed", "listing_id", "observed_at"),
        CheckConstraint("source_record_number >= 2", name="source_record"),
        CheckConstraint("listing_price >= 0", name="price_nonnegative"),
        CheckConstraint("listing_mileage >= 0", name="mileage_nonnegative"),
        CheckConstraint("mileage_unit IN ('MI', 'KM')", name="mileage_unit"),
        CheckConstraint("used IN (0, 1)", name="used_boolean"),
        CheckConstraint("certified IN (0, 1)", name="certified_boolean"),
        TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listings.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    import_batch_id: Mapped[int] = mapped_column(
        ForeignKey("import_batches.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )

    # Keep source details separate from timestamps set by the database.
    source_record_number: Mapped[int] = mapped_column(
        BigInteger,
        comment="Original CSV record number, counting the header as 1 and the first data record as 2.",
    )
    observed_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6),
        comment="When the source snapshot was taken, in UTC. This is separate from the import time.",
    )

    # Save the original amounts and units. Convert them only when reading data for
    # valuation.
    listing_price: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2),
        comment="The listed asking price. Leave missing prices null rather than setting them to zero.",
    )
    currency_code: Mapped[str | None] = mapped_column(
        CHAR(3),
        comment="Confirm the currency before using this snapshot for valuation.",
    )
    listing_mileage: Mapped[int | None] = mapped_column(BigInteger)
    mileage_unit: Mapped[str | None] = mapped_column(
        String(2),
        comment="MI or KM. Confirm the unit before using this snapshot for valuation.",
    )
    used: Mapped[bool | None]
    certified: Mapped[bool | None]
    listing_status: Mapped[ListingStatus | None] = mapped_column(
        Enum(
            ListingStatus,
            name="listing_status",
            values_callable=lambda statuses: [status.value for status in statuses],
            validate_strings=True,
        ),
        comment="in_transit means the provider reports the vehicle is in transit. Null means unknown, not sold.",
    )

    # Leave missing source dates empty; the import time does not tell us when a listing
    # was observed.
    first_seen_date: Mapped[date | None] = mapped_column(
        Date, comment="Date reported in this source snapshot."
    )
    last_seen_date: Mapped[date | None] = mapped_column(
        Date, comment="Date reported in this source snapshot."
    )
    dealer_vdp_last_seen_date: Mapped[date | None] = mapped_column(Date)

    listing: Mapped[Listing] = relationship()
    import_batch: Mapped[ImportBatch] = relationship()
