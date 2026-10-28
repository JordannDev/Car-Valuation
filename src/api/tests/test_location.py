"""Tests for the endpoint that suggests a visitor's country."""

from __future__ import annotations

import pytest
from app import create_app


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch):
    """Create a test app with its own in-memory SQLite database."""
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    application = create_app()
    application.config.update(TESTING=True)
    yield application


@pytest.mark.parametrize(
    "header,value",
    [
        ("CF-IPCountry", "CA"),
        ("CF-IPCountry", "us"),
        ("X-Vercel-IP-Country", "CA"),
        ("X-Vercel-IP-Country", "us"),
    ],
)
def test_location_returns_supported_country_headers(app, header: str, value: str):
    response = app.test_client().get("/api/location", headers={header: value})

    assert response.status_code == 200
    assert response.get_json() == {"countryCode": value.strip().upper()}


def test_valid_cloudflare_country_takes_precedence_over_vercel(app):
    response = app.test_client().get(
        "/api/location",
        headers={"CF-IPCountry": "ca", "X-Vercel-IP-Country": "US"},
    )

    assert response.get_json() == {"countryCode": "CA"}


def test_vercel_country_is_used_when_cloudflare_value_is_unsupported(app):
    response = app.test_client().get(
        "/api/location",
        headers={"CF-IPCountry": "GB", "X-Vercel-IP-Country": "us"},
    )

    assert response.get_json() == {"countryCode": "US"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"CF-IPCountry": "XX"},
        {"CF-IPCountry": "T1"},
        {"CF-IPCountry": "US,CA"},
        {"CF-IPCountry": "USA"},
        {"CF-IPCountry": ""},
    ],
)
def test_location_returns_null_for_missing_or_unsupported_country(app, headers):
    response = app.test_client().get("/api/location", headers=headers)

    assert response.get_json() == {"countryCode": None}


def test_location_response_is_private_and_does_not_query_database(app):
    from sqlalchemy import event

    from app.extensions import db

    statements: list[str] = []

    def capture_query(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", capture_query)
    try:
        response = app.test_client().get(
            "/api/location", headers={"CF-IPCountry": "US"}
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_query)

    assert response.headers["Cache-Control"] == "private, no-store"
    assert statements == []
