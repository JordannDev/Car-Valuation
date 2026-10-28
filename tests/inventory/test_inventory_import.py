"""Check real imported inventory, reports, and valuation inputs against source rows."""

import json
import os
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from app import create_app
from app.extensions import db
from app.models import (
    Dealer,
    ImportBatch,
    Listing,
    ListingObservation,
    Make,
    Model,
    ModelYear,
    Vehicle,
)
from app.valuation.contracts import ModelASettings, ValuationQuery
from app.valuation.repository import load_comparables
from app.valuation.service import InsufficientEvidence, valuate
from conftest import ROOT, SAMPLE_CONFIG, SOURCE
from sqlalchemy import func, select
from sqlalchemy.engine import make_url


@pytest.fixture(scope="module")
def imported_inventory(inventory, tmp_path_factory):
    """Import once into an explicitly disposable database; never use the app database."""

    url = os.environ.get("VALUEAPI_IMPORT_TEST_URL")
    if not url:
        pytest.skip("Set VALUEAPI_IMPORT_TEST_URL to a disposable MySQL database")
    parsed = make_url(url)
    if (
        parsed.database != "valueapi_import_test"
        or parsed.get_backend_name() != "mysql"
    ):
        pytest.fail(
            "Inventory integration tests require MySQL database valueapi_import_test"
        )

    environment = {**os.environ, "DATABASE_URL": url}
    patch = pytest.MonkeyPatch()
    patch.setenv("DATABASE_URL", url)
    application = create_app()
    work = tmp_path_factory.mktemp("inventory-import")
    args = [
        sys.executable,
        str(ROOT / "src/cli/main.py"),
        "import",
        str(SOURCE),
        "--config",
        str(SAMPLE_CONFIG),
        "--work-dir",
        str(work),
        "--batch-size",
        "137",
        "--currency",
        "USD",
        "--mileage-unit",
        "MI",
    ]

    try:
        with application.app_context():
            migration = AlembicConfig(str(ROOT / "src/api/alembic.ini"))
            migration.set_main_option(
                "script_location", str(ROOT / "src/api/migrations")
            )
            command.upgrade(migration, "head")
            # Only this explicitly disposable database is cleared by the test suite.
            with db.engine.begin() as connection:
                for table in reversed(db.metadata.sorted_tables):
                    connection.execute(table.delete())

        result = subprocess.run(
            args, env=environment, cwd=ROOT, capture_output=True, text=True, check=False
        )
        assert result.returncode == 1, result.stdout + result.stderr
        messages = [
            json.loads(line)
            for line in result.stdout.splitlines()
            if line.startswith("{")
        ]
        status = next(message for message in messages if message["type"] == "import")
        yield application, status, args
    finally:
        with application.app_context():
            db.session.remove()
            db.engine.dispose()
        patch.undo()


def test_import_keeps_usable_inventory_and_explains_each_rejection(
    imported_inventory, inventory
):
    application, status, _ = imported_inventory
    assert status["status"] == "completed"
    assert (status["total_rows"], status["accepted_rows"], status["rejected_rows"]) == (
        999,
        995,
        4,
    )
    assert status["checkpoint"] == 1000
    manifest = json.loads(Path(status["manifest_uri"]).read_text())
    assert manifest["configuration"]["minimum_model_vins"] == 1
    assert manifest["configuration"]["minimum_make_models"] == 1
    errors = []
    for uri in status["issue_report_uris"]:
        report = json.loads(Path(unquote(urlparse(uri).path)).read_text())
        errors.extend(issue for issue in report if issue["severity"] == "error")
    assert {
        issue["source_record_number"]: set(issue["reasons"]) for issue in errors
    } == {
        552: {
            "error_required_make",
            "error_required_model",
            "make and model are required",
        },
        577: {"complete dealer name and location are required"},
        655: {"complete dealer name and location are required"},
        713: {"complete dealer name and location are required"},
    }
    for number in (577, 655, 713):
        source = inventory.source[number - 2]
        assert source["dealer_street"] == "ONLINE_ONLY" and source["dealer_zip"] == ""
    with application.app_context():
        assert db.session.scalar(select(func.count()).select_from(Vehicle)) == 995
        assert (
            db.session.scalar(select(func.count()).select_from(ListingObservation))
            == 995
        )


