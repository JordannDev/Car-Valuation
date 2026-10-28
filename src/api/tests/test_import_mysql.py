"""Test imports against a MySQL database set aside for these tests."""

import os
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app import create_app
from app.extensions import db
from app.importing.contracts import ImportRow, ImportSpec
from app.models import ImportBatch, Listing, ListingObservation, Vehicle
from sqlalchemy import func, select
from sqlalchemy.engine import make_url


@pytest.fixture(scope="module")
def engine():
    """Only allow a disposable test database because cleanup deletes its data."""
    url = os.environ.get("VALUEAPI_IMPORT_TEST_URL")
    if not url:
        pytest.skip("Set VALUEAPI_IMPORT_TEST_URL to a disposable MySQL database")
    if make_url(url).database != "valueapi_import_test":
        pytest.fail("Integration tests require the database valueapi_import_test")
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        app = create_app()
        with app.app_context():
            config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
            config.set_main_option(
                "script_location", str(Path(__file__).parents[1] / "migrations")
            )
            command.upgrade(config, "head")
            yield db.engine
            db.session.remove()
            db.engine.dispose()
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


@pytest.fixture(autouse=True)
def empty_database(engine):
    with engine.begin() as connection:
        for table in reversed(db.metadata.sorted_tables):
            connection.execute(table.delete())


@pytest.fixture
def spec(tmp_path):
    return ImportSpec(
        source_name="inventory",
        source_file_name="inventory.csv",
        source_file_hash="a" * 64,
        source_file_uri=str(tmp_path / "source.csv"),
        normalization_version="4",
        rules_hash="b" * 64,
        manifest_uri=str(tmp_path / "manifest.json"),
        artifact_uri=str(tmp_path / "normalized.parquet"),
        artifact_hash="c" * 64,
        total_rows=1,
    )


@pytest.fixture
def row():
    return ImportRow(
        source_record_number=2,
        vin="KNAFK4A61F5428652",
        year=2015,
        make="Kia",
        model="Forte",
        trim="LX",
        dealer_name="Example Auto",
        dealer_street="123 Main St",
        dealer_city="Test City",
        dealer_state="ON",
        dealer_zip="N1N 1N1",
        first_seen_date=date(2022, 1, 1),
        last_seen_date=date(2022, 2, 1),
        listing_price=Decimal("10000.50"),
        listing_mileage=0,
        used=False,
        certified=False,
    )


def count(engine, model):
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(model))


def test_initial_import_and_identical_file_are_idempotent(engine, spec, row):
    from app.importing.service import run_import

    result = run_import(engine, spec, [[row]])
    assert result["accepted_rows"] == 1
    run_import(engine, replace(spec, source_file_name="renamed.csv"), [[row]])
    assert count(engine, ImportBatch) == 1
    assert count(engine, Vehicle) == 1
    assert count(engine, Listing) == 1
    assert count(engine, ListingObservation) == 1
    with engine.connect() as connection:
        observation = (
            connection.execute(select(ListingObservation.__table__)).mappings().one()
        )
    assert observation["listing_price"] == Decimal("10000.50")
    assert observation["listing_mileage"] == 0
    assert observation["used"] is False
    assert observation["observed_at"] is None


@pytest.mark.parametrize("dry_run", [False, True])
def test_record_identities_are_computed_once_and_reused(
    engine, spec, row, monkeypatch, dry_run
):
    from app.importing import repository
    from app.importing.service import run_import

    rows = [
        row,
        replace(row, source_record_number=3),
        replace(row, source_record_number=4, source_listing_id="provider-123"),
        replace(row, source_record_number=5, dealer_name=None),
    ]
    expected_dealer = repository.dealer_key(row)
    expected_listing = repository.listing_key(row, spec)[2]
    original_hash = repository.identity_hash
    hashed_parts = []

    def track_hash(parts):
        hashed_parts.append(parts)
        return original_hash(parts)

    monkeypatch.setattr(repository, "identity_hash", track_hash)
    result = run_import(engine, replace(spec, total_rows=4), [rows], dry_run=dry_run)

    assert result["accepted_rows"] == 3
    assert result["rejected_rows"] == 1
    # These rows need three dealer hashes and two fallback listing hashes. Provider
    # listing IDs don't need hashing.
    assert len(hashed_parts) == 5
    if not dry_run:
        from app.models import Dealer

        with engine.connect() as connection:
            assert connection.scalar(select(Dealer.identity_key)) == expected_dealer
            assert (
                connection.scalar(
                    select(Listing.fallback_identity_key).where(
                        Listing.source_listing_id.is_(None)
                    )
                )
                == expected_listing
            )
        assert count(engine, Listing) == 2
        assert count(engine, ListingObservation) == 3


