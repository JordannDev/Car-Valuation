"""Vehicle makes and the keys used to match their names."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

from ._mixins import TimestampMixin

if TYPE_CHECKING:
    from .model import Model


class Make(TimestampMixin, db.Model):
    """A make named by the source. Do not guess the manufacturer from a model name."""

    __tablename__ = "makes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    normalized_name: Mapped[str] = mapped_column(
        String(120),
        unique=True,
        comment="Name with whitespace trimmed and case normalized, matched using binary comparison.",
    )

    models: Mapped[list[Model]] = relationship(
        back_populates="make", passive_deletes="all"
    )
