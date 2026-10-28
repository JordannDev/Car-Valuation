"""add_trims

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27 06:43:39.325246

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the trims table."""
    op.create_table(
        "trims",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("model_year_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "normalized_name",
            sa.String(length=200),
            nullable=False,
            comment="Match this name within its model year, using binary comparison.",
        ),
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
            ["model_year_id"],
            ["model_years.id"],
            name=op.f("fk_trims_model_year_id_model_years"),
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trims")),
        sa.UniqueConstraint("id", "model_year_id", name="uq_trims_id_model_year"),
        sa.UniqueConstraint(
            "model_year_id", "normalized_name", name="uq_trims_model_year_name"
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_bin",
        mysql_engine="InnoDB",
    )


def downgrade() -> None:
    """Remove the trims table."""
    op.drop_table("trims")
