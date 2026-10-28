"""add_listings

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-27 06:45:13.323432

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the listings table."""
    op.create_table(
        "listings",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("vehicle_id", sa.BigInteger(), nullable=False),
        sa.Column("dealer_id", sa.BigInteger(), nullable=False),
        sa.Column("source_name", sa.String(length=80), nullable=False),
        sa.Column("source_listing_id", sa.String(length=255), nullable=True),
        sa.Column(
            "fallback_identity_key",
            sa.CHAR(length=64),
            nullable=True,
            comment="Match using the source, VIN, dealer, and first-seen date. Do not guess when those details are missing.",
        ),
        sa.Column("first_seen_date", sa.Date(), nullable=True),
        sa.Column("last_seen_date", sa.Date(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["dealer_id"],
            ["dealers.id"],
            name=op.f("fk_listings_dealer_id_dealers"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"],
            ["vehicles.id"],
            name=op.f("fk_listings_vehicle_id_vehicles"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_listings")),
        sa.UniqueConstraint(
            "source_name", "fallback_identity_key", name="uq_listings_source_fallback"
        ),
        sa.UniqueConstraint(
            "source_name", "source_listing_id", name="uq_listings_source_id"
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_bin",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_listings_vehicle_dealer",
        "listings",
        ["vehicle_id", "dealer_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the listings table and the indexes used by its foreign keys."""
    op.drop_table("listings")
