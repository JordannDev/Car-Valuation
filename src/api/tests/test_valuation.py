"""Tests for pricing safeguards and how model A handles API requests and database results."""

from dataclasses import replace
from datetime import date, timedelta
from math import exp

import pytest
from app import create_app
from app.valuation.contracts import Comparable, ModelASettings, ValuationQuery
from app.valuation.service import (
    InsufficientEvidence,
    dealer_weights,
    fit_beta,
    valuate,
    weighted_quantile,
)

TODAY = date(2026, 10, 27)
QUERY = ValuationQuery(2020, "Toyota", "Camry", 100_000)
SETTINGS = ModelASettings(fallback_beta_per_1000_km=-0.003, market_log_dispersion=0.12)


def cohort(count=30):
    return [
        Comparable(
            i,
            i,
            i % 10,
            20_000 * exp(-0.003 * (i * 5000 - 100_000) / 1000),
            i * 5000,
            TODAY,
            "US",
            "Michigan",
        )
        for i in range(count)
    ]


def test_huber_recovers_depreciation_and_normalizes_without_mileage_bias():
    result = valuate(cohort(), QUERY, TODAY, SETTINGS)
    assert result["estimatedAskingPrice"] == 20_000
    beta, source = fit_beta(cohort(), [1 / 30] * 30, SETTINGS)
    assert beta == pytest.approx(-0.003, abs=1e-6)
    assert source == "huber"
    evidence = result["comparables"][0]
    assert evidence["listingPrice"] == pytest.approx(cohort()[0].price)
    assert evidence["mileageKm"] == cohort()[0].mileage_km
    assert evidence["listingPrice"] != result["estimatedAskingPrice"]
    assert evidence["observedOn"] == TODAY.isoformat()
    assert evidence["location"] == {"city": None, "region": "Michigan", "country": "US"}
    assert result["marketRange"] == {"low": 20_000, "high": 20_000}
    assert sum(r["weight"] for r in result["comparables"]) == pytest.approx(1)


def test_positive_mileage_relationship_cannot_produce_positive_beta():
    rows = [
        replace(r, price=10_000 * exp(0.002 * r.mileage_km / 1000)) for r in cohort()
    ]
    beta, _ = fit_beta(rows, [1 / len(rows)] * len(rows), SETTINGS)
    assert beta <= 0
    assert beta == pytest.approx(0, abs=1e-7)


def test_isolated_price_outlier_excluded_after_normalization():
    rows = cohort()
    rows[15] = replace(rows[15], price=200_000)
    result = valuate(rows, QUERY, TODAY, SETTINGS)
    assert result["estimatedAskingPrice"] == pytest.approx(20_000, abs=100)
    evidence = next(r for r in result["comparables"] if r["vehicleId"] == 15)
    assert evidence["weight"] == 0
    # Keep the valid low-mileage vehicle even though its asking price is higher.
    assert result["comparables"][0]["weight"] > 0


def test_median_reference_and_repeated_vehicle_do_not_multiply_evidence():
    rows = cohort()
    result = valuate(
        rows + [rows[0]] * 100, replace(QUERY, mileage_km=None), TODAY, SETTINGS
    )
    assert len(result["comparables"]) == 30
    assert result["referenceMileageKm"] == 72_500
    assert result["mileageSource"] == "comparable_median"


def test_high_mileage_rounds_down_to_largest_observed_point_and_keeps_request():
    result = valuate(cohort(), replace(QUERY, mileage_km=500_000), TODAY, SETTINGS)
    assert result["requestedMileageKm"] == 500_000
    assert result["referenceMileageKm"] == 145_000
    assert result["mileageSource"] == "rounded_down"


def test_low_mileage_is_unavailable_without_rounding_up():
    rows = [replace(row, mileage_km=row.mileage_km + 10_000) for row in cohort()]
    result = valuate(rows, replace(QUERY, mileage_km=1_000), TODAY, SETTINGS)
    assert result["referenceMileageKm"] is None
    assert result["requestedMileageKm"] == 1_000
    assert result["mileageSource"] == "unavailable"
    assert result["estimatedAskingPrice"] == 21_900
    assert {row["listingPrice"] for row in result["comparables"]} == {
        row.price for row in rows
    }


