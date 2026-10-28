"""add_vehicles

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-27 06:44:27.070228

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the vehicles table."""
    op.create_table(
        "vehicles",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "vin",
            sa.CHAR(length=17),
            nullable=False,
            comment="Validated uppercase VIN identifying the physical vehicle across its listings.",
        ),
        sa.Column("model_year_id", sa.BigInteger(), nullable=False),
        sa.Column("trim_id", sa.BigInteger(), nullable=True),
        sa.Column("drivetrain_code", sa.String(length=32), nullable=True),
        sa.Column("body_type_code", sa.String(length=32), nullable=True),
        sa.Column("fuel_type_code", sa.String(length=32), nullable=True),
        sa.Column("powertrain_type_code", sa.String(length=32), nullable=True),
        sa.Column(
            "engine_displacement_liters",
            sa.Numeric(precision=5, scale=2),
            nullable=True,
        ),
        sa.Column("engine_cylinders", sa.SmallInteger(), nullable=True),
        sa.Column("exterior_color_code", sa.String(length=32), nullable=True),
        sa.Column("interior_color_code", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
            nullable=False,
            comment="When the row was created, in UTC. Keep this unchanged after creation.",
        ),
        sa.Column(
            "updated_at",
            mysql.DATETIME(fsp=6),
            server_default=sa.text(
                "CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"
            ),
            nullable=False,
            comment="When the row last changed, in UTC. MySQL updates this automatically.",
        ),
        sa.CheckConstraint(
            "engine_cylinders >= 0",
            name=op.f("ck_vehicles_engine_cylinders_nonnegative"),
        ),
        sa.CheckConstraint(
            "engine_displacement_liters >= 0",
            name=op.f("ck_vehicles_engine_displacement_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["body_type_code"],
            ["body_types.code"],
            name=op.f("fk_vehicles_body_type_code_body_types"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["drivetrain_code"],
            ["drivetrains.code"],
            name=op.f("fk_vehicles_drivetrain_code_drivetrains"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["exterior_color_code"],
            ["colors.code"],
            name=op.f("fk_vehicles_exterior_color_code_colors"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fuel_type_code"],
            ["fuel_types.code"],
            name=op.f("fk_vehicles_fuel_type_code_fuel_types"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["interior_color_code"],
            ["colors.code"],
            name=op.f("fk_vehicles_interior_color_code_colors"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_year_id"],
            ["model_years.id"],
            name=op.f("fk_vehicles_model_year_id_model_years"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["powertrain_type_code"],
            ["powertrain_types.code"],
            name=op.f("fk_vehicles_powertrain_type_code_powertrain_types"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["trim_id", "model_year_id"],
            ["trims.id", "trims.model_year_id"],
            name="fk_vehicles_trim_year",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehicles")),
        sa.UniqueConstraint("vin", name=op.f("uq_vehicles_vin")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_bin",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_vehicles_model_year_trim",
        "vehicles",
        ["model_year_id", "trim_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the vehicles table and the indexes used by its foreign keys."""
    op.drop_table("vehicles")
