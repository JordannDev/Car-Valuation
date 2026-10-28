"""Count recorded listings separately from the listings used to calculate a price."""

from datetime import date, timedelta

from sqlalchemy import case, func

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

from .contracts import DEFAULT_SOURCE_CURRENCY, ListingSummaryResponse, ValuationQuery
from .repository import effective_observation_time


def load_listing_summary(query: ValuationQuery, as_of: date) -> ListingSummaryResponse:
    """Count separate listings and find their lowest and highest recorded prices.

    Match the requested year, make, model, currency, and country in completed imports.
    Do not apply the pricing calculation's age, mileage, duplicate-vehicle, or outlier
    filters.
    """

    observation = ListingObservation
    observed_at = effective_observation_time()

    # A listing still counts even when a snapshot has no positive price.
    positive_price = case((observation.listing_price > 0, observation.listing_price))
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
            func.coalesce(observation.currency_code, DEFAULT_SOURCE_CURRENCY)
            == query.currency,
            observed_at < as_of + timedelta(days=1),
        )
        .with_only_columns(
            func.count(func.distinct(Listing.id)),
            func.max(observed_at),
            func.min(positive_price),
            func.max(positive_price),
        )
    )
    if query.country:
        statement = statement.where(Dealer.country_code == query.country)

    # Calculate totals in the database instead of loading every snapshot into memory.
    count, latest, low, high = db.session.execute(statement).one()

    return {
        "totalListings": count,
        "latestListingDate": latest.date().isoformat() if latest else None,
        "lowestRecordedPrice": float(low) if low is not None else None,
        "highestRecordedPrice": float(high) if high is not None else None,
    }