@pytest.mark.parametrize("requested", [None, 321_868.8])
def test_missing_mileage_uses_raw_price_valuation_and_preserves_nulls(requested):
    rows = [replace(row, mileage_km=None) for row in cohort()]
    result = valuate(rows, replace(QUERY, mileage_km=requested), TODAY)
    assert result["referenceMileageKm"] is None
    assert result["mileageSource"] == "unavailable"
    assert result["requestedMileageKm"] == requested
    assert all(row["mileageKm"] is None for row in result["comparables"])
    assert result["estimatedAskingPrice"] == 21_900


def test_null_mileage_rows_excluded_when_usable_mileage_exists():
    rows = cohort() + [
        replace(cohort()[0], observation_id=100, vehicle_id=100, mileage_km=None)
    ]
    result = valuate(rows, QUERY, TODAY, SETTINGS)
    assert len(result["comparables"]) == 30


def test_dealer_weight_cap_survives_many_listings_from_one_dealer():
    rows = [
        replace(r, dealer_id=1 if r.vehicle_id < 25 else r.vehicle_id) for r in cohort()
    ]
    weights = dealer_weights(rows, [1] * len(rows), 0.2)
    assert sum(w for r, w in zip(rows, weights) if r.dealer_id == 1) <= 0.2 + 1e-9
    assert sum(weights) == pytest.approx(1)
    with pytest.raises(InsufficientEvidence, match="dealers"):
        valuate([replace(r, dealer_id=1) for r in rows], QUERY, TODAY, SETTINGS)


def test_sparse_fit_and_zero_mad_require_configured_calibration():
    rows = [replace(r, mileage_km=100_000, price=20_000) for r in cohort(6)]
    with pytest.raises(InsufficientEvidence, match="fallback"):
        valuate(rows, QUERY, TODAY)
    with pytest.raises(InsufficientEvidence, match="dispersion"):
        valuate(rows, QUERY, TODAY, ModelASettings(fallback_beta_per_1000_km=-0.003))
    assert valuate(rows, QUERY, TODAY, SETTINGS)["estimatedAskingPrice"] == 20_000
    assert fit_beta(rows, [1 / len(rows)] * len(rows), SETTINGS) == (
        -0.003,
        "calibrated_fallback",
    )


@pytest.mark.parametrize("divisor", [2, 5])
def test_second_price_population_is_not_silently_removed(divisor):
    rows = [
        replace(r, price=r.price * (3 if r.vehicle_id % divisor == 0 else 1))
        for r in cohort()
    ]
    with pytest.raises(InsufficientEvidence, match="populations"):
        valuate(rows, QUERY, TODAY, SETTINGS)


def test_search_expands_region_and_age_and_ignores_future_evidence():
    rows = [replace(r, observed_on=TODAY - timedelta(days=120)) for r in cohort()]
    rows += [replace(rows[0], vehicle_id=100, observed_on=TODAY + timedelta(days=1))]
    rows += [replace(rows[0], vehicle_id=101, observed_on=TODAY - timedelta(days=900))]
    result = valuate(rows, replace(QUERY, country="US", region="Ohio"), TODAY, SETTINGS)
    assert result["locationSelection"]["scope"] == "country"
    assert {r["observedOn"] for r in result["comparables"]} == {
        (TODAY - timedelta(days=120)).isoformat()
    }
    assert len(result["comparables"]) == 30
    assert result["confidence"]["level"] == "low"


def test_too_few_comparables_are_explicit_failures():
    with pytest.raises(InsufficientEvidence, match="five"):
        valuate(cohort(4), QUERY, TODAY, SETTINGS)
    assert weighted_quantile([1, 2, 1000], [1, 1, 0.01], 0.9) == 2


@pytest.mark.parametrize(
    ("values", "weights", "quantile", "expected"),
    [
        ([1, 2, 3], [1, 1, 1], 0, 1),
        ([1, 2, 3], [1, 1, 1], 1, 3),
        ([1, 2, 3], [1, 1, 1], 0.5, 2),
        ([1, 2, 2, 3], [1, 1, 1, 1], 0.75, 2),
        ([1, 2, 3], [0, 1, 0], 0.5, 2),
    ],
)
def test_weighted_quantile_boundaries_ties_and_zero_weights(
    values, weights, quantile, expected
):
    assert weighted_quantile(values, weights, quantile) == expected


