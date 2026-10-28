"""Load matching year/make/model listings from completed imports, keeping one snapshot per vehicle."""

from datetime import date, timedelta

from sqlalchemy import func, select

from app.extensions import db
from app.importing.service import completed_observations
from app.models import (
    Dealer,
    Listing,
    ListingObservation,
    Make,
    Model,
    ModelYear,
    Vehicle,
)

from .contracts import (
    DEFAULT_SOURCE_CURRENCY,
    DEFAULT_SOURCE_MILEAGE_UNIT,
    Comparable,
    ValuationQuery,
)
from .service import InsufficientEvidence


def effective_observation_time():
    """Use the first available source date, never the time the row was imported or updated."""

    return func.coalesce(
        ListingObservation.observed_at,
        ListingObservation.first_seen_date,
        ListingObservation.last_seen_date,
    )


def load_comparables(query: ValuationQuery, as_of: date) -> list[Comparable]:
    """Choose the latest snapshot before checking its values so a bad recent row cannot bring an older one back."""

    observation = ListingObservation
    observed_at = effective_observation_time()

    # Choose each vehicle's latest snapshot before checking price and mileage. A bad
    # latest row must not make an older, more favorable one eligible again.
    ranked = (
        completed_observations()
        .join(Listing, observation.listing_id == Listing.id)
        .join(Vehicle, Listing.vehicle_id == Vehicle.id)
        .join(ModelYear, Vehicle.model_year_id == ModelYear.id)
        .join(Model, ModelYear.model_id == Model.id)
        .join(Make, Model.make_id == Make.id)
        .where(
            ModelYear.year == query.year,
            Make.normalized_name == query.make.strip().lower(),
            Model.normalized_name == query.model.strip().lower(),
            observed_at < as_of + timedelta(days=1),
        )
        .with_only_columns(
            observation.id.label("observation_id"),
            func.row_number()
            .over(
                partition_by=Vehicle.id,
                order_by=(observed_at.desc(), observation.id.desc()),
            )
            .label("rank"),
        )
        .subquery()
    )

    # Check those latest snapshots. Use the source's currency and mileage unit when
    # provided.
    statement = (
        select(
            observation,
            Listing.vehicle_id,
            Listing.dealer_id,
            Dealer.country_code,
            Dealer.state_or_province,
            observed_at.label("effective_observed_at"),
            Dealer.city,
        )
        .join(ranked, ranked.c.observation_id == observation.id)
        .join(Listing, observation.listing_id == Listing.id)
        .join(Dealer, Listing.dealer_id == Dealer.id)
        .where(
            ranked.c.rank == 1,
            func.coalesce(observation.currency_code, DEFAULT_SOURCE_CURRENCY)
            == query.currency,
            observation.listing_price > 0,
            (
                observation.listing_mileage.is_(None)
                | (observation.listing_mileage >= 0)
            ),
            func.coalesce(observation.mileage_unit, DEFAULT_SOURCE_MILEAGE_UNIT).in_(
                ("KM", "MI")
            ),
        )
    )
    if query.country:
        statement = statement.where(Dealer.country_code == query.country)

    # Convert recorded mileage to kilometres once at the repository boundary.
    rows = [
        Comparable(
            row.id,
            vehicle_id,
            dealer_id,
            float(row.listing_price),
            (
                float(row.listing_mileage)
                * (
                    1.609344
                    if (row.mileage_unit or DEFAULT_SOURCE_MILEAGE_UNIT) == "MI"
                    else 1
                )
                if row.listing_mileage is not None
                else None
            ),
            effective_at.date(),
            country,
            region,
            city=city,
        )
        for row, vehicle_id, dealer_id, country, region, effective_at, city in db.session.execute(
            statement
        )
    ]

    if not rows:
        explain_empty_selection(query)

    return rows


def explain_empty_selection(query: ValuationQuery) -> None:
    """Explain when matching listings are unusable because their source dates are missing."""

    observation = ListingObservation
    observed_at = effective_observation_time()
    statement = (
        completed_observations()
        .join(Listing, observation.listing_id == Listing.id)
        .join(Vehicle, Listing.vehicle_id == Vehicle.id)
        .join(ModelYear, Vehicle.model_year_id == ModelYear.id)
        .join(Model, ModelYear.model_id == Model.id)
        .join(Make, Model.make_id == Make.id)
        .join(Dealer, Listing.dealer_id == Dealer.id)
        .where(
            ModelYear.year == query.year,
            Make.normalized_name == query.make.strip().lower(),
            Model.normalized_name == query.model.strip().lower(),
        )
        .with_only_columns(func.count(), func.count(observed_at))
    )
    if query.country:
        statement = statement.where(Dealer.country_code == query.country)

    # Check whether missing source dates explain why no listings could be used.
    total, dates = db.session.execute(statement).one()
    if total and not dates:
        raise InsufficientEvidence(
            "Matching listings have no observation, first-seen, or last-seen date",
            "source_metadata_missing",
        )
