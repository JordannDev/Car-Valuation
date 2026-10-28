"""Read and check vehicle data without a database connection."""

from .config import Config
from .pipeline import build

__all__ = ["Config", "build"]
