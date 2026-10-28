from types import SimpleNamespace

import pytest
from app.valuation import init_valuation_settings

PROJECTION_ENV = (
    "VALUATION_PROJECTION_ENABLED",
    "VALUATION_PROJECTION_ANNUAL_DEPRECIATION_RATE",
    "VALUATION_PROJECTION_ANNUAL_MARKET_CHANGE_RATE",
    "VALUATION_PROJECTION_ANNUAL_LOG_UNCERTAINTY",
    "VALUATION_PROJECTION_SOURCE",
    "VALUATION_FALLBACK_BETA_PER_1000_KM",
    "VALUATION_MARKET_LOG_DISPERSION",
)


@pytest.fixture
def settings_app(monkeypatch):
    for name in PROJECTION_ENV:
        monkeypatch.delenv(name, raising=False)
    return SimpleNamespace(config={})


def test_projection_defaults_disabled_while_existing_fallback_config_still_loads(
    settings_app, monkeypatch
):
    monkeypatch.setenv("VALUATION_FALLBACK_BETA_PER_1000_KM", "-0.003")
    monkeypatch.setenv("VALUATION_MARKET_LOG_DISPERSION", "0.12")

    init_valuation_settings(settings_app)

    settings = settings_app.config["MODEL_A_SETTINGS"]
    assert settings.fallback_beta_per_1000_km == -0.003
    assert settings.market_log_dispersion == 0.12
    assert settings.projection.enabled is False


def test_enabled_projection_accepts_complete_configuration(settings_app, monkeypatch):
    monkeypatch.setenv("VALUATION_PROJECTION_ENABLED", "true")
    monkeypatch.setenv("VALUATION_PROJECTION_ANNUAL_DEPRECIATION_RATE", "0.12")
    monkeypatch.setenv("VALUATION_PROJECTION_ANNUAL_MARKET_CHANGE_RATE", "-0.03")
    monkeypatch.setenv("VALUATION_PROJECTION_ANNUAL_LOG_UNCERTAINTY", "0.2")
    monkeypatch.setenv("VALUATION_PROJECTION_SOURCE", "approved calibration")

    init_valuation_settings(settings_app)

    projection = settings_app.config["MODEL_A_SETTINGS"].projection
    assert projection.enabled is True
    assert projection.annual_depreciation_rate == 0.12
    assert projection.annual_market_change_rate == -0.03
    assert projection.annual_log_uncertainty == 0.2
    assert projection.source == "approved calibration"


def test_enabled_projection_may_be_unconfigured_for_historical_passthrough(
    settings_app, monkeypatch
):
    monkeypatch.setenv("VALUATION_PROJECTION_ENABLED", "true")

    init_valuation_settings(settings_app)

    projection = settings_app.config["MODEL_A_SETTINGS"].projection
    assert projection.enabled is True
    assert projection.annual_depreciation_rate is None
    assert projection.annual_market_change_rate is None
    assert projection.annual_log_uncertainty is None
    assert projection.source is None


@pytest.mark.parametrize("value", ["yes", "1", "", "enabled"])
def test_projection_enabled_requires_strict_boolean(settings_app, monkeypatch, value):
    monkeypatch.setenv("VALUATION_PROJECTION_ENABLED", value)

    with pytest.raises(ValueError, match="VALUATION_PROJECTION_ENABLED"):
        init_valuation_settings(settings_app)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("VALUATION_PROJECTION_ANNUAL_DEPRECIATION_RATE", "-0.01"),
        ("VALUATION_PROJECTION_ANNUAL_DEPRECIATION_RATE", "1"),
        ("VALUATION_PROJECTION_ANNUAL_MARKET_CHANGE_RATE", "-1"),
        ("VALUATION_PROJECTION_ANNUAL_LOG_UNCERTAINTY", "0"),
        ("VALUATION_PROJECTION_ANNUAL_DEPRECIATION_RATE", "nan"),
        ("VALUATION_PROJECTION_ANNUAL_MARKET_CHANGE_RATE", "inf"),
        ("VALUATION_PROJECTION_ANNUAL_LOG_UNCERTAINTY", "-inf"),
        ("VALUATION_PROJECTION_ANNUAL_LOG_UNCERTAINTY", "not-a-number"),
    ],
)
def test_invalid_projection_numbers_fail_startup_even_when_disabled(
    settings_app, monkeypatch, name, value
):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        init_valuation_settings(settings_app)


def test_blank_projection_source_is_unconfigured(settings_app, monkeypatch):
    monkeypatch.setenv("VALUATION_PROJECTION_SOURCE", "   ")
    init_valuation_settings(settings_app)
    assert settings_app.config["MODEL_A_SETTINGS"].projection.source is None