def test_overlap_preserves_observations_without_duplicate_entities(engine, spec, row):
    from app.importing.service import run_import

    run_import(engine, spec, [[row]])
    later = replace(spec, source_file_hash="d" * 64)
    run_import(engine, later, [[replace(row, listing_price=Decimal(9500))]])
    assert count(engine, Vehicle) == 1
    assert count(engine, Listing) == 1
    assert count(engine, ListingObservation) == 2


def test_make_cleanup_merges_catalog_collisions_and_preserves_observations(
    engine, spec, row
):
    from app.catalog_cleanup import normalize_catalog_makes
    from app.importing.service import run_import
    from app.models import Make, Model, ModelYear, Trim
    from reader import Config
    from reader.makes import canonical_make
    from sqlalchemy.orm import Session

    names = ["Harley Davidson", "Harley-Davidson", "Harley-Davidson&#174;"]
    rows = [
        replace(
            row,
            source_record_number=index + 2,
            vin=f"KNAFK4A61F542865{index}",
            make=name,
            model="Cruiser",
            trim="LX" if index < 2 else "Touring",
        )
        for index, name in enumerate(names)
    ]
    spec = replace(spec, total_rows=3)
    run_import(engine, spec, [rows])
    with engine.connect() as connection:
        before = connection.execute(select(ListingObservation.__table__)).all()
        vehicle_ids = connection.scalars(select(Vehicle.id).order_by(Vehicle.id)).all()
    canonicalize = lambda value: canonical_make(value, Config().aliases["make"])
    with Session(engine) as session:
        normalize_catalog_makes(session, canonicalize)
        session.rollback()
    assert count(engine, Make) == 3
    with Session(engine) as session, session.begin():
        assert len(normalize_catalog_makes(session, canonicalize)) == 2
        assert normalize_catalog_makes(session, canonicalize) == []
    assert count(engine, Make) == count(engine, Model) == count(engine, ModelYear) == 1
    assert count(engine, Trim) == 2
    with engine.connect() as connection:
        assert connection.execute(select(ListingObservation.__table__)).all() == before
        assert (
            connection.scalars(select(Vehicle.id).order_by(Vehicle.id)).all()
            == vehicle_ids
        )
        assert connection.execute(
            select(Trim.name)
            .join(Vehicle, Vehicle.trim_id == Trim.id)
            .order_by(Vehicle.id)
        ).scalars().all() == ["LX", "LX", "Touring"]
    canonical_rows = [replace(record, make="Harley-Davidson") for record in rows]
    result = run_import(
        engine, replace(spec, source_file_hash="d" * 64), [canonical_rows]
    )
    assert result["accepted_rows"] == 3
    assert count(engine, Vehicle) == count(engine, Listing) == 3
    assert count(engine, ListingObservation) == 6


def test_relisting_and_older_snapshot_do_not_collapse_history(engine, spec, row):
    from app.importing.service import run_import

    run_import(engine, spec, [[row]])
    older = replace(row, last_seen_date=date(2022, 1, 2))
    run_import(engine, replace(spec, source_file_hash="d" * 64), [[older]])
    relisting = replace(
        row, first_seen_date=date(2023, 1, 1), last_seen_date=date(2023, 2, 1)
    )
    run_import(engine, replace(spec, source_file_hash="e" * 64), [[relisting]])
    assert count(engine, Listing) == 2
    with engine.connect() as connection:
        dates = connection.execute(
            select(Listing.first_seen_date, Listing.last_seen_date)
        ).all()
    assert (date(2022, 1, 1), date(2022, 2, 1)) in dates


def test_invalid_identity_rejected_and_checkpoint_counts_every_record(
    engine, spec, row
):
    from app.importing.service import run_import

    invalid = replace(row, source_record_number=3, first_seen_date=None)
    result = run_import(engine, replace(spec, total_rows=2), [[row, invalid]])
    assert result["accepted_rows"] == 1
    assert result["rejected_rows"] == 1
    assert count(engine, ListingObservation) == 1
    with engine.connect() as connection:
        batch = connection.execute(select(ImportBatch.__table__)).mappings().one()
    assert batch["last_processed_record"] == 3
    assert batch["status"].value == "completed"


def test_dry_run_executes_no_mutations(engine, spec, row):
    from app.importing.service import run_import
    from sqlalchemy import event

    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().split()[0].upper())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = run_import(engine, spec, [[row]], dry_run=True)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert result["accepted_rows"] == 1
    assert not ({"INSERT", "UPDATE", "DELETE", "REPLACE"} & set(statements))
    assert count(engine, ImportBatch) == 0


