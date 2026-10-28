"""Vehicle models for a particular year."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, SmallInteger, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

from ._mixins import TABLE_OPTIONS, TimestampMixin

if TYPE_CHECKING:
    from .model import Model
    from .trim import Trim


class ModelYear(TimestampMixin, db.Model):
    """One model in one year, with its trims stored separately."""

    __tablename__ = "model_years"
    __table_args__ = (
        UniqueConstraint("model_id", "year", name="uq_model_years_model_year"),
        TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    model_id: Mapped[int] = mapped_column(
        ForeignKey("models.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    year: Mapped[int] = mapped_column(SmallInteger)

    model: Mapped[Model] = relationship(back_populates="model_years")

    trims: Mapped[list[Trim]] = relationship(
        back_populates="model_year", passive_deletes="all"
    )
