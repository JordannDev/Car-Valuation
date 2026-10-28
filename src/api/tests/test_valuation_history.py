from dataclasses import replace
from datetime import date, timedelta
from math import exp

import pytest
from app.valuation.contracts import (
    Comparable,
    ModelASettings,
    ProjectionSettings,
    ValuationQuery,
)
from app.valuation.service import valuate

TODAY = date(2026, 10, 27)
QUERY = ValuationQuery(2020, "Toyota", "Camry", 100_000)
BASE_SETTINGS = ModelASettings(
    fallback_beta_per_1000_km=-0.003, market_log_dispersion=0.12
)


def cohort(on: date, price: float = 20_000, count: int = 30) -> list[Comparable]:
    return [
        Comparable(
            observation_id=i,
            vehicle_id=i,
            dealer_id=i % 10,
            price=price * exp(-0.003 * (i * 5000 - 100_000) / 1000),
            mileage_km=i * 5000,
            observed_on=on,
            country="US",
            region="Michigan",
        )
        for i in range(count)
    ]


def test_recent_cohort_is_preferred_to_older_higher_price_listings():
    recent_date = TODAY.replace(day=1)
    old_rows = [
        replace(
            row,
            observation_id=row.observation_id + 100,
            vehicle_id=row.vehicle_id + 100,
        )
        for row in cohort(date(2022, 6, 1), price=30_000)
    ]
    rows = cohort(recent_date) + old_rows

    result = valuate(rows, QUERY, TODAY, BASE_SETTINGS)

    assert result["valuationBasis"] == "current"
    assert result["estimatedAskingPrice"] == 20_000
    assert {row["observedOn"] for row in result["comparables"]} == {
        recent_date.isoformat()
    }
    assert (
        result["historicalReference"]["estimatedAskingPrice"]
        == result["estimatedAskingPrice"]
    )


def test_uses_2022_listing_cohort_as_historical_reference_when_recent_is_insufficient():
    source_date = date(2022, 6, 1)

    result = valuate(cohort(source_date), QUERY, TODAY, BASE_SETTINGS)

    assert result["valuationBasis"] == "historical"
    assert {row["observedOn"] for row in result["comparables"]} == {
        source_date.isoformat()
    }
    assert result["confidence"]["level"] == "low"


def test_disabled_projection_preserves_historical_price_and_range_exactly():
    source_date = date(2022, 6, 1)
    disabled = valuate(cohort(source_date), QUERY, TODAY, BASE_SETTINGS)
    unconfigured_settings = replace(
        BASE_SETTINGS,
        projection=ProjectionSettings(enabled=True),
    )

    unconfigured = valuate(cohort(source_date), QUERY, TODAY, unconfigured_settings)

    assert unconfigured["valuationBasis"] == "historical"
    assert unconfigured["estimatedAskingPrice"] == disabled["estimatedAskingPrice"]
    assert unconfigured["marketRange"] == disabled["marketRange"]
    assert unconfigured["historicalReference"] == disabled["historicalReference"]


def test_projection_applies_configured_factors_and_widens_historical_range():
    source_date = date(2022, 6, 1)
    rows = cohort(source_date)
    historical = valuate(rows, QUERY, TODAY, BASE_SETTINGS)
    projection = ProjectionSettings(
        enabled=True,
        annual_depreciation_rate=0.1,
        annual_market_change_rate=0.02,
        annual_log_uncertainty=0.1,
        source="approved calibration",
    )
    projected = valuate(
        rows, QUERY, TODAY, replace(BASE_SETTINGS, projection=projection)
    )

    gap_years = (TODAY - source_date).days / 365.25
    expected_factor = 0.9**gap_years * 1.02**gap_years
    uncertainty = exp(0.1 * gap_years)
    assert projected["estimatedAskingPrice"] == pytest.approx(
        historical["estimatedAskingPrice"] * expected_factor, abs=50
    )
    assert projected["marketRange"]["low"] == pytest.approx(
        historical["marketRange"]["low"] * expected_factor / uncertainty, abs=50
    )
    assert projected["marketRange"]["high"] == pytest.approx(
        historical["marketRange"]["high"] * expected_factor * uncertainty, abs=50
    )
    assert (
        projected["historicalReference"]["estimatedAskingPrice"]
        == historical["estimatedAskingPrice"]
    )
    assert projected["valuationBasis"] == "projected"
    assert projected["estimatedAskingPrice"] != historical["estimatedAskingPrice"]
    assert (
        projected["marketRange"]["low"]
        < historical["marketRange"]["low"] * expected_factor
    )
    assert (
        projected["marketRange"]["high"]
        > historical["marketRange"]["high"] * expected_factor
    )
    assert projected["confidence"]["level"] == "low"


def test_historical_gap_keeps_unprojected_prices_and_confidence_low():
    rows = cohort(date(2022, 6, 1))
    near = valuate(rows, QUERY, date(2024, 6, 1), BASE_SETTINGS)
    far = valuate(rows, QUERY, TODAY, BASE_SETTINGS)

    assert far["estimatedAskingPrice"] == near["estimatedAskingPrice"]
    assert far["marketRange"] == near["marketRange"]
    assert far["confidence"]["level"] == near["confidence"]["level"] == "low"


