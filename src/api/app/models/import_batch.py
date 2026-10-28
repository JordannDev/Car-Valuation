"""Source file details and saved progress for imports that can be resumed."""

from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Enum,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db

from ._mixins import TABLE_OPTIONS, TimestampMixin
from .enums import ImportBatchStatus


class ImportBatch(TimestampMixin, db.Model):
    """One batch per source file, even after retries. Keep detailed reports and manifests outside the database."""

    __tablename__ = "import_batches"
    __table_args__ = (
        UniqueConstraint(
            "source_name", "source_file_hash", name="uq_import_batches_source_file"
        ),
        CheckConstraint(
            "total_rows >= 0 AND accepted_rows >= 0 AND rejected_rows >= 0",
            name="counts_nonnegative",
        ),
        CheckConstraint(
            "accepted_rows + rejected_rows <= total_rows", name="counts_total"
        ),
        CheckConstraint("last_processed_record >= 1", name="checkpoint_valid"),
        CheckConstraint(
            "status <> 'completed' OR accepted_rows + rejected_rows = total_rows",
            name="completed_counts",
        ),
        TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_name: Mapped[str] = mapped_column(String(80))
    source_file_name: Mapped[str] = mapped_column(String(255))
    source_file_hash: Mapped[str] = mapped_column(
        CHAR(64), comment="SHA-256 hash of the original source file."
    )
    source_file_uri: Mapped[str] = mapped_column(
        String(2048),
        comment="Location of the original file, saved outside the database.",
    )

    # Keep the processing settings and manifest so a resumed import can check that
    # nothing changed.
    normalization_version: Mapped[str] = mapped_column(String(40))
    rules_hash: Mapped[str] = mapped_column(
        CHAR(64),
        comment="Hash of the settings and rule code used to prepare this file.",
    )
    manifest_uri: Mapped[str] = mapped_column(
        String(2048),
        comment="Manifest saved for this run. Keep it unchanged and retain earlier manifests when reprocessing.",
    )

    # Valuation only reads data from completed batches.
    status: Mapped[ImportBatchStatus] = mapped_column(
        Enum(
            ImportBatchStatus,
            name="import_batch_status",
            values_callable=lambda statuses: [status.value for status in statuses],
            validate_strings=True,
        ),
        server_default=ImportBatchStatus.PENDING.value,
        comment="pending: waiting to start; running: importing; completed: available to query; failed: can be resumed.",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6), comment="When the import started, in UTC."
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6), comment="When the import finished, in UTC."
    )

    # Save the row counts and checkpoint together after each chunk.
    total_rows: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    accepted_rows: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    rejected_rows: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    last_processed_record: Mapped[int] = mapped_column(
        BigInteger,
        server_default=text("1"),
        comment="Original row number of the last source record saved successfully.",
    )

    # Keep large source files outside the database and store their locations and hashes
    # here.
    artifact_uri: Mapped[str | None] = mapped_column(
        String(2048),
        comment="Saved cleaned-up data file. Keep it unchanged so the import can be resumed.",
    )
    artifact_hash: Mapped[str | None] = mapped_column(
        CHAR(64), comment="SHA-256 hash of the saved cleaned-up data file."
    )
    spec_hash: Mapped[str | None] = mapped_column(
        CHAR(64),
        comment="Hash of all source and observation details used to check a resumed import.",
    )
