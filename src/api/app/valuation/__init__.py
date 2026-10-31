"""API endpoints and settings for model A asking-price estimates."""

import os
import re
from datetime import UTC, datetime
from math import isfinite

from flask import Blueprint, current_app, request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models import Dealer

from .contracts import (
    ModelASettings,
    ProjectionSettings,
    ValuationApiResponse,
    ValuationQuery,
)
from .listing_summary import load_listing_summary
from .repository import load_comparables
from .service import InsufficientEvidence, valuate

valuation_blueprint = Blueprint("valuation", __name__)


def init_valuation_settings(app) -> None:
    """Load optional pricing settings and fail startup if a configured value is invalid."""

    # These settings are optional, but any supplied values must be checked before
    # pricing uses them.
    values: dict[str, float | None] = {}

    for name in (
        "VALUATION_FALLBACK_BETA_PER_1000_KM",
        "VALUATION_MARKET_LOG_DISPERSION",
    ):
        raw = os.environ.get(name)
        value = float(raw) if raw is not None else None
        if value is not None and (
            not isfinite(value) or (value > 0 if name.endswith("KM") else value <= 0)
        ):
            raise ValueError(f"Invalid {name}")

        values[name] = value

    # Historical price adjustments have their own settings and must be explicitly
    # enabled.
    projection_enabled = _environment_bool(
        "VALUATION_PROJECTION_ENABLED", default=False
    )
    annual_depreciation_rate = _environment_float(
        "VALUATION_PROJECTION_ANNUAL_DEPRECIATION_RATE",
        minimum=0,
        maximum=1,
        maximum_inclusive=False,
    )
    annual_market_change_rate = _environment_float(
        "VALUATION_PROJECTION_ANNUAL_MARKET_CHANGE_RATE",
        minimum=-1,
        minimum_inclusive=False,
    )
    annual_log_uncertainty = _environment_float(
        "VALUATION_PROJECTION_ANNUAL_LOG_UNCERTAINTY",
        minimum=0,
        minimum_inclusive=False,
    )
    projection_source = (
        os.environ.get("VALUATION_PROJECTION_SOURCE", "").strip() or None
    )
    # Load the checked settings once so every request uses the same values.
    app.config["MODEL_A_SETTINGS"] = ModelASettings(
        huber_enabled=_environment_bool("VALUATION_HUBER_ENABLED", default=True),
        outlier_filter_enabled=_environment_bool(
            "VALUATION_OUTLIER_FILTER_ENABLED", default=True
        ),
        fallback_beta_per_1000_km=values["VALUATION_FALLBACK_BETA_PER_1000_KM"],
        market_log_dispersion=values["VALUATION_MARKET_LOG_DISPERSION"],
        projection=ProjectionSettings(
            enabled=projection_enabled,
            annual_depreciation_rate=annual_depreciation_rate,
            annual_market_change_rate=annual_market_change_rate,
            annual_log_uncertainty=annual_log_uncertainty,
            source=projection_source,
        ),
    )


def _environment_bool(name: str, *, default: bool) -> bool:
    """Read an environment setting that must be true or false."""

    raw = os.environ.get(name)
    if raw is None:
        return default

    value = raw.strip().lower()
    if value not in ("true", "false"):
        raise ValueError(f"Invalid {name}: expected true or false")

    return value == "true"


def _environment_float(
    name: str,
    *,
    minimum: float,
    maximum: float | None = None,
    minimum_inclusive: bool = True,
    maximum_inclusive: bool = True,
) -> float | None:
    """Read an optional numeric setting and check that it is finite and within its allowed range."""

    raw = os.environ.get(name)
    if raw is None:
        return None

    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"Invalid {name}") from error

    if not isfinite(value):
        raise ValueError(f"Invalid {name}")

    below_minimum = value < minimum if minimum_inclusive else value <= minimum
    above_maximum = maximum is not None and (
        value > maximum if maximum_inclusive else value >= maximum
    )
    if below_minimum or above_maximum:
        raise ValueError(f"Invalid {name}")

    return value