def test_latest_isolated_listing_does_not_block_older_viable_cohort():
    old_date = date(2022, 6, 1)
    isolated = replace(
        cohort(old_date)[0],
        observation_id=100,
        vehicle_id=100,
        dealer_id=100,
        observed_on=TODAY - timedelta(days=10),
    )

    result = valuate(cohort(old_date) + [isolated], QUERY, TODAY, BASE_SETTINGS)

    assert result["valuationBasis"] == "historical"
    assert {row["observedOn"] for row in result["comparables"]} == {
        old_date.isoformat()
    }


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [("price", 0), ("mileage_km", -1)],
)
def test_invalid_latest_vehicle_observation_does_not_revive_older_snapshot(
    field, invalid_value
):
    latest_date = TODAY - timedelta(days=5)
    older_date = TODAY - timedelta(days=20)
    vehicle = cohort(latest_date)[0]
    old_snapshot = replace(
        vehicle,
        observation_id=1000,
        observed_on=older_date,
    )
    invalid_latest = replace(
        vehicle,
        observation_id=1001,
        observed_on=TODAY - timedelta(days=1),
        **{field: invalid_value},
    )

    result = valuate(
        cohort(latest_date) + [old_snapshot, invalid_latest],
        QUERY,
        TODAY,
        BASE_SETTINGS,
    )

    assert result["valuationBasis"] == "current"
    assert len(result["comparables"]) == 29
    assert all(row["vehicleId"] != vehicle.vehicle_id for row in result["comparables"])


def test_future_vehicle_observation_does_not_suppress_latest_eligible_snapshot():
    eligible_date = TODAY - timedelta(days=5)
    vehicle = cohort(eligible_date)[0]
    future_snapshot = replace(
        vehicle,
        observation_id=1000,
        observed_on=TODAY + timedelta(days=1),
    )

    result = valuate(
        cohort(eligible_date) + [future_snapshot], QUERY, TODAY, BASE_SETTINGS
    )

    selected = next(
        row for row in result["comparables"] if row["vehicleId"] == vehicle.vehicle_id
    )
    assert selected["observationId"] == vehicle.observation_id
    assert selected["observedOn"] == eligible_date.isoformat()


def test_equal_date_vehicle_observations_choose_greater_observation_id():
    rows = cohort(TODAY)
    selected_snapshot = replace(rows[0], observation_id=1000)

    result = valuate(rows + [selected_snapshot], QUERY, TODAY, BASE_SETTINGS)

    vehicle_rows = [
        row for row in result["comparables"] if row["vehicleId"] == rows[0].vehicle_id
    ]
    assert len(vehicle_rows) == 1
    assert vehicle_rows[0]["observationId"] == selected_snapshot.observation_id


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("annual_depreciation_rate", 1),
        ("annual_market_change_rate", -1),
        ("annual_log_uncertainty", 0),
    ],
)
def test_projection_settings_reject_invalid_rates(field, value):
    with pytest.raises(ValueError):
        ProjectionSettings(**{field: value})


def test_long_historical_search_is_bounded_and_spans_the_source_history(monkeypatch):
    from app.valuation import service

    rows = [
        replace(row, observed_on=date(2022, 6, 1) - timedelta(days=index))
        for index, row in enumerate(cohort(date(2022, 6, 1), count=500))
    ]
    attempts = []

    def cannot_value(_rows, _query, reference_date, _settings):
        attempts.append(reference_date)
        raise service.InsufficientEvidence("Test cohort cannot support regression")

    monkeypatch.setattr(service, "evaluate", cannot_value)
    with pytest.raises(service.InsufficientEvidence):
        valuate(rows, QUERY, TODAY, BASE_SETTINGS)

    historical_attempts = [day for day in attempts if day != TODAY]
    assert len(historical_attempts) <= service.MAX_HISTORICAL_REFERENCE_DATES * 2
    assert historical_attempts[0] == max(row.observed_on for row in rows)
    assert historical_attempts[-1] == min(row.observed_on for row in rows)


def test_disabling_projection_preserves_old_prices_even_when_rates_remain_configured():
    rows = cohort(date(2022, 6, 1))
    base = valuate(rows, QUERY, TODAY, BASE_SETTINGS)
    configured_but_disabled = ProjectionSettings(
        enabled=False,
        annual_depreciation_rate=0.15,
        annual_market_change_rate=-0.03,
        annual_log_uncertainty=0.2,
        source="test calibration",
    )
    disabled = valuate(
        rows, QUERY, TODAY, replace(BASE_SETTINGS, projection=configured_but_disabled)
    )
    assert disabled["estimatedAskingPrice"] == base["estimatedAskingPrice"]
    assert disabled["marketRange"] == base["marketRange"]
    assert disabled["historicalReference"] == base["historicalReference"]
    assert disabled["valuationBasis"] == "historical"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("annual_depreciation_rate", None),
        ("annual_market_change_rate", None),
        ("annual_log_uncertainty", None),
        ("source", None),
        ("source", "   "),
        ("annual_depreciation_rate", 0.9999999999999999),
        ("annual_market_change_rate", 1e308),
        ("annual_log_uncertainty", 1e308),
    ],
)
def test_incomplete_or_unsafe_projection_preserves_historical_prices(field, value):
    rows = cohort(date(2000, 6, 1))
    historical = valuate(rows, QUERY, TODAY, BASE_SETTINGS)
    configured = ProjectionSettings(
        enabled=True,
        annual_depreciation_rate=0.1,
        annual_market_change_rate=0.02,
        annual_log_uncertainty=0.1,
        source="test calibration",
    )

    result = valuate(
        rows,
        QUERY,
        TODAY,
        replace(BASE_SETTINGS, projection=replace(configured, **{field: value})),
    )

    assert result["valuationBasis"] == "historical"
    assert result["estimatedAskingPrice"] == historical["estimatedAskingPrice"]
    assert result["marketRange"] == historical["marketRange"]
    assert result["historicalReference"] == historical["historicalReference"]