def test_stored_identities_prices_mileage_and_dates_match_every_accepted_source_row(
    imported_inventory, inventory
):
    application, _, _ = imported_inventory
    with application.app_context():
        rows = db.session.execute(
            select(
                ListingObservation,
                Vehicle,
                ModelYear.year,
                Model.name,
                Make.name,
                Dealer,
            )
            .join(Listing, ListingObservation.listing_id == Listing.id)
            .join(Vehicle, Listing.vehicle_id == Vehicle.id)
            .join(ModelYear, Vehicle.model_year_id == ModelYear.id)
            .join(Model, ModelYear.model_id == Model.id)
            .join(Make, Model.make_id == Make.id)
            .join(Dealer, Listing.dealer_id == Dealer.id)
        ).all()
        assert {row[0].source_record_number for row in rows} == set(range(2, 1001)) - {
            552,
            577,
            655,
            713,
        }
        for observation, vehicle, year, model, make, dealer in rows:
            source = inventory.source[observation.source_record_number - 2]
            context = observation.source_record_number, source["vin"]
            assert (vehicle.vin, year, make.lower(), model.lower()) == (
                source["vin"],
                int(source["year"]),
                source["make"].lower(),
                source["model"].lower(),
            ), context
            assert observation.listing_price == (
                Decimal(source["listing_price"]) if source["listing_price"] else None
            ), context
            assert observation.listing_mileage == (
                int(source["listing_mileage"]) if source["listing_mileage"] else None
            ), context
            assert observation.observed_at is None, context
            assert observation.first_seen_date == (
                date.fromisoformat(source["first_seen_date"])
                if source["first_seen_date"]
                else None
            ), context
            assert observation.last_seen_date == (
                date.fromisoformat(source["last_seen_date"])
                if source["last_seen_date"]
                else None
            ), context
            assert (observation.currency_code, observation.mileage_unit) == (
                "USD",
                "MI",
            ), context
            assert (
                dealer.name,
                dealer.city,
                dealer.state_or_province,
                dealer.postal_code,
            ) == tuple(
                source[field] or None
                for field in (
                    "dealer_name",
                    "dealer_city",
                    "dealer_state",
                    "dealer_zip",
                )
            ), context
            assert dealer.country_code is None, context


def test_valuation_uses_only_the_six_priced_2020_malibus_and_their_source_dates(
    imported_inventory, inventory
):
    application, _, _ = imported_inventory
    expected = {
        row["vin"]: row
        for row in inventory.source
        if row["year"] == "2020"
        and row["make"] == "Chevrolet"
        and row["model"] == "Malibu"
        and row["listing_price"]
    }
    assert len(expected) == 6
    with application.app_context():
        query = ValuationQuery(2020, "Chevrolet", "Malibu")
        comparables = load_comparables(query, date(2026, 10, 28))
        vins = dict(db.session.execute(select(Vehicle.id, Vehicle.vin)).all())
        assert {vins[row.vehicle_id] for row in comparables} == expected.keys()
        for row in comparables:
            source = expected[vins[row.vehicle_id]]
            assert row.price == float(source["listing_price"])
            assert row.mileage_km == pytest.approx(
                int(source["listing_mileage"]) * 1.609344
            )
            assert row.observed_on == date.fromisoformat(source["first_seen_date"])
        # Six asking prices cannot calibrate the ten-vehicle mileage regression.
        # An import date or invented calibration must not turn these into a quote.
        with pytest.raises(InsufficientEvidence):
            valuate(comparables, query, date(2026, 10, 28), ModelASettings())


def test_reimporting_the_actual_sample_does_not_duplicate_inventory(imported_inventory):
    application, _, args = imported_inventory
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    status = json.loads(result.stdout.strip().splitlines()[-1])
    assert status["no_op"] is True
    with application.app_context():
        assert db.session.scalar(select(func.count()).select_from(Vehicle)) == 995
        assert (
            db.session.scalar(select(func.count()).select_from(ListingObservation))
            == 995
        )
        assert db.session.scalar(select(func.count()).select_from(ImportBatch)) == 1