def test_weighted_quantile_supports_dates_for_weighted_reference_date():
    dates = [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)]

    assert weighted_quantile(dates, [1, 1, 1], 0.5) == dates[1]


def test_location_prefers_supported_region_and_changes_the_price():
    regional = [
        replace(row, region=" MI ", observed_on=TODAY - timedelta(days=120))
        for row in cohort()
    ]
    broader = [
        replace(
            row,
            observation_id=row.observation_id + 100,
            vehicle_id=row.vehicle_id + 100,
            region="NY",
            price=row.price * 1.2,
        )
        for row in cohort()
    ]
    result = valuate(
        regional + broader, replace(QUERY, country="US", region="mi"), TODAY, SETTINGS
    )
    assert result["estimatedAskingPrice"] == 20_000
    assert {r["observedOn"] for r in result["comparables"]} == {
        (TODAY - timedelta(days=120)).isoformat()
    }
    assert result["locationSelection"] == {
        "country": "US",
        "region": "mi",
        "scope": "region",
        "fallback": False,
    }
    assert {row["location"]["region"] for row in result["comparables"]} == {" MI "}
    other_region = valuate(
        regional + broader, replace(QUERY, country="US", region="NY"), TODAY, SETTINGS
    )
    assert other_region["estimatedAskingPrice"] == 24_000


def test_location_fallback_is_explicit_and_low_confidence():
    result = valuate(
        cohort(), replace(QUERY, country="US", region="FL"), TODAY, SETTINGS
    )
    assert result["locationSelection"] == {
        "country": "US",
        "region": "FL",
        "scope": "country",
        "fallback": True,
    }
    assert result["confidence"]["level"] == "low"


def test_region_without_country_uses_recorded_region_without_inventing_country():
    rows = [replace(row, country=None, region="FL") for row in cohort()]
    result = valuate(rows, replace(QUERY, region="FL"), TODAY, SETTINGS)
    assert result["locationSelection"] == {
        "country": None,
        "region": "FL",
        "scope": "region",
        "fallback": False,
    }
    assert (
        valuate(rows, replace(QUERY, region="NY"), TODAY, SETTINGS)[
            "locationSelection"
        ]["scope"]
        == "all"
    )


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.delenv("VALUATION_FALLBACK_BETA_PER_1000_KM", raising=False)
    monkeypatch.delenv("VALUATION_MARKET_LOG_DISPERSION", raising=False)
    for name in (
        "VALUATION_PROJECTION_ENABLED",
        "VALUATION_PROJECTION_ANNUAL_DEPRECIATION_RATE",
        "VALUATION_PROJECTION_ANNUAL_MARKET_CHANGE_RATE",
        "VALUATION_PROJECTION_ANNUAL_LOG_UNCERTAINTY",
        "VALUATION_PROJECTION_SOURCE",
    ):
        monkeypatch.delenv(name, raising=False)
    application = create_app()
    application.config.update(TESTING=True, MODEL_A_SETTINGS=SETTINGS)
    return application


@pytest.mark.parametrize(
    "suffix",
    [
        "",
        "&year=2020",
        "&mileage=nan&mileageUnit=KM",
        "&mileage=-1&mileageUnit=MI",
        "&mileage=5",
        "&mileageUnit=KM",
        "&region=",
        "&currency=US",
        "&x=1",
    ],
)
def test_invalid_requests_do_not_query_database(app, monkeypatch, suffix):
    def unexpected(*_args):
        pytest.fail("Invalid request reached repository")

    monkeypatch.setattr("app.valuation.load_comparables", unexpected)
    base = "" if not suffix else "?year=2020&make=Toyota&model=Camry"
    assert (
        app.test_client().get("/api/valuations/model-a" + base + suffix).status_code
        == 400
    )


