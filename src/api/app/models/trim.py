"""Trim names grouped under a model and year."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

from ._mixins import TABLE_OPTIONS, TimestampMixin

if TYPE_CHECKING:
    from .model_year import ModelYear


class Trim(TimestampMixin, db.Model):
    """A trim name for a model year. Engine and drivetrain details belong to the vehicle."""

    __tablename__ = "trims"
    __table_args__ = (
        UniqueConstraint(
            "model_year_id", "normalized_name", name="uq_trims_model_year_name"
        ),
        UniqueConstraint("id", "model_year_id", name="uq_trims_id_model_year"),
        TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    model_year_id: Mapped[int] = mapped_column(
        ForeignKey("model_years.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(
        String(200),
        comment="Match this name within its model year, using binary comparison.",
    )

    model_year: Mapped[ModelYear] = relationship(back_populates="trims")
