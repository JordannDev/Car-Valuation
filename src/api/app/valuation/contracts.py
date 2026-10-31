"""Normalized inputs, calibration settings, and response contracts for Model A pricing."""

from dataclasses import dataclass, field
from datetime import date
from math import isfinite
from typing import Literal, TypedDict

# The project owner confirmed these source defaults. Use recorded units when available.
DEFAULT_SOURCE_CURRENCY = "USD"
DEFAULT_SOURCE_MILEAGE_UNIT = "MI"

type MileageSource = Literal[
    "provided", "comparable_median", "rounded_down", "unavailable"
]


class MarketRangeResponse(TypedDict):
    """Rounded low and high asking-price estimates in the response currency."""

    low: int
    high: int


class ConfidenceResponse(TypedDict):
    """Confidence rating and number of vehicles contributing to the estimate."""

    level: Literal["low", "medium", "high"]
    independentVehicles: int


class ComparableLocationResponse(TypedDict):
    """Recorded listing location; fields may be absent in source data."""

    city: str | None
    region: str | None
    country: str | None


class ComparableResponse(TypedDict):
    """Listing data used by the comparable table and mileage chart."""

    observationId: int
    vehicleId: int
    listingPrice: float
    mileageKm: float | None
    location: ComparableLocationResponse
    observedOn: str
    weight: float


class LocationSelectionResponse(TypedDict):
    """Requested location and whether the estimate fell back to a wider area."""

    country: str | None
    region: str | None
    scope: Literal["region", "country", "all"]
    fallback: bool


class HistoricalReferenceResponse(TypedDict):
    """Unprojected price used to position the estimate on the listing chart."""

    estimatedAskingPrice: int


class ListingSummaryResponse(TypedDict):
    """All matching recorded listings, including listings excluded from pricing."""

    totalListings: int
    latestListingDate: str | None
    lowestRecordedPrice: float | None
    highestRecordedPrice: float | None


class EvaluationResult(TypedDict):
    """Calculated asking price and the listings supporting it."""

    currency: str
    estimatedAskingPrice: int
    marketRange: MarketRangeResponse
    referenceMileageKm: float | None
    requestedMileageKm: float | None
    mileageSource: MileageSource
    confidence: ConfidenceResponse
    comparables: list[ComparableResponse]


class EvaluationResultWithTime(EvaluationResult):
    """Valuation with its time basis and unprojected chart price."""

    valuationBasis: Literal["current", "historical", "projected"]
    historicalReference: HistoricalReferenceResponse


class ValuationResult(EvaluationResultWithTime):
    """Completed valuation with the selected location scope."""

    locationSelection: LocationSelectionResponse


class ValuationApiResponse(ValuationResult):
    """HTTP response with the broader recorded listing summary."""

    listingSummary: ListingSummaryResponse


@dataclass(frozen=True)
class ValuationQuery:
    """Requested vehicle and market; supplied mileage is normalized to kilometres."""

    year: int
    make: str
    model: str
    mileage_km: float | None = None
    currency: str = "USD"
    country: str | None = None
    region: str | None = None


@dataclass(frozen=True)
class Comparable:
    """One listing observation with price, mileage, and dates in normalized units.

    ``price`` is in the requested currency and ``mileage_km`` is kilometres.
    ``observed_on`` is the source observation date selected for valuation.
    """

    observation_id: int
    vehicle_id: int
    dealer_id: int
    price: float
    mileage_km: float | None
    observed_on: date
    country: str | None = None
    region: str | None = None
    city: str | None = None


@dataclass(frozen=True)
class ProjectionSettings:
    """Optional yearly adjustments for bringing a historical estimate up to date."""

    enabled: bool = False
    annual_depreciation_rate: float | None = None
    annual_market_change_rate: float | None = None
    annual_log_uncertainty: float | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        """Check that yearly adjustments keep prices positive and uncertainty stays finite."""

        if self.annual_depreciation_rate is not None and (
            not isfinite(self.annual_depreciation_rate)
            or not 0 <= self.annual_depreciation_rate < 1
        ):
            raise ValueError(
                "Annual depreciation rate must be finite and between zero and one"
            )

        if self.annual_market_change_rate is not None and (
            not isfinite(self.annual_market_change_rate)
            or self.annual_market_change_rate <= -1
        ):
            raise ValueError(
                "Annual market change rate must be finite and greater than -1"
            )

        if self.annual_log_uncertainty is not None and (
            not isfinite(self.annual_log_uncertainty)
            or self.annual_log_uncertainty <= 0
        ):
            raise ValueError("Annual log uncertainty must be positive and finite")


@dataclass(frozen=True)
class ModelASettings:
    """Initial pricing thresholds, with fallback values supplied through calibration."""

    minimum_effective: float = 5.0
    minimum_regression: int = 10
    minimum_mileage_span_km: float = 20_000
    freshness_days: float = 45
    mileage_decay_km: float = 80_000
    dealer_share_cap: float = 0.2
    huber_scale: float = 0.1
    huber_enabled: bool = True
    outlier_filter_enabled: bool = True

    # A fallback must be configured if the listings cannot support a reliable mileage
    # fit.
    fallback_beta_per_1000_km: float | None = None
    market_log_dispersion: float | None = None
    projection: ProjectionSettings = field(default_factory=ProjectionSettings)
