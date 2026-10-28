"""Tests for catalog endpoints and their caches."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import sleep

import pytest
from app import create_app


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch):
    """Create a fresh app and cache without connecting to a database."""
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.delenv("CATALOG_CACHE_TTL_SECONDS", raising=False)
    application = create_app()
    application.config.update(TESTING=True)
    yield application


def create_catalog_tables(app) -> None:
    """Create just the catalog tables in this test's in-memory SQLite database."""
    from sqlalchemy import MetaData

    from app.extensions import db
    from app.models import Make, Model, ModelYear, Trim

    metadata = MetaData()
    for model in (Make, Model, ModelYear, Trim):
        table = model.__table__.to_metadata(metadata)
        for name in ("created_at", "updated_at"):
            table.c[name].server_default = None
            table.c[name].nullable = True
    with app.app_context():
        metadata.create_all(db.engine)


def seed_catalog(app) -> None:
    """Add makes with and without models, years, and trims to the test database."""
    from app.extensions import db

    create_catalog_tables(app)
    with app.app_context(), db.engine.begin() as connection:
        connection.execute(
            db.metadata.tables["makes"].insert(),
            [
                {"id": 1, "name": "Toyota", "normalized_name": "TOYOTA"},
                {"id": 2, "name": "Honda", "normalized_name": "HONDA"},
                {"id": 3, "name": "Empty", "normalized_name": "EMPTY"},
                {"id": 4, "name": "No years", "normalized_name": "NO YEARS"},
            ],
        )
        connection.execute(
            db.metadata.tables["models"].insert(),
            [
                {"id": 10, "make_id": 1, "name": "RAV4", "normalized_name": "RAV4"},
                {"id": 11, "make_id": 1, "name": "Camry", "normalized_name": "CAMRY"},
                {"id": 12, "make_id": 2, "name": "Civic", "normalized_name": "CIVIC"},
                {"id": 13, "make_id": 4, "name": "Model", "normalized_name": "MODEL"},
            ],
        )
        connection.execute(
            db.metadata.tables["model_years"].insert(),
            [
                {"id": 100, "model_id": 11, "year": 2020},
                {"id": 101, "model_id": 11, "year": 2022},
                {"id": 102, "model_id": 10, "year": 2021},
                {"id": 103, "model_id": 12, "year": 2019},
            ],
        )
        connection.execute(
            db.metadata.tables["trims"].insert(),
            [
                {
                    "id": 1000,
                    "model_year_id": 101,
                    "name": "XLE",
                    "normalized_name": "XLE",
                },
                {
                    "id": 1001,
                    "model_year_id": 101,
                    "name": "LE",
                    "normalized_name": "LE",
                },
                {
                    "id": 1002,
                    "model_year_id": 103,
                    "name": "Sport",
                    "normalized_name": "SPORT",
                },
            ],
        )


