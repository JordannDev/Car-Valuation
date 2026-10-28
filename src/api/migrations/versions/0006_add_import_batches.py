"""add_import_batches

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-27 06:44:59.543876

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the import batches table."""
    op.create_table(
        "import_batches",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("source_name", sa.String(length=80), nullable=False),
        sa.Column("source_file_name", sa.String(length=255), nullable=False),
        sa.Column(
            "source_file_hash",
            sa.CHAR(length=64),
            nullable=False,
            comment="SHA-256 hash of the original source file.",
        ),
        sa.Column(
            "source_file_uri",
            sa.String(length=2048),
            nullable=False,
            comment="Location of the original file, saved outside the database.",
        ),
        sa.Column("normalization_version", sa.String(length=40), nullable=False),
        sa.Column(
            "rules_hash",
            sa.CHAR(length=64),
            nullable=False,
            comment="Hash of the settings and rule code used to prepare this file.",
        ),
        sa.Column(
            "manifest_uri",
            sa.String(length=2048),
            nullable=False,
            comment="Manifest saved for this run. Keep it unchanged and retain earlier manifests when reprocessing.",
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "running", "completed", "failed", name="import_batch_status"
            ),
            server_default="pending",
            nullable=False,
            comment="pending: waiting to start; running: importing; completed: available to query; failed: can be resumed.",
        ),
        sa.Column(
            "started_at",
            mysql.DATETIME(fsp=6),
            nullable=True,
            comment="When the import started, in UTC.",
        ),
        sa.Column(
            "completed_at",
            mysql.DATETIME(fsp=6),
            nullable=True,
            comment="When the import finished, in UTC.",
        ),
        sa.Column(
            "total_rows", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "accepted_rows",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "rejected_rows",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
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
        sa.CheckConstraint(
            "status <> 'completed' OR accepted_rows + rejected_rows = total_rows",
            name=op.f("ck_import_batches_completed_counts"),
        ),
        sa.CheckConstraint(
            "accepted_rows + rejected_rows <= total_rows",
            name=op.f("ck_import_batches_counts_total"),
        ),
        sa.CheckConstraint(
            "total_rows >= 0 AND accepted_rows >= 0 AND rejected_rows >= 0",
            name=op.f("ck_import_batches_counts_nonnegative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_batches")),
        sa.UniqueConstraint(
            "source_name", "source_file_hash", name="uq_import_batches_source_file"
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_bin",
        mysql_engine="InnoDB",
    )


def downgrade() -> None:
    """Remove the import batches table."""
    op.drop_table("import_batches")
