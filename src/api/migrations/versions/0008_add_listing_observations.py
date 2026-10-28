"""add_listing_observations

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-27 06:45:26.984732

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the listing snapshots table."""
    op.create_table(
        "listing_observations",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("listing_id", sa.BigInteger(), nullable=False),
        sa.Column("import_batch_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "source_record_number",
            sa.BigInteger(),
            nullable=False,
            comment="Original CSV record number, counting the header as 1 and the first data record as 2.",
        ),
        sa.Column(
            "observed_at",
            mysql.DATETIME(fsp=6),
            nullable=True,
            comment="When the source snapshot was taken, in UTC. This is separate from the import time.",
        ),
        sa.Column(
            "listing_price",
            sa.Numeric(precision=18, scale=2),
            nullable=True,
            comment="The listed asking price. Leave missing prices null rather than setting them to zero.",
        ),
        sa.Column(
            "currency_code",
            sa.CHAR(length=3),
            nullable=True,
            comment="Confirm the currency before using this snapshot for valuation.",
        ),
        sa.Column("listing_mileage", sa.BigInteger(), nullable=True),
        sa.Column(
            "mileage_unit",
            sa.String(length=2),
            nullable=True,
            comment="MI or KM. Confirm the unit before using this snapshot for valuation.",
        ),
        sa.Column("used", sa.Boolean(), nullable=True),
        sa.Column("certified", sa.Boolean(), nullable=True),
        sa.Column(
            "listing_status",
            sa.Enum("in_transit", name="listing_status"),
            nullable=True,
            comment="in_transit means the provider reports the vehicle is in transit. Null means unknown, not sold.",
        ),
        sa.Column(
            "first_seen_date",
            sa.Date(),
            nullable=True,
            comment="Date reported in this source snapshot.",
        ),
        sa.Column(
            "last_seen_date",
            sa.Date(),
            nullable=True,
            comment="Date reported in this source snapshot.",
        ),
        sa.Column("dealer_vdp_last_seen_date", sa.Date(), nullable=True),
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
            "mileage_unit IN ('MI', 'KM')",
            name=op.f("ck_listing_observations_mileage_unit"),
        ),
        sa.CheckConstraint(
            "certified IN (0, 1)",
            name=op.f("ck_listing_observations_certified_boolean"),
        ),
        sa.CheckConstraint(
            "listing_mileage >= 0",
            name=op.f("ck_listing_observations_mileage_nonnegative"),
        ),
        sa.CheckConstraint(
            "listing_price >= 0", name=op.f("ck_listing_observations_price_nonnegative")
        ),
        sa.CheckConstraint(
            "source_record_number >= 2",
            name=op.f("ck_listing_observations_source_record"),
        ),
        sa.CheckConstraint(
            "used IN (0, 1)", name=op.f("ck_listing_observations_used_boolean")
        ),
        sa.ForeignKeyConstraint(
            ["import_batch_id"],
            ["import_batches.id"],
            name=op.f("fk_listing_observations_import_batch_id_import_batches"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listings.id"],
            name=op.f("fk_listing_observations_listing_id_listings"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_listing_observations")),
        sa.UniqueConstraint(
            "import_batch_id",
            "source_record_number",
            name="uq_observations_batch_record",
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_bin",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_observations_listing_observed",
        "listing_observations",
        ["listing_id", "observed_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the listing snapshots table and the indexes used by its foreign keys."""
    op.drop_table("listing_observations")
