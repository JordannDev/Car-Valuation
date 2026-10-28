"""Import and listing status values stored as MySQL enums."""

from enum import StrEnum


class ImportBatchStatus(StrEnum):
    """Track whether an imported file is running, complete, or available to resume."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ListingStatus(StrEnum):
    """Status reported by the provider. A missing status does not mean the vehicle was sold."""

    IN_TRANSIT = "in_transit"
