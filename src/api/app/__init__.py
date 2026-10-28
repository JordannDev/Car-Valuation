"""Create and configure the API."""

from flask import Flask
from sqlalchemy.engine import make_url

from .config import get_catalog_cache_ttl_seconds, get_database_url
from .extensions import db


def create_app() -> Flask:
    """Create the API app and set up its database connection settings."""

    # Keep settings on each app instance so tests and workers can be set up
    # independently.
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = get_database_url()
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
    app.config["CATALOG_CACHE_TTL_SECONDS"] = get_catalog_cache_ttl_seconds()

    if make_url(app.config["SQLALCHEMY_DATABASE_URI"]).get_backend_name() == "mysql":
        # Use UTC for MySQL timestamps and strict writes so invalid values are rejected.
        app.config["SQLALCHEMY_ENGINE_OPTIONS"]["connect_args"] = {
            "init_command": (
                "SET time_zone = '+00:00', sql_mode = "
                "'STRICT_TRANS_TABLES,ONLY_FULL_GROUP_BY,NO_ZERO_DATE,"
                "NO_ZERO_IN_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION'"
            )
        }

    db.init_app(app)

    # Import every model so migrations can find all table definitions.
    from . import models  # noqa: F401
    from .catalog import catalog_blueprint, init_catalog_cache
    from .location import location_blueprint
    from .valuation import init_valuation_settings, valuation_blueprint

    # Check pricing settings before registering routes so invalid configuration fails at
    # startup.
    init_valuation_settings(app)
    init_catalog_cache(app)
    app.register_blueprint(catalog_blueprint, url_prefix="/api")
    app.register_blueprint(location_blueprint, url_prefix="/api")
    app.register_blueprint(valuation_blueprint, url_prefix="/api")

    @app.get("/health")
    def health() -> dict[str, str]:
        """Report that the API is responding."""

        return {"status": "ok"}

    return app
