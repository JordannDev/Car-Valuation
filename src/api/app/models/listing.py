"""Separate dealer listings for the same physical vehicle."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import (
    CHAR,
    BigInteger,
    Date,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

from ._mixins import TABLE_OPTIONS, TimestampMixin

if TYPE_CHECKING:
    from .dealer import Dealer
    from .vehicle import Vehicle


class Listing(TimestampMixin, db.Model):
    """One sale listing. The same VIN can appear in more than one listing over time."""

    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint(
            "source_name", "source_listing_id", name="uq_listings_source_id"
        ),
        UniqueConstraint(
            "source_name", "fallback_identity_key", name="uq_listings_source_fallback"
        ),
        Index("ix_listings_vehicle_dealer", "vehicle_id", "dealer_id"),
        TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(
        ForeignKey("vehicles.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    dealer_id: Mapped[int] = mapped_column(
        ForeignKey("dealers.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )

    # Identify this listing with the provider's ID or the fallback key.
    source_name: Mapped[str] = mapped_column(String(80))
    source_listing_id: Mapped[str | None] = mapped_column(String(255))
    fallback_identity_key: Mapped[str | None] = mapped_column(
        CHAR(64),
        comment="Match using the source, VIN, dealer, and first-seen date. Do not guess when those details are missing.",
    )

    # Keep the earliest and latest dates reported across this listing's snapshots.
    first_seen_date: Mapped[date | None] = mapped_column(Date)
    last_seen_date: Mapped[date | None] = mapped_column(Date)

    vehicle: Mapped[Vehicle] = relationship()
    dealer: Mapped[Dealer] = relationship()
