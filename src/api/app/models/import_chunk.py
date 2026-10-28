"""Links between saved chunk progress and issue reports stored outside the database."""

from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db

from ._mixins import TABLE_OPTIONS


class ImportChunk(db.Model):
    """An issue report belongs to the import only after its chunk transaction commits."""

    __tablename__ = "import_chunks"
    __table_args__ = (
        UniqueConstraint(
            "import_batch_id", "last_record", name="uq_import_chunks_batch_last"
        ),
        TABLE_OPTIONS,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    import_batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"))

    # Use the source row range to link this saved chunk to its issue report.
    first_record: Mapped[int] = mapped_column(BigInteger)
    last_record: Mapped[int] = mapped_column(BigInteger)
    report_uri: Mapped[str] = mapped_column(String(2048))
