"""Cache and database settings shared by the API and Alembic."""

import math
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL

DEFAULT_CATALOG_CACHE_TTL_SECONDS = 900.0


def get_catalog_cache_ttl_seconds() -> float:
    """Read how long to cache catalog responses and require a positive, finite duration."""

    raw_value = os.environ.get(
        "CATALOG_CACHE_TTL_SECONDS", str(DEFAULT_CATALOG_CACHE_TTL_SECONDS)
    )

    try:
        ttl = float(raw_value)
    except ValueError as error:
        raise ValueError(
            "CATALOG_CACHE_TTL_SECONDS must be a positive finite number"
        ) from error

    if not math.isfinite(ttl) or ttl <= 0:
        raise ValueError("CATALOG_CACHE_TTL_SECONDS must be a positive finite number")

    return ttl


def get_database_url() -> str | URL:
    """Load local settings and build the MySQL connection URL with credentials handled safely."""

    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)

    # Use DATABASE_URL when supplied; otherwise build it from the individual MySQL
    # settings.
    if database_url := os.environ.get("DATABASE_URL"):
        return database_url

    password = os.environ.get("MYSQL_PASSWORD")
    if not password:
        raise ValueError(
            "Set MYSQL_PASSWORD in the root .env or environment, "
            "or supply DATABASE_URL. See .env.example."
        )

    # Build the URL with SQLAlchemy so special characters in credentials are handled
    # correctly.
    return URL.create(
        "mysql+pymysql",
        username=os.environ.get("MYSQL_USER", "valueapi"),
        password=password,
        host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        database=os.environ.get("MYSQL_DATABASE", "valueapi"),
        query={"charset": "utf8mb4"},
    )