def test_http_miles_conversion_and_current_as_of(app, monkeypatch):
    captured = []

    def load(query, as_of):
        captured.append((query, as_of))
        return [replace(r, observed_on=as_of) for r in cohort()]

    monkeypatch.setattr("app.valuation.load_comparables", load)
    listing_summary = {
        "totalListings": 42,
        "latestListingDate": TODAY.isoformat(),
        "lowestRecordedPrice": 10_000,
        "highestRecordedPrice": 30_000,
    }
    monkeypatch.setattr(
        "app.valuation.load_listing_summary", lambda *_: listing_summary
    )
    response = app.test_client().get(
        "/api/valuations/model-a?year=2020&make=Toyota&model=Camry&mileage=62137.119&mileageUnit=MI"
    )
    assert response.status_code == 200
    assert response.json["estimatedAskingPrice"] == 20_000
    assert response.json["listingSummary"] == listing_summary
    assert captured[0][0].mileage_km == pytest.approx(100_000, abs=0.01)
    assert {row["observedOn"] for row in response.json["comparables"]} == {
        captured[0][1].isoformat()
    }


def test_evidence_failure_and_database_error_are_distinct(app, monkeypatch):
    from sqlalchemy.exc import OperationalError

    monkeypatch.setattr("app.valuation.load_comparables", lambda *_: [])
    url = "/api/valuations/model-a?year=2020&make=Toyota&model=Camry"
    assert app.test_client().get(url).status_code == 422

    def fail(*_):
        raise OperationalError("private SQL", {}, Exception("private credentials"))

    monkeypatch.setattr("app.valuation.load_comparables", fail)
    response = app.test_client().get(url)
    assert response.status_code == 503
    assert response.json == {"error": "Valuation temporarily unavailable"}