def parse_query() -> ValuationQuery:
    """Check the requested vehicle details and require a unit when mileage is supplied."""

    # Reject unexpected or repeated parameters so typos cannot silently change the
    # request.
    allowed = {
        "year",
        "make",
        "model",
        "mileage",
        "mileageUnit",
        "currency",
        "country",
        "region",
    }
    if set(request.args) - allowed or any(
        len(request.args.getlist(key)) != 1 for key in request.args
    ):
        raise ValueError("Unknown or repeated query parameter")

    # Check the year, make, and model before looking up comparable listings.
    year = request.args.get("year", "")
    if (
        not re.fullmatch(r"[0-9]{4}", year)
        or not 1886 <= int(year) <= datetime.now(UTC).year + 1
    ):
        raise ValueError("year must be a valid vehicle model year")

    make = request.args.get("make", "").strip()
    model = request.args.get("model", "").strip()
    if not make or not model or len(make) > 120 or len(model) > 120:
        raise ValueError("make and model are required (maximum 120 characters)")

    # Check currency and location so the estimate does not mix separate markets.
    currency = request.args.get("currency", "USD").upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be a three-letter code")

    country = request.args.get("country")
    if country is not None:
        country = country.upper()
        if not re.fullmatch(r"[A-Z]{2}", country):
            raise ValueError("country must be a two-letter code")

    region = request.args.get("region")
    if region is not None:
        region = region.strip()
        if not region or len(region) > 80:
            raise ValueError("region must be a name of at most 80 characters")

    # Check the mileage and its unit before converting it to kilometres.
    mileage = request.args.get("mileage")
    unit = request.args.get("mileageUnit")

    mileage_km = None
    if mileage is not None:
        try:
            mileage_km = float(mileage)
        except ValueError as error:
            raise ValueError("mileage must be a finite nonnegative number") from error

        if not isfinite(mileage_km) or not 0 <= mileage_km <= 10_000_000:
            raise ValueError("mileage must be between 0 and 10000000")

        if unit not in ("KM", "MI"):
            raise ValueError("mileageUnit must be KM or MI when mileage is supplied")

        mileage_km *= 1.609344 if unit == "MI" else 1
    elif unit is not None:
        raise ValueError("mileageUnit requires mileage")

    return ValuationQuery(int(year), make, model, mileage_km, currency, country, region)


@valuation_blueprint.get("/valuations/locations")
def valuation_locations():
    """Return dealer regions found in the data, leaving missing countries empty."""

    try:
        # Only return regions found in the data; leave missing countries empty.
        rows = db.session.execute(
            select(Dealer.country_code, Dealer.state_or_province).distinct()
        )
        locations = {}

        for country, region in rows:
            region = (region or "").strip()
            country = (country or "").strip().upper() or None

            if not region or (
                country is not None and not re.fullmatch(r"[A-Z]{2}", country)
            ):
                continue

            locations[(country, region.casefold())] = {
                "country": country,
                "region": region,
            }

        return {
            "locations": sorted(
                locations.values(),
                key=lambda entry: (entry["region"].casefold(), entry["country"] or ""),
            )
        }
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Valuation location query failed")

        return {"error": "Locations temporarily unavailable"}, 503


@valuation_blueprint.get("/valuations/model-a")
def model_a():
    """Return an asking-price estimate, or explain why the listings cannot support one."""

    try:
        query = parse_query()
    except ValueError as error:
        return {"error": str(error)}, 400

    # Use the same UTC date when selecting listings, calculating prices, and counting
    # totals.
    as_of = datetime.now(UTC).date()

    try:
        rows = load_comparables(query, as_of)
        result = valuate(rows, query, as_of, current_app.config["MODEL_A_SETTINGS"])

        # Count recorded listings separately from the group used to calculate the price.
        response: ValuationApiResponse = {
            **result,
            "listingSummary": load_listing_summary(query, as_of),
        }
    except InsufficientEvidence as error:
        return {
            "error": "Insufficient evidence",
            "reason": str(error),
            "reasonCode": error.code,
        }, 422
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Model A comparable query failed")

        return {"error": "Valuation temporarily unavailable"}, 503

    return response
