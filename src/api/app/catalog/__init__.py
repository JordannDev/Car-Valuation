"""Read-only catalog endpoints with a separate in-memory cache for each app."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic
from typing import TypedDict

from flask import Blueprint, Flask, Response, current_app
from sqlalchemy import select

from app.extensions import db
from app.models import Make, Model, ModelYear, Trim

logger = logging.getLogger(__name__)
catalog_blueprint = Blueprint("catalog", __name__)


class MakeListEntry(TypedDict):
    id: int
    name: str


class TrimEntry(TypedDict):
    id: int
    name: str


class ModelYearEntry(TypedDict):
    id: int
    year: int
    trims: list[TrimEntry]


class ModelEntry(TypedDict):
    id: int
    name: str
    years: list[ModelYearEntry]


class MakeDetailEntry(TypedDict):
    id: int
    name: str
    models: list[ModelEntry]


@dataclass(frozen=True)
class CacheEntry:
    """One cached JSON response and the time it expires."""

    body: str
    expires_at: float


@dataclass
class CatalogCache:
    """Cached responses and a lock to keep requests from refreshing them at the same time."""

    lock: Lock = field(default_factory=Lock)
    makes: CacheEntry | None = None
    make_details: dict[int, CacheEntry] = field(default_factory=dict)


def init_catalog_cache(app: Flask) -> None:
    """Give this app its own cache within the current worker process."""

    app.extensions["catalog_cache"] = CatalogCache()


def _load_makes() -> dict[str, list[MakeListEntry]]:
    """Load all known makes without also loading their models, years, and trims."""

    rows = db.session.execute(
        select(Make.id, Make.name).order_by(Make.name.asc(), Make.id.asc())
    )

    return {"makes": [{"id": make_id, "name": name} for make_id, name in rows]}


def _load_make_detail(make_id: int) -> MakeDetailEntry | None:
    """Load a make and group its models, years, and trims from one query."""

    # Keep the make in the result even if it has no models, years, or trims yet.
    statement = (
        select(
            Make.id,
            Make.name,
            Model.id,
            Model.name,
            ModelYear.id,
            ModelYear.year,
            Trim.id,
            Trim.name,
        )
        .select_from(Make)
        .outerjoin(Model, Model.make_id == Make.id)
        .outerjoin(ModelYear, ModelYear.model_id == Model.id)
        .outerjoin(Trim, Trim.model_year_id == ModelYear.id)
        .where(Make.id == make_id)
        .order_by(
            Model.name.asc(),
            Model.id.asc(),
            ModelYear.year.desc(),
            ModelYear.id.asc(),
            Trim.name.asc(),
            Trim.id.asc(),
        )
    )

    rows = db.session.execute(statement)
    make: MakeDetailEntry | None = None

    # Reuse the model and year entries while grouping repeated query rows into the response.
    models_by_id: dict[int, ModelEntry] = {}
    years_by_id: dict[int, ModelYearEntry] = {}

    for (
        make_row_id,
        make_name,
        model_id,
        model_name,
        year_id,
        year,
        trim_id,
        trim_name,
    ) in rows:
        if make is None:
            make = {"id": make_row_id, "name": make_name, "models": []}

        if model_id is None:
            # The outer join can return a make without any models.
            continue

        # Reuse the entry if an earlier row already added this model.
        model = models_by_id.get(model_id)
        if model is None:
            # Build a dictionary, so model is no longer None after this assignment.
            model = ModelEntry(id=model_id, name=model_name, years=[])

            models_by_id[model_id] = model
            make["models"].append(model)

        if year_id is None:
            # The outer join can return a model without any years.
            continue

        # Reuse the year entry when multiple rows contain its trims.
        model_year = years_by_id.get(year_id)
        if model_year is None:
            # Build a dictionary, so model_year is no longer None after this assignment.
            model_year = ModelYearEntry(id=year_id, year=year, trims=[])

            years_by_id[year_id] = model_year
            model["years"].append(model_year)

        # The outer join can return a year without trims; only append existing trims.
        if trim_id is not None:
            model_year["trims"].append({"id": trim_id, "name": trim_name})

    return make


def _response(body: str, status: int = 200) -> Response:
    """Return a JSON response using text that has already been serialized."""

    return Response(body, status=status, mimetype="application/json")


def _unavailable() -> Response:
    """Return the same temporary-unavailable response for catalog database errors."""

    return _response(json.dumps({"error": "Catalog temporarily unavailable"}), 503)


def _cached_makes(cache: CatalogCache) -> Response:
    """Return cached makes, or load and cache them if the entry has expired."""

    entry = cache.makes
    if entry is not None and monotonic() < entry.expires_at:
        return _response(entry.body)

    # Another request may have refreshed the cache while we waited. Check again after
    # taking the lock.
    with cache.lock:
        entry = cache.makes
        if entry is not None and monotonic() < entry.expires_at:
            return _response(entry.body)

        try:
            body = json.dumps(_load_makes(), separators=(",", ":"))
        except Exception:
            logger.exception("Catalog make-list refresh failed")

            return _unavailable()

        # Cache successful results only, and start their lifetime after loading
        # finishes.
        cache.makes = CacheEntry(
            body=body,
            expires_at=monotonic() + current_app.config["CATALOG_CACHE_TTL_SECONDS"],
        )

        return _response(body)


def _cached_make_detail(cache: CatalogCache, make_id: int) -> Response:
    """Return a make's cached details, or refresh them when needed."""

    entry = cache.make_details.get(make_id)
    if entry is not None and monotonic() < entry.expires_at:
        return _response(entry.body)

    # Take the lock and check again so waiting requests do not repeat the same database
    # work.
    with cache.lock:
        entry = cache.make_details.get(make_id)
        if entry is not None and monotonic() < entry.expires_at:
            return _response(entry.body)

        try:
            detail = _load_make_detail(make_id)
            if detail is None:
                return _response(json.dumps({"error": "Make not found"}), 404)

            body = json.dumps(detail, separators=(",", ":"))
        except Exception:
            logger.exception("Catalog make-detail refresh failed")

            return _unavailable()

        # Only cache makes that exist. Missing makes and failed queries can be checked
        # again later.
        cache.make_details[make_id] = CacheEntry(
            body=body,
            expires_at=monotonic() + current_app.config["CATALOG_CACHE_TTL_SECONDS"],
        )

        return _response(body)


@catalog_blueprint.get("/makes")
def get_catalog() -> Response:
    """Return makes in alphabetical order, or a temporary-unavailable response."""

    cache: CatalogCache = current_app.extensions["catalog_cache"]

    return _cached_makes(cache)


@catalog_blueprint.get("/makes/<int:make_id>")
def get_make_catalog(make_id: int) -> Response:
    """Return a make with its models, years, and trims."""

    cache: CatalogCache = current_app.extensions["catalog_cache"]

    return _cached_make_detail(cache, make_id)