def test_repository_exact_match_latest_completed_snapshot_and_unit_safety(app):
    from datetime import datetime

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
    from app.valuation.repository import load_comparables
    from sqlalchemy import MetaData

    with app.app_context():
        # Test the query without changing the MySQL table definitions used by the app.
        metadata = MetaData()
        for table in db.metadata.sorted_tables:
            copied = table.to_metadata(metadata)
            for name in ("created_at", "updated_at"):
                if name in copied.c:
                    copied.c[name].server_default = None
                    copied.c[name].nullable = True
        metadata.create_all(db.engine)

        def insert(model, **values):
            db.session.execute(model.__table__.insert().values(**values))

        insert(Make, id=1, name="Toyota", normalized_name="toyota")
        insert(Model, id=1, make_id=1, name="Camry", normalized_name="camry")
        insert(Model, id=2, make_id=1, name="Corolla", normalized_name="corolla")
        insert(ModelYear, id=1, model_id=1, year=2020)
        insert(ModelYear, id=2, model_id=2, year=2020)
        insert(ModelYear, id=3, model_id=1, year=2019)
        insert(
            Dealer,
            id=1,
            name="Dealer",
            identity_key="a" * 64,
            country_code="US",
            state_or_province="Michigan",
            city="Detroit",
        )
        for batch, status in ((1, "completed"), (2, "running")):
            insert(
                ImportBatch,
                id=batch,
                source_name="test",
                source_file_name="test.csv",
                source_file_hash=str(batch) * 64,
                source_file_uri="source",
                normalization_version="1",
                rules_hash="b" * 64,
                manifest_uri="manifest",
                status=status,
            )
        for vehicle_id in range(1, 10):
            insert(
                Vehicle,
                id=vehicle_id,
                vin=f"TEST{vehicle_id:013}",
                model_year_id=2 if vehicle_id == 8 else 3 if vehicle_id == 9 else 1,
            )
            insert(
                Listing,
                id=vehicle_id,
                vehicle_id=vehicle_id,
                dealer_id=1,
                source_name="test",
            )

        def observation(obs_id, vehicle_id, **overrides):
            values = dict(
                id=obs_id,
                listing_id=vehicle_id,
                import_batch_id=1,
                source_record_number=obs_id + 2,
                observed_at=datetime(2026, 10, 20),
                listing_price=20_000,
                currency_code="USD",
                listing_mileage=50_000,
                mileage_unit="MI",
            )
            values.update(overrides)
            insert(ListingObservation, **values)

        for vehicle_id in range(1, 10):
            observation(
                vehicle_id,
                vehicle_id,
                currency_code="CAD" if vehicle_id == 4 else "USD",
                mileage_unit=None if vehicle_id == 5 else "MI",
                observed_at=None
                if vehicle_id == 6
                else datetime(2024, 1, 1)
                if vehicle_id == 7
                else datetime(2026, 10, 20),
            )
        observation(11, 1, observed_at=datetime(2026, 10, 21), listing_price=21_000)
        observation(
            12,
            1,
            import_batch_id=2,
            observed_at=datetime(2026, 10, 22),
            listing_price=99_000,
        )
        observation(13, 2, observed_at=datetime(2026, 10, 21), listing_price=None)
        observation(14, 3, observed_at=datetime(2026, 10, 28), listing_price=99_000)
        # Count the vehicle once, even if it has another listing.
        insert(Listing, id=10, vehicle_id=1, dealer_id=1, source_name="other")
        observation(
            15,
            1,
            listing_id=10,
            observed_at=datetime(2026, 10, 22),
            listing_price=22_000,
            currency_code=None,
            mileage_unit=None,
        )
        observation(
            16,
            3,
            observed_at=datetime(2026, 10, 20),
            listing_price=22_000,
            currency_code="USD",
            mileage_unit="MI",
        )
        # Keep the newest snapshot even when its mileage is unavailable.
        observation(
            17,
            5,
            observed_at=datetime(2026, 10, 21),
            listing_mileage=None,
        )
        db.session.commit()

        locations_response = app.test_client().get("/api/valuations/locations")
        assert locations_response.status_code == 200
        assert locations_response.json == {
            "locations": [{"country": "US", "region": "Michigan"}]
        }

        from app.valuation.listing_summary import load_listing_summary

        # Count separate listings, including old ones, rather than vehicles or
        # snapshots. Unfinished imports, future dates, and other years, makes, models,
        # or currencies must not affect these totals or price extremes.
        assert load_listing_summary(QUERY, TODAY) == {
            "totalListings": 6,
            "latestListingDate": "2026-10-22",
            "lowestRecordedPrice": 20_000,
            "highestRecordedPrice": 22_000,
        }
        assert load_listing_summary(replace(QUERY, country="CA"), TODAY) == {
            "totalListings": 0,
            "latestListingDate": None,
            "lowestRecordedPrice": None,
            "highestRecordedPrice": None,
        }

        rows = load_comparables(replace(QUERY, make=" TOYOTA ", model="camry"), TODAY)
        assert {row.vehicle_id for row in rows} == {1, 3, 5, 7}
        assert 2 not in {row.vehicle_id for row in rows}  # Latest price is null.
        latest_without_mileage = next(row for row in rows if row.vehicle_id == 5)
        assert latest_without_mileage.observation_id == 17
        assert latest_without_mileage.mileage_km is None
        eligible_vehicle = next(row for row in rows if row.vehicle_id == 3)
        # Ignore the future snapshot, then choose the greater ID for the date tie.
        assert eligible_vehicle.observation_id == 16
        assert next(row for row in rows if row.vehicle_id == 7).observed_on == date(
            2024, 1, 1
        )
        selected = next(row for row in rows if row.vehicle_id == 1)
        assert selected.observation_id == 15
        assert selected.price == 22_000
        assert selected.city == "Detroit"
        assert selected.region == "Michigan"
        assert selected.mileage_km == pytest.approx(80_467.2)
        assert next(row for row in rows if row.vehicle_id == 3).price == 22_000

        # Mileage already marked as kilometres should not be converted again.
        from sqlalchemy import update

        db.session.execute(
            update(ListingObservation)
            .where(ListingObservation.id == 16)
            .values(mileage_unit="KM")
        )
        explicit_km = next(
            row for row in load_comparables(QUERY, TODAY) if row.vehicle_id == 3
        )
        assert explicit_km.mileage_km == 50_000
        db.session.execute(
            update(ListingObservation)
            .where(ListingObservation.id == 17)
            .values(listing_mileage=None)
        )
        missing_mileage = next(
            row for row in load_comparables(QUERY, TODAY) if row.vehicle_id == 5
        )
        assert missing_mileage.mileage_km is None
        db.session.execute(
            update(ListingObservation)
            .where(ListingObservation.id == 17)
            .values(listing_mileage=50_000)
        )
        assert load_comparables(replace(QUERY, country="CA"), TODAY) == []
        assert {
            row.vehicle_id
            for row in load_comparables(replace(QUERY, currency="CAD"), TODAY)
        } == {4}

        # Use the explicit observation time even if another source date is newer.
        db.session.execute(
            update(ListingObservation)
            .where(ListingObservation.id == 11)
            .values(last_seen_date=TODAY, first_seen_date=TODAY)
        )
        assert (
            next(
                row for row in load_comparables(QUERY, TODAY) if row.vehicle_id == 1
            ).observation_id
            == 15
        )

        # Use first-seen before last-seen when choosing the latest snapshot and checking
        # its age.
        db.session.execute(
            update(ListingObservation)
            .where(ListingObservation.id == 15)
            .values(
                observed_at=None,
                last_seen_date=date(2026, 10, 23),
                first_seen_date=TODAY,
            )
        )
        fallback = next(
            row for row in load_comparables(QUERY, TODAY) if row.vehicle_id == 1
        )
        assert fallback.observed_on == TODAY
        assert fallback.observation_id == 15

        db.session.execute(
            update(ListingObservation)
            .where(ListingObservation.id == 6)
            .values(
                last_seen_date=date(2026, 10, 25), first_seen_date=date(2026, 10, 24)
            )
        )
        fallback = next(
            row for row in load_comparables(QUERY, TODAY) if row.vehicle_id == 6
        )
        assert fallback.observed_on == date(2026, 10, 24)

        db.session.execute(
            update(ListingObservation)
            .where(ListingObservation.id == 6)
            .values(first_seen_date=None)
        )
        fallback = next(
            row for row in load_comparables(QUERY, TODAY) if row.vehicle_id == 6
        )
        assert fallback.observed_on == date(2026, 10, 25)

        # Reject a future observation date, even if another source date would be usable.
        db.session.execute(
            update(ListingObservation)
            .where(ListingObservation.id == 6)
            .values(observed_at=datetime(2026, 10, 28), last_seen_date=TODAY)
        )
        assert 6 not in {row.vehicle_id for row in load_comparables(QUERY, TODAY)}

        # A future first-seen date must not fall back to an older last-seen date.
        db.session.execute(
            update(ListingObservation)
            .where(ListingObservation.id == 6)
            .values(
                observed_at=None,
                first_seen_date=date(2026, 10, 28),
                last_seen_date=TODAY,
            )
        )
        assert 6 not in {row.vehicle_id for row in load_comparables(QUERY, TODAY)}

        # A recent import does not make an undated listing usable.
        db.session.execute(
            update(ListingObservation).values(
                observed_at=None,
                last_seen_date=None,
                first_seen_date=None,
                created_at=datetime(2026, 10, 27),
                currency_code=None,
                mileage_unit=None,
            )
        )
        db.session.execute(
            update(ImportBatch).values(completed_at=datetime(2026, 10, 27))
        )
        with pytest.raises(InsufficientEvidence) as failure:
            load_comparables(QUERY, TODAY)
        assert failure.value.code == "source_metadata_missing"

        # Older source dates can still support a historical estimate.
        db.session.execute(
            update(ListingObservation).values(
                observed_at=None,
                last_seen_date=date(2022, 8, 18),
                first_seen_date=date(2022, 8, 1),
                currency_code="USD",
                mileage_unit="MI",
            )
        )
        old_rows = load_comparables(QUERY, TODAY)
        assert old_rows
        assert all(row.observed_on == date(2022, 8, 1) for row in old_rows)


def test_source_failure_code_reaches_http_client(app, monkeypatch):
    def missing_metadata(*_):
        raise InsufficientEvidence(
            "Source snapshot date is unconfirmed", "source_metadata_missing"
        )

    monkeypatch.setattr("app.valuation.load_comparables", missing_metadata)
    response = app.test_client().get(
        "/api/valuations/model-a?year=2020&make=Toyota&model=Camry"
    )
    assert response.status_code == 422
    assert response.json["reasonCode"] == "source_metadata_missing"
    assert response.json["reason"] == "Source snapshot date is unconfirmed"
