"""Suggest a country for the frontend's default mileage unit."""

from __future__ import annotations

from typing import TypedDict

from flask import Blueprint, Response, jsonify, request

location_blueprint = Blueprint("location", __name__)
_SUPPORTED_COUNTRIES = frozenset({"CA", "US"})


class LocationResponse(TypedDict):
    countryCode: str | None


def _supported_country(value: str | None) -> str | None:
    """Read a supported two-letter country code from a CDN header."""

    if value is None:
        return None

    country = value.strip().upper()

    return country if country in _SUPPORTED_COUNTRIES else None


@location_blueprint.get("/location")
def get_location() -> Response:
    """Return a country hint from the deployment headers without caching it for other visitors."""

    # Use Cloudflare's hint first if both providers send one.
    country_code = _supported_country(request.headers.get("CF-IPCountry"))
    if country_code is None:
        country_code = _supported_country(request.headers.get("X-Vercel-IP-Country"))

    # This hint belongs to one request, so shared caches must not reuse it for another
    # visitor.
    response = jsonify(LocationResponse(countryCode=country_code))
    response.headers["Cache-Control"] = "private, no-store"

    return response