def test_resume_commits_no_duplicate_records(engine, spec, row, monkeypatch):
    from app.importing import service

    real_process = service.process_rows
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated interruption")
        return real_process(*args, **kwargs)

    monkeypatch.setattr(service, "process_rows", fail_second)
    second = replace(row, source_record_number=3)
    expanded = replace(spec, total_rows=2)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        service.run_import(engine, expanded, [[row], [second]])
    with engine.connect() as connection:
        batch_id = connection.scalar(select(ImportBatch.id))
    assert count(engine, ListingObservation) == 1
    monkeypatch.setattr(service, "process_rows", real_process)
    result = service.run_import(
        engine, expanded, [[row, second]], resume_batch_id=batch_id
    )
    assert result["accepted_rows"] == 2
    assert count(engine, ListingObservation) == 2


def test_vehicle_conflicts_do_not_replace_established_identity(engine, spec, row):
    from app.importing.service import run_import

    run_import(engine, spec, [[row]])
    result = run_import(
        engine, replace(spec, source_file_hash="d" * 64), [[replace(row, make="Ford")]]
    )
    assert result["rejected_rows"] == 1
    assert count(engine, Vehicle) == 1
    assert count(engine, ListingObservation) == 1


def test_provider_listing_id_cannot_move_to_another_vin(engine, spec, row):
    from app.importing.service import run_import

    provider = replace(row, source_listing_id="provider-1")
    run_import(engine, spec, [[provider]])
    conflict = replace(provider, vin="3GTP1NEJ7HG400459")
    result = run_import(engine, replace(spec, source_file_hash="d" * 64), [[conflict]])
    assert result["rejected_rows"] == 1
    assert count(engine, Vehicle) == 1
    assert count(engine, ListingObservation) == 1


def test_enrichment_preserves_existing_attributes_and_unknown_codes(engine, spec, row):
    from app.importing.service import run_import

    run_import(engine, spec, [[row]])
    run_import(
        engine,
        replace(spec, source_file_hash="d" * 64),
        [
            [
                replace(
                    row,
                    drivetrain_code="FWD",
                    exterior_color_code="WHITE",
                    fuel_type_code="CUSTOM_UNKNOWN",
                )
            ]
        ],
    )
    run_import(
        engine,
        replace(spec, source_file_hash="e" * 64),
        [
            [
                replace(
                    row,
                    drivetrain_code="RWD",
                    exterior_color_code=None,
                )
            ]
        ],
    )
    with engine.connect() as connection:
        vehicle = connection.execute(select(Vehicle.__table__)).mappings().one()
    assert vehicle["drivetrain_code"] == "FWD"
    assert vehicle["exterior_color_code"] == "WHITE"
    assert vehicle["fuel_type_code"] is None


def test_importer_lock_blocks_competing_writer(engine, spec, row):
    from app.importing.service import run_import

    with engine.connect() as lock:
        assert lock.scalar(select(func.get_lock("valueapi_importer", 0))) == 1
        try:
            with pytest.raises(RuntimeError, match="another import"):
                run_import(engine, spec, [[row]])
        finally:
            lock.execute(select(func.release_lock("valueapi_importer")))
    assert count(engine, ImportBatch) == 0


def test_resume_refuses_changed_observation_metadata(engine, spec, row):
    from app.importing import service

    def interrupted():
        yield [row]
        raise RuntimeError("interrupted")

    expanded = replace(spec, total_rows=2)
    with pytest.raises(RuntimeError):
        service.run_import(engine, expanded, interrupted())
    with engine.connect() as connection:
        batch_id = connection.scalar(select(ImportBatch.id))
    with pytest.raises(ValueError, match="metadata differs"):
        service.run_import(
            engine, replace(expanded, currency_code="USD"), [], resume_batch_id=batch_id
        )
    assert count(engine, ListingObservation) == 1


