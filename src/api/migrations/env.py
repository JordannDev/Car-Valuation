"""Run migrations with the Flask app's database engine and table definitions."""

from logging.config import fileConfig

from alembic import context
from app import create_app
from app.extensions import db

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def run_migrations() -> None:
    """Use the same database settings as Flask when configuring Alembic."""
    app = create_app()

    with app.app_context():
        if context.is_offline_mode():
            context.configure(
                url=db.engine.url,
                target_metadata=db.metadata,
                literal_binds=True,
                dialect_opts={"paramstyle": "named"},
                compare_type=True,
                compare_server_default=True,
            )

            with context.begin_transaction():
                context.run_migrations()
        else:
            with db.engine.connect() as connection:
                context.configure(
                    connection=connection,
                    target_metadata=db.metadata,
                    compare_type=True,
                    compare_server_default=True,
                )

                with context.begin_transaction():
                    context.run_migrations()


run_migrations()
