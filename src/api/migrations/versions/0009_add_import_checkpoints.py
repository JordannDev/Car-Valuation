"""add_import_checkpoints

Revision ID: 0009
Revises: 0008
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add saved import progress and details of the prepared source files."""
    op.add_column(
        "import_batches",
        sa.Column(
            "last_processed_record",
            sa.BigInteger(),
            server_default=sa.text("1"),
            nullable=False,
            comment="Original row number of the last source record saved successfully.",
        ),
    )
    op.add_column(
        "import_batches",
        sa.Column(
            "artifact_uri",
            sa.String(length=2048),
            nullable=True,
            comment="Saved cleaned-up data file. Keep it unchanged so the import can be resumed.",
        ),
    )
    op.add_column(
        "import_batches",
        sa.Column(
            "artifact_hash",
            sa.CHAR(length=64),
            nullable=True,
            comment="SHA-256 hash of the saved cleaned-up data file.",
        ),
    )
    op.create_check_constraint(
        op.f("ck_import_batches_checkpoint_valid"),
        "import_batches",
        "last_processed_record >= 1",
    )
    op.add_column(
        "import_batches",
        sa.Column(
            "spec_hash",
            sa.CHAR(64),
            nullable=True,
            comment="Hash of all source and observation details used to check a resumed import.",
        ),
    )
    op.create_table(
        "import_chunks",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "import_batch_id",
            sa.BigInteger(),
            sa.ForeignKey("import_batches.id"),
            nullable=False,
        ),
        sa.Column("first_record", sa.BigInteger(), nullable=False),
        sa.Column("last_record", sa.BigInteger(), nullable=False),
        sa.Column("report_uri", sa.String(2048), nullable=False),
        sa.UniqueConstraint(
            "import_batch_id", "last_record", name="uq_import_chunks_batch_last"
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_bin",
    )


def downgrade() -> None:
    """Remove the saved progress and prepared-file fields."""
    op.drop_table("import_chunks")
    op.drop_column("import_batches", "spec_hash")
    op.drop_constraint(
        op.f("ck_import_batches_checkpoint_valid"), "import_batches", type_="check"
    )
    op.drop_column("import_batches", "artifact_hash")
    op.drop_column("import_batches", "artifact_uri")
    op.drop_column("import_batches", "last_processed_record")
