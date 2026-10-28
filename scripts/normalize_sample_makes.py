"""Preview or apply confirmed make aliases in the local sample database.

Run with PYTHONPATH=src/api:src/cli:scripts/performance and the CLI virtualenv. Changes
are rolled back by default. Use --apply to save them, and supply a backup directory
outside the repository.
"""

import argparse
import hashlib
import json
from pathlib import Path

from app.catalog_cleanup import normalize_catalog_makes
from app.models import (
    Listing,
    ListingObservation,
    Make,
    Model,
    ModelYear,
    Trim,
    Vehicle,
)
from database import LocalDatabase
from reader import Config
from reader.makes import canonical_make
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session


def fingerprint(session, canonicalize):
    """Compare vehicle details while ignoring auto-generated catalog IDs."""
    digest = hashlib.sha256()
    fields = [
        column
        for column in Vehicle.__table__.columns
        if column.name not in ("model_year_id", "trim_id", "created_at", "updated_at")
    ]
    records = session.execute(
        select(
            *fields,
            Make.name,
            Model.normalized_name,
            ModelYear.year,
            Trim.normalized_name,
        )
        .select_from(Vehicle)
        .join(ModelYear)
        .join(Model)
        .join(Make)
        .outerjoin(Trim, Vehicle.trim_id == Trim.id)
        .order_by(Vehicle.id)
    )
    for record in records:
        values = list(record)
        values[len(fields)] = canonicalize(values[len(fields)])
        digest.update(json.dumps(values, default=str).encode())
    for model in (Listing, ListingObservation):
        for record in session.execute(select(model.__table__).order_by(model.id)):
            digest.update(json.dumps(list(record), default=str).encode())
    return digest.hexdigest()


def backup(session, directory):
    """Back up affected rows and their original vehicle links before merging anything."""
    directory.mkdir(parents=True, exist_ok=True)
    for model in (Make, Model, ModelYear, Trim, Vehicle):
        with (directory / f"{model.__tablename__}.jsonl").open("x") as stream:
            for record in session.execute(select(model.__table__)).mappings():
                stream.write(json.dumps(dict(record), default=str) + "\n")


def main(args):
    local = LocalDatabase.inspect("valueapi-mysql-1")
    engine = create_engine(local.configure_application("valueapi"))
    config = Config.load(args.config, [], [], args.aliases)
    canonicalize = lambda value: canonical_make(value, config.aliases["make"])
    try:
        with engine.connect() as lock:
            if lock.scalar(text("SELECT GET_LOCK('valueapi_importer', 0)")) != 1:
                raise RuntimeError("Another importer is active; refusing cleanup")
            try:
                with Session(engine) as session, session.begin():
                    before = fingerprint(session, canonicalize)
                    if args.apply:
                        backup(session, args.backup)
                    changes = normalize_catalog_makes(session, canonicalize)
                    after = fingerprint(session, canonicalize)
                    if before != after:
                        raise RuntimeError(
                            "Vehicle identities or listing history changed; rolling back"
                        )
                    report = {
                        "applied": args.apply,
                        "changes": changes,
                        "semantic_sha256": after,
                    }
                    if not args.apply:
                        session.rollback()
                if args.apply:
                    (args.backup / "merge-report.json").write_text(
                        json.dumps(report, indent=2)
                    )
                print(json.dumps(report, indent=2))
            finally:
                lock.execute(text("SELECT RELEASE_LOCK('valueapi_importer')"))
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--aliases", type=Path)
    arguments = parser.parse_args()
    if arguments.apply and arguments.backup is None:
        parser.error("--apply requires --backup pointing to a new external directory")
    main(arguments)
