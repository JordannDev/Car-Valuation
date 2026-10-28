"""add_dealers

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-27 06:44:35.135516

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the dealer locations table."""
    op.create_table(
        "dealers",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "identity_key",
            sa.CHAR(length=64),
            nullable=False,
            comment="Hash of the cleaned-up dealer name and full address.",
        ),
        sa.Column("website", sa.String(length=2048), nullable=True),
        sa.Column("street", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("state_or_province", sa.String(length=80), nullable=True),
        sa.Column(
            "postal_code",
            sa.String(length=20),
            nullable=True,
            comment="Store as text to keep leading zeros and Canadian postal codes intact.",
        ),
        sa.Column("country_code", sa.CHAR(length=2), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dealers")),
        sa.UniqueConstraint("identity_key", name=op.f("uq_dealers_identity_key")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_bin",
        mysql_engine="InnoDB",
    )


def downgrade() -> None:
    """Remove the dealer locations table."""
    op.drop_table("dealers")
