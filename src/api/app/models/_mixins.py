"""MySQL table settings and audit timestamps shared by the app's models."""

from datetime import datetime

from sqlalchemy import FetchedValue, String, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

TABLE_OPTIONS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_bin",
}


class TimestampMixin:
    """Let MySQL manage UTC timestamps and place them after the model's own columns."""

    __table_args__ = TABLE_OPTIONS

    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        server_default=text("CURRENT_TIMESTAMP(6)"),
        comment="When the row was created, in UTC. Keep this unchanged after creation.",
        sort_order=1000,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
        server_onupdate=FetchedValue(),
        comment="When the row last changed, in UTC. MySQL updates this automatically.",
        sort_order=1001,
    )


class AttributeLookupMixin(TimestampMixin):
    """Store supported attribute values, including broad categories that are not specific enough to identify a configuration."""

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(100))
    is_specific: Mapped[bool]