def test_make_list_is_make_only_alphabetical_and_cached_without_sql_hits(app):
    from sqlalchemy import event

    from app.extensions import db

    seed_catalog(app)
    statements: list[str] = []

    def capture_query(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", capture_query)
    try:
        client = app.test_client()
        response = client.get("/api/makes")
        cached_response = client.get("/api/makes")
    finally:
        event.remove(engine, "before_cursor_execute", capture_query)

    assert response.status_code == 200
    assert cached_response.data == response.data
    assert response.get_json() == {
        "makes": [
            {"id": 3, "name": "Empty"},
            {"id": 2, "name": "Honda"},
            {"id": 4, "name": "No years"},
            {"id": 1, "name": "Toyota"},
        ]
    }
    assert len(statements) == 1


def test_make_detail_groups_sorts_scopes_and_preserves_empty_descendants(app):
    from sqlalchemy import event

    from app.extensions import db

    seed_catalog(app)
    statements: list[str] = []

    def capture_query(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", capture_query)
    try:
        client = app.test_client()
        response = client.get("/api/makes/1")
        cached_response = client.get("/api/makes/1")
    finally:
        event.remove(engine, "before_cursor_execute", capture_query)

    assert response.status_code == 200
    assert cached_response.data == response.data
    assert response.get_json() == {
        "id": 1,
        "name": "Toyota",
        "models": [
            {
                "id": 11,
                "name": "Camry",
                "years": [
                    {
                        "id": 101,
                        "year": 2022,
                        "trims": [
                            {"id": 1001, "name": "LE"},
                            {"id": 1000, "name": "XLE"},
                        ],
                    },
                    {"id": 100, "year": 2020, "trims": []},
                ],
            },
            {
                "id": 10,
                "name": "RAV4",
                "years": [{"id": 102, "year": 2021, "trims": []}],
            },
        ],
    }
    assert len(statements) == 1
    assert "LEFT OUTER JOIN" in statements[0]

    assert client.get("/api/makes/2").get_json() == {
        "id": 2,
        "name": "Honda",
        "models": [
            {
                "id": 12,
                "name": "Civic",
                "years": [
                    {
                        "id": 103,
                        "year": 2019,
                        "trims": [
                            {"id": 1002, "name": "Sport"},
                        ],
                    }
                ],
            }
        ],
    }
    assert client.get("/api/makes/3").get_json() == {
        "id": 3,
        "name": "Empty",
        "models": [],
    }
    assert client.get("/api/makes/4").get_json() == {
        "id": 4,
        "name": "No years",
        "models": [{"id": 13, "name": "Model", "years": []}],
    }


def test_empty_make_list_is_a_valid_cached_response(app):
    from sqlalchemy import event

    from app.extensions import db

    create_catalog_tables(app)
    statements: list[str] = []

    def capture_query(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", capture_query)
    try:
        client = app.test_client()
        first_response = client.get("/api/makes")
        cached_response = client.get("/api/makes")
    finally:
        event.remove(engine, "before_cursor_execute", capture_query)

    assert first_response.status_code == 200
    assert first_response.get_json() == {"makes": []}
    assert cached_response.data == first_response.data
    assert len(statements) == 1


def test_unknown_make_returns_generic_404_and_does_not_cache_id(app, monkeypatch):
    attempts: list[int] = []

    def unknown(make_id: int):
        attempts.append(make_id)
        return None

    monkeypatch.setattr("app.catalog._load_make_detail", unknown)
    client = app.test_client()
    for _ in range(2):
        response = client.get("/api/makes/987654")
        assert response.status_code == 404
        assert response.get_json() == {"error": "Make not found"}
    assert attempts == [987654, 987654]
    assert app.extensions["catalog_cache"].make_details == {}


def test_database_errors_are_generic_and_successful_requests_retry(app, monkeypatch):
    attempts = 0

    def fail_once():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("private database detail")
        return {"makes": []}

    monkeypatch.setattr("app.catalog._load_makes", fail_once)
    client = app.test_client()
    failed = client.get("/api/makes")
    assert failed.status_code == 503
    assert failed.get_json() == {"error": "Catalog temporarily unavailable"}
    assert b"private database detail" not in failed.data
    assert client.get("/api/makes").status_code == 200
    assert attempts == 2


def test_detail_database_error_is_503_and_retries_without_caching(app, monkeypatch):
    attempts = 0

    def load_detail(make_id: int):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("private database detail")
        return {"id": make_id, "name": "Toyota", "models": []}

    monkeypatch.setattr("app.catalog._load_make_detail", load_detail)
    client = app.test_client()
    failed = client.get("/api/makes/1")
    assert failed.status_code == 503
    assert failed.get_json() == {"error": "Catalog temporarily unavailable"}
    assert b"private database detail" not in failed.data
    assert client.get("/api/makes/1").status_code == 200
    assert attempts == 2


def test_make_list_and_details_have_independent_ttl_cache_keys(app, monkeypatch):
    from app import catalog

    now = 100.0
    make_calls = 0
    detail_calls: list[int] = []

    def load_makes():
        nonlocal make_calls
        make_calls += 1
        return {"makes": []}

    def load_detail(make_id: int):
        detail_calls.append(make_id)
        return {"id": make_id, "name": f"Make {make_id}", "models": []}

    monkeypatch.setattr(catalog, "monotonic", lambda: now)
    monkeypatch.setattr(catalog, "_load_makes", load_makes)
    monkeypatch.setattr(catalog, "_load_make_detail", load_detail)
    client = app.test_client()

    client.get("/api/makes")
    client.get("/api/makes/1")
    client.get("/api/makes/2")
    client.get("/api/makes")
    client.get("/api/makes/1")
    assert (make_calls, detail_calls) == (1, [1, 2])

    now += 900
    client.get("/api/makes/1")
    assert (make_calls, detail_calls) == (1, [1, 2, 1])


def test_expired_make_detail_cache_refreshes_from_database(app, monkeypatch):
    from sqlalchemy import event

    from app import catalog
    from app.extensions import db

    seed_catalog(app)
    now = 100.0
    monkeypatch.setattr(catalog, "monotonic", lambda: now)
    statements: list[str] = []

    def capture_query(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", capture_query)
    try:
        client = app.test_client()
        first_response = client.get("/api/makes/1")
        cached_response = client.get("/api/makes/1")
        assert len(statements) == 1

        now += app.config["CATALOG_CACHE_TTL_SECONDS"]
        refreshed_response = client.get("/api/makes/1")
    finally:
        event.remove(engine, "before_cursor_execute", capture_query)

    assert first_response.status_code == 200
    assert cached_response.data == first_response.data
    assert refreshed_response.status_code == 200
    assert refreshed_response.data == first_response.data
    assert len(statements) == 2


def test_concurrent_cold_make_detail_requests_share_one_refresh(app, monkeypatch):
    calls: list[int] = []
    calls_lock = Lock()

    def load_detail(make_id: int):
        with calls_lock:
            calls.append(make_id)
        sleep(0.05)
        return {"id": make_id, "name": "Toyota", "models": []}

    monkeypatch.setattr("app.catalog._load_make_detail", load_detail)

    def request_detail():
        return app.test_client().get("/api/makes/1")

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _index: request_detail(), range(8)))

    assert [response.status_code for response in responses] == [200] * 8
    assert all(response.get_json() == responses[0].get_json() for response in responses)
    assert calls == [1]


def test_expired_refresh_failure_returns_503_then_recovers(app, monkeypatch):
    from app import catalog

    now = 100.0
    attempts = 0

    def load_makes():
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError("database unavailable")
        return {"makes": []}

    monkeypatch.setattr(catalog, "monotonic", lambda: now)
    monkeypatch.setattr(catalog, "_load_makes", load_makes)
    client = app.test_client()
    assert client.get("/api/makes").status_code == 200
    original_entry = app.extensions["catalog_cache"].makes

    now += 900
    assert client.get("/api/makes").status_code == 503
    assert app.extensions["catalog_cache"].makes is original_entry
    assert client.get("/api/makes").status_code == 200
    assert attempts == 3


def test_concurrent_cold_requests_share_one_refresh(app, monkeypatch):
    calls = 0
    calls_lock = Lock()

    def load_makes():
        nonlocal calls
        with calls_lock:
            calls += 1
        sleep(0.05)
        return {"makes": []}

    monkeypatch.setattr("app.catalog._load_makes", load_makes)

    def request_catalog():
        return app.test_client().get("/api/makes").status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda _index: request_catalog(), range(8)))

    assert statuses == [200] * 8
    assert calls == 1


def test_apps_have_independent_catalog_caches(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    first = create_app()
    second = create_app()
    calls = 0

    def load_makes():
        nonlocal calls
        calls += 1
        return {"makes": []}

    monkeypatch.setattr("app.catalog._load_makes", load_makes)
    first.test_client().get("/api/makes")
    first.test_client().get("/api/makes")
    second.test_client().get("/api/makes")
    assert calls == 2


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "bad"])
def test_catalog_ttl_must_be_positive_and_finite(monkeypatch, value):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("CATALOG_CACHE_TTL_SECONDS", value)
    with pytest.raises(ValueError, match="CATALOG_CACHE_TTL_SECONDS"):
        create_app()