def test_report_failure_rolls_back_chunk_and_resumes(engine, spec, row, monkeypatch):
    from app.importing import service
    from app.models import ImportChunk

    original = service._write_chunk_report

    def fail_report(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(service, "_write_chunk_report", fail_report)
    with pytest.raises(OSError):
        service.run_import(engine, spec, [[row]])
    assert count(engine, Vehicle) == 0
    assert count(engine, ListingObservation) == 0
    assert count(engine, ImportChunk) == 0
    with engine.connect() as connection:
        batch_id = connection.scalar(select(ImportBatch.id))
    monkeypatch.setattr(service, "_write_chunk_report", original)
    service.run_import(engine, spec, [[row]], resume_batch_id=batch_id)
    assert count(engine, ListingObservation) == 1
    assert count(engine, ImportChunk) == 1


def test_transient_retry_keeps_only_committed_report(engine, spec, row, monkeypatch):
    import pymysql
    from app.importing import service
    from sqlalchemy.exc import OperationalError

    original = service._write_chunk_report
    calls = 0

    def lose_connection(*args, **kwargs):
        nonlocal calls
        report = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            raise OperationalError(
                None, None, pymysql.err.OperationalError(1213, "deadlock")
            )
        return report

    monkeypatch.setattr(service, "_write_chunk_report", lose_connection)
    result = service.run_import(engine, spec, [[row]])
    assert calls == 2
    assert result["accepted_rows"] == 1
    assert count(engine, ListingObservation) == 1
    assert len(result["issue_report_uris"]) == 1
    assert len(list(Path(spec.manifest_uri).parent.glob("issues/*/*.json"))) == 2


def test_commit_response_loss_does_not_replay_committed_counts(engine, spec, row):
    import pymysql
    from app.importing.service import run_import
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    fired = False

    def after_commit(session):
        nonlocal fired
        if fired:
            return
        for entity in session.identity_map.values():
            if (
                isinstance(entity, ImportBatch)
                and entity.__dict__.get("last_processed_record") == 2
            ):
                fired = True
                raise OperationalError(
                    None, None, pymysql.err.OperationalError(2013, "lost response")
                )

    event.listen(Session, "after_commit", after_commit)
    try:
        result = run_import(engine, spec, [[row]])
    finally:
        event.remove(Session, "after_commit", after_commit)
    assert fired
    assert result["accepted_rows"] == 1
    assert count(engine, ListingObservation) == 1


def test_completed_visibility_excludes_failed_batch(engine, spec, row):
    from app.importing.service import completed_observations, run_import

    def interrupted():
        yield [row]
        raise RuntimeError("interrupted")

    with pytest.raises(RuntimeError):
        run_import(engine, replace(spec, total_rows=2), interrupted())
    assert count(engine, ListingObservation) == 1
    with engine.connect() as connection:
        assert connection.execute(completed_observations()).all() == []


def test_cli_import_deduplication_and_status(engine, tmp_path):
    import json
    import subprocess
    import sys

    root = Path(__file__).parents[3]
    cli = [sys.executable, str(root / "src/cli/main.py")]
    environment = {
        **os.environ,
        "DATABASE_URL": engine.url.render_as_string(hide_password=False),
    }
    args = [
        "import",
        str(root / "sample_data/inventory_sm.csv"),
        "--config",
        str(root / "src/cli/rules.sample.toml"),
        "--work-dir",
        str(tmp_path / "artifacts"),
        "--batch-size",
        "300",
    ]
    first = subprocess.run(
        [*cli, *args], env=environment, capture_output=True, text=True, check=False
    )
    assert first.returncode in (0, 1), first.stderr
    result = json.loads(first.stdout.splitlines()[-1])
    assert result["total_rows"] == 999
    assert result["accepted_rows"] == 995
    assert result["rejected_rows"] == 4
    assert result["status"] == "completed"
    import csv

    from app.models import Make, Model

    with (root / "sample_data/inventory_sm.csv").open(newline="") as source:
        records = csv.DictReader(source, delimiter="|", quoting=csv.QUOTE_NONE)
        expected_catalog = {
            (row["make"].lower(), row["model"].lower())
            for number, row in enumerate(records, 2)
            if number not in {552, 577, 655, 713}
        }

    with engine.connect() as connection:
        assert (
            set(
                connection.execute(
                    select(Make.normalized_name, Model.normalized_name).join(Model)
                ).all()
            )
            == expected_catalog
        )
    initial_observations = count(engine, ListingObservation)
    second = subprocess.run(
        [*cli, *args], env=environment, capture_output=True, text=True, check=False
    )
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout.splitlines()[-1])["no_op"] is True
    assert len(list((tmp_path / "artifacts").iterdir())) == 1
    assert count(engine, ListingObservation) == initial_observations
    status = subprocess.run(
        [*cli, "import-status", str(result["batch_id"])],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    persisted = json.loads(status.stdout.splitlines()[-1])
    assert persisted["checkpoint"] == 1000
    assert len(persisted["issue_report_uris"]) == 4

    # Restore a partial checkpoint to test loading the manifest when resuming through
    # the CLI.
    from app.models import ImportChunk

    with engine.begin() as connection:
        connection.execute(
            ListingObservation.__table__.delete().where(
                ListingObservation.source_record_number > 301
            )
        )
        connection.execute(
            ImportChunk.__table__.delete().where(ImportChunk.last_record > 301)
        )
        accepted = connection.scalar(
            select(func.count()).select_from(ListingObservation)
        )
        connection.execute(
            ImportBatch.__table__.update().values(
                status="failed",
                completed_at=None,
                last_processed_record=301,
                accepted_rows=accepted,
                rejected_rows=300 - accepted,
            )
        )
    resumed = subprocess.run(
        [*cli, "import-resume", str(result["batch_id"]), "--batch-size", "300"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert resumed.returncode in (0, 1), resumed.stderr
    assert (
        json.loads(resumed.stdout.splitlines()[-1])["accepted_rows"]
        == result["accepted_rows"]
    )
    assert count(engine, ListingObservation) == initial_observations
