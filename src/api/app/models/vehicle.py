"""Vehicles matched by VIN, with details filled in only when the source supports them."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    SmallInteger,
    and_,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

from ._mixins import TABLE_OPTIONS, TimestampMixin
from .trim import Trim

if TYPE_CHECKING:
    from .body_type import BodyType
    from .color import Color
    from .drivetrain import Drivetrain
    from .fuel_type import FuelType
    from .model_year import ModelYear
    from .powertrain_type import PowertrainType


class Vehicle(TimestampMixin, db.Model):
    """Keep one vehicle per valid VIN and report conflicting details instead of overwriting them."""

    __tablename__ = "vehicles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["trim_id", "model_year_id"],
            ["trims.id", "trims.model_year_id"],
            name="fk_vehicles_trim_year",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        Index("ix_vehicles_model_year_trim", "model_year_id", "trim_id"),
        CheckConstraint(
            "engine_displacement_liters >= 0", name="engine_displacement_nonnegative"
        ),
        CheckConstraint("engine_cylinders >= 0", name="engine_cylinders_nonnegative"),
        TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # Use the VIN to follow the same vehicle across dealer listings and source
    # snapshots.
    vin: Mapped[str] = mapped_column(
        CHAR(17),
        unique=True,
        comment="Validated uppercase VIN identifying the physical vehicle across its listings.",
    )
    model_year_id: Mapped[int] = mapped_column(
        ForeignKey("model_years.id", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    trim_id: Mapped[int | None] = mapped_column(BigInteger)

    # Leave optional details empty until the source provides a supported value.
    drivetrain_code: Mapped[str | None] = mapped_column(
        ForeignKey("drivetrains.code", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    body_type_code: Mapped[str | None] = mapped_column(
        ForeignKey("body_types.code", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    fuel_type_code: Mapped[str | None] = mapped_column(
        ForeignKey("fuel_types.code", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    powertrain_type_code: Mapped[str | None] = mapped_column(
        ForeignKey("powertrain_types.code", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    engine_displacement_liters: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    engine_cylinders: Mapped[int | None] = mapped_column(SmallInteger)
    exterior_color_code: Mapped[str | None] = mapped_column(
        ForeignKey("colors.code", ondelete="RESTRICT", onupdate="RESTRICT")
    )
    interior_color_code: Mapped[str | None] = mapped_column(
        ForeignKey("colors.code", ondelete="RESTRICT", onupdate="RESTRICT")
    )

    # Use these relationships to read catalog details. The database checks that a trim
    # belongs to the vehicle's model year.
    model_year: Mapped[ModelYear] = relationship(foreign_keys=[model_year_id])

    # Only synchronize trim_id here. Set model_year_id explicitly so the database can
    # validate the pair.
    trim: Mapped[Trim | None] = relationship(
        primaryjoin=lambda: and_(
            Vehicle.trim_id == Trim.id, Vehicle.model_year_id == Trim.model_year_id
        ),
        foreign_keys=[trim_id],
    )
    drivetrain: Mapped[Drivetrain | None] = relationship()
    body_type: Mapped[BodyType | None] = relationship()
    fuel_type: Mapped[FuelType | None] = relationship()
    powertrain_type: Mapped[PowertrainType | None] = relationship()
    exterior_color: Mapped[Color | None] = relationship(
        foreign_keys=[exterior_color_code]
    )
    interior_color: Mapped[Color | None] = relationship(
        foreign_keys=[interior_color_code]
    )
