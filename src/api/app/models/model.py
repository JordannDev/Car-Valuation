"""Vehicle models grouped under their make."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

from ._mixins import TABLE_OPTIONS, TimestampMixin

if TYPE_CHECKING:
    from .make import Make
    from .model_year import ModelYear


class Model(TimestampMixin, db.Model):
    """A model name that is unique within its make."""

    __tablename__ = "models"
    __table_args__ = (
        UniqueConstraint("make_id", "normalized_name", name="uq_models_make_name"),
        TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    make_id: Mapped[int] = mapped_column(
        ForeignKey("makes.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    name: Mapped[str] = mapped_column(String(160))
    normalized_name: Mapped[str] = mapped_column(
        String(160), comment="Match this name within its make, using binary comparison."
    )

    make: Mapped[Make] = relationship(back_populates="models")
    model_years: Mapped[list[ModelYear]] = relationship(
        back_populates="model", passive_deletes="all"
    )
