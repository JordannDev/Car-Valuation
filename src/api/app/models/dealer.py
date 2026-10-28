"""Dealer locations matched by their name and full address."""

from sqlalchemy import CHAR, BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db

from ._mixins import TimestampMixin


class Dealer(TimestampMixin, db.Model):
    """One dealer location. A matching name or website alone is not enough to merge locations."""

    __tablename__ = "dealers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    identity_key: Mapped[str] = mapped_column(
        CHAR(64),
        unique=True,
        comment="Hash of the cleaned-up dealer name and full address.",
    )

    # Fill in optional contact and address details when they become available.
    website: Mapped[str | None] = mapped_column(String(2048))
    street: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(120))
    state_or_province: Mapped[str | None] = mapped_column(String(80))
    postal_code: Mapped[str | None] = mapped_column(
        String(20),
        comment="Store as text to keep leading zeros and Canadian postal codes intact.",
    )
    country_code: Mapped[str | None] = mapped_column(CHAR(2))
