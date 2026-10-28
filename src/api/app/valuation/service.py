"""Build explainable Model A asking-price estimates from comparable observations.

``valuate`` selects the latest eligible snapshot per vehicle, then searches current
and bounded historical cohorts. ``evaluate`` adjusts prices for mileage, balances
independent dealers, and handles outliers before reporting weighted price quantiles.
The completed response retains source evidence and explains location/time fallbacks;
projection uses explicitly configured rates and never invents market adjustments.

Callers supply observations for the requested year/make/model and currency. This
module performs no database access; the repository owns those matching constraints.
"""

from collections import defaultdict
from datetime import date
from math import exp, floor, isfinite, log
from statistics import median
from typing import Literal

import numpy as np
from scipy.optimize import least_squares

from .contracts import (
    Comparable,
    EvaluationResult,
    EvaluationResultWithTime,
    HistoricalReferenceResponse,
    MileageSource,
    ModelASettings,
    ProjectionSettings,
    ValuationQuery,
    ValuationResult,
)

# Limit repeated fits on long histories while still covering the full range of source
# dates.
MAX_HISTORICAL_REFERENCE_DATES = 24
PRICE_POPULATION_LOG_GAP = 0.35
OUTLIER_SCORE_THRESHOLD = 2.5
OUTLIER_EXCLUSION_SCORE = 3.5
OUTLIER_DOWNWEIGHT = 0.25
MAD_TO_NORMAL_SCALE = 1.4826
DISPERSION_FLOOR = 1e-8
MINIMUM_MAD_SAMPLE_SIZE = 10
MEDIUM_CONFIDENCE_EFFECTIVE_COUNT = 10
HIGH_CONFIDENCE_EFFECTIVE_COUNT = 25
MEDIUM_CONFIDENCE_MAX_DISPERSION = 0.25
HIGH_CONFIDENCE_MAX_DISPERSION = 0.15
MEDIUM_CONFIDENCE_MAX_AGE_DAYS = 90
HIGH_CONFIDENCE_MAX_AGE_DAYS = 45
HIGH_CONFIDENCE_MIN_LOCAL_SHARE = 0.75
_DEFAULT_SETTINGS = ModelASettings()


class InsufficientEvidence(ValueError):
    """The available listings cannot support an estimate under the current settings."""

    def __init__(self, reason: str, code: str = "insufficient_comparables") -> None:
        super().__init__(reason)
        self.code = code


def _weighted_quantiles[T: (float, date)](
    values: list[T], weights: list[float], quantiles: list[float]
) -> list[T]:
    """Return ascending step quantiles using one sort and cumulative-weight scan.

    Internal callers supply quantiles in ascending order. Sorting value/weight pairs
    retains the original tie ordering, including floating-point accumulation at ties.
    """

    if not quantiles:
        return []

    ordered = sorted(zip(values, weights, strict=True))
    total_weight = sum(weights)
    thresholds = [total_weight * quantile for quantile in quantiles]
    results: list[T] = []
    cumulative = 0.0

    for value, weight in ordered:
        cumulative += weight
        if weight <= 0:
            continue

        # A step quantile picks an observed value without interpolation. One listing
        # may cross several thresholds, so advance through all of them before scanning on.
        while cumulative >= thresholds[len(results)]:
            results.append(value)
            if len(results) == len(thresholds):
                return results

    raise InsufficientEvidence("No positive comparable weights")


def weighted_quantile[T: (float, date)](
    values: list[T], weights: list[float], quantile: float
) -> T:
    """Return the first value whose cumulative positive weight reaches a quantile.

    Values may be prices, mileage values, or dates and are not interpolated. The
    caller must provide equally sized lists, nonnegative weights, and a quantile
    between zero and one. Missing positive weight raises :class:`InsufficientEvidence`;
    mismatched lengths raise :class:`ValueError`.
    """

    return _weighted_quantiles(values, weights, [quantile])[0]


def effective_count(weights: list[float]) -> float:
    """Calculate Kish effective sample size for normalized or raw nonnegative weights."""

    total = sum(weights)

    return total * total / sum(w * w for w in weights) if total else 0


def dealer_weights(rows: list[Comparable], raw: list[float], cap: float) -> list[float]:
    """Normalize weights while capping each dealer's total share.

    ``rows`` and ``raw`` must have equal lengths and weights must be nonnegative;
    zero-weight rows contribute nothing. Raises :class:`InsufficientEvidence` when the cap cannot cover unit weight.
    """

    totals: dict[int, float] = defaultdict(float)

    for row, weight in zip(rows, raw, strict=True):
        totals[row.dealer_id] += weight

    totals = {dealer: total for dealer, total in totals.items() if total > 0}
    if len(totals) * cap < 1 - 1e-9:
        minimum_dealers = int(np.ceil((1 - 1e-9) / cap))
        raise InsufficientEvidence(
            f"At least {_count_label(minimum_dealers)} independent dealers are required"
        )

    # Keep sharing the remaining weight until every dealer is below the limit.
    shares: dict[int, float] = {}
    remaining = dict(totals)

    while remaining:
        budget = 1 - sum(shares.values())
        total = sum(remaining.values())
        capped = [
            dealer
            for dealer, value in remaining.items()
            if budget * value / total > cap
        ]
        if not capped:
            shares.update(
                {dealer: budget * value / total for dealer, value in remaining.items()}
            )
            break

        for dealer in capped:
            shares[dealer] = cap
            del remaining[dealer]

    return [
        weight / totals[row.dealer_id] * shares[row.dealer_id] if weight > 0 else 0
        for row, weight in zip(rows, raw, strict=True)
    ]


def fit_beta(
    rows: list[Comparable], weights: list[float], settings: ModelASettings
) -> tuple[float, str]:
    """Fit log price against mileage in thousands of kilometers.

    Rows must have positive finite prices and recorded mileage in kilometers.
    The slope is constrained to be nonpositive and uses dealer-balanced weights. If the
    weighted mileage span or effective sample is insufficient, a finite nonpositive
    configured fallback is used; otherwise :class:`InsufficientEvidence` is raised.
    """

    x = np.array([mileage / 1000 for mileage in _recorded_mileages(rows)])
    y = np.array([log(row.price) for row in rows])

    # Require a useful mileage range so one extreme reading cannot support the fit by
    # itself.
    low_mileage, high_mileage = _weighted_quantiles(list(x), weights, [0.1, 0.9])
    span = high_mileage - low_mileage
    if (
        effective_count(weights) >= settings.minimum_regression
        and span * 1000 >= settings.minimum_mileage_span_km
    ):
        center = float(np.median(x))

        centered_mileage = x - center
        weight_array = np.asarray(weights)

        def loss(z):
            """Apply dealer-balanced weights to the Huber loss and its derivatives."""

            root = np.sqrt(np.maximum(z, 1e-30))

            return (
                np.array(
                    [
                        np.where(z <= 1, z, 2 * root - 1),
                        np.where(z <= 1, 1, 1 / root),
                        np.where(z <= 1, 0, -0.5 / root**3),
                    ]
                )
                * weight_array
            )

        # Keep the slope at or below zero so extra mileage cannot increase the estimated
        # price.
        result = least_squares(
            lambda parameters: parameters[0] + parameters[1] * centered_mileage - y,
            [float(np.median(y)), -0.001],
            bounds=([-np.inf, -np.inf], [np.inf, 0]),
            # SciPy accepts callable loss; its unannotated default is inferred as str.
            loss=loss,  # pyright: ignore[reportArgumentType]
            f_scale=settings.huber_scale,
            max_nfev=500,
        )
        if result.success and np.all(np.isfinite(result.x)):
            return float(result.x[1]), "huber"

    # Use a configured fallback if these listings cannot support a reliable mileage fit.
    beta = settings.fallback_beta_per_1000_km
    if beta is None:
        raise InsufficientEvidence(
            "Mileage regression is unreliable and no calibrated fallback is configured"
        )

    if not isfinite(beta) or beta > 0:
        raise ValueError("Fallback mileage coefficient must be finite and nonpositive")

    return beta, "calibrated_fallback"


def _recorded_mileages(rows: list[Comparable]) -> list[float]:
    """Require recorded mileage for calculations that cannot accept missing values."""

    mileages = [row.mileage_km for row in rows if row.mileage_km is not None]
    if len(mileages) != len(rows):
        raise ValueError(
            "Mileage calculations require a recorded mileage for every row"
        )

    return mileages


def _select_mileage(
    rows: list[Comparable], query: ValuationQuery
) -> tuple[list[Comparable], float | None, MileageSource]:
    """Choose the mileage target, excluding null mileage only when usable values exist."""

    mileage_rows = [row for row in rows if row.mileage_km is not None]
    if not mileage_rows:
        return list(rows), None, "unavailable"

    mileages = _recorded_mileages(mileage_rows)
    minimum = min(mileages)
    maximum = max(mileages)
    if query.mileage_km is None:
        return (
            mileage_rows,
            median(mileages),
            "comparable_median",
        )
    if minimum <= query.mileage_km <= maximum:
        return mileage_rows, query.mileage_km, "provided"
    if query.mileage_km > maximum:
        return mileage_rows, maximum, "rounded_down"

    # Do not extrapolate below the lowest observed mileage; use raw asking prices.
    return mileage_rows, None, "unavailable"


def _check_price_populations(
    rows: list[Comparable], minimum_mileage_span_km: float
) -> None:
    """Reject distinct price groups that overlap enough in mileage to imply mixed trims."""

    price_order = sorted(rows, key=lambda row: row.price)
    minimum_cluster = max(3, int(np.ceil(len(rows) * 0.2)))

    for split in range(minimum_cluster, len(rows) - minimum_cluster + 1):
        if (
            log(price_order[split].price / price_order[split - 1].price)
            <= PRICE_POPULATION_LOG_GAP
        ):
            continue

        lower_mileage = sorted(_recorded_mileages(price_order[:split]))
        upper_mileage = sorted(_recorded_mileages(price_order[split:]))
        overlap = min(
            float(np.quantile(lower_mileage, 0.9)),
            float(np.quantile(upper_mileage, 0.9)),
        ) - max(
            float(np.quantile(lower_mileage, 0.1)),
            float(np.quantile(upper_mileage, 0.1)),
        )
        if overlap >= minimum_mileage_span_km:
            raise InsufficientEvidence(
                "Multiple price populations require trim/configuration review"
            )


def _check_adjusted_populations(adjusted_logs: list[float]) -> None:
    """Reject a second substantial price population after mileage adjustment."""

    ordered = sorted(adjusted_logs)
    minimum_cluster = max(3, int(np.ceil(len(adjusted_logs) * 0.2)))
    if any(
        ordered[split] - ordered[split - 1] > PRICE_POPULATION_LOG_GAP
        for split in range(minimum_cluster, len(ordered) - minimum_cluster + 1)
    ):
        raise InsufficientEvidence(
            "Multiple price populations require trim/configuration review"
        )


def _outlier_weights(
    rows: list[Comparable],
    raw_weights: list[float],
    adjusted_logs: list[float],
    settings: ModelASettings,
) -> tuple[float, list[float]]:
    """Measure robust price dispersion and downweight or exclude unusual prices."""

    center = median(adjusted_logs)
    mad = median(abs(value - center) for value in adjusted_logs)
    dispersion = MAD_TO_NORMAL_SCALE * mad
    if len(rows) < MINIMUM_MAD_SAMPLE_SIZE or dispersion < DISPERSION_FLOOR:
        dispersion = settings.market_log_dispersion
        if dispersion is None:
            raise InsufficientEvidence(
                "Small or zero-MAD sample requires calibrated market dispersion"
            )
        if not isfinite(dispersion) or dispersion <= 0:
            raise ValueError("Market log dispersion must be positive and finite")

    scores = [abs(value - center) / dispersion for value in adjusted_logs]
    unusual_count = sum(score > OUTLIER_SCORE_THRESHOLD for score in scores)
    if unusual_count >= max(3, len(rows) * 0.2):
        raise InsufficientEvidence(
            "Multiple price populations require trim/configuration review"
        )

    factors = [
        1.0 if score <= OUTLIER_SCORE_THRESHOLD else OUTLIER_DOWNWEIGHT
        for score in scores
    ]
    exclusion_factors = [
        0.0 if score > OUTLIER_EXCLUSION_SCORE else factor
        for score, factor in zip(scores, factors, strict=True)
    ]
    try:
        weights = dealer_weights(
            rows,
            [
                raw * factor
                for raw, factor in zip(raw_weights, exclusion_factors, strict=True)
            ],
            settings.dealer_share_cap,
        )
        if effective_count(weights) + 1e-9 < settings.minimum_effective:
            raise InsufficientEvidence(
                "Insufficient effective comparables after exclusion"
            )
    except InsufficientEvidence:
        # Keep extreme observations at reduced weight when exclusion leaves too little
        # effective evidence to satisfy the configured sample threshold.
        weights = dealer_weights(
            rows,
            [raw * factor for raw, factor in zip(raw_weights, factors, strict=True)],
            settings.dealer_share_cap,
        )

    return dispersion, weights


def _confidence_level(
    effective: float,
    dispersion: float,
    weighted_age_days: float,
    local_share: float,
    beta_source: str,
) -> Literal["low", "medium", "high"]:
    """Apply the provisional sample, dispersion, age, and location confidence gates."""

    confidence: Literal["low", "medium", "high"] = "low"
    if (
        effective >= MEDIUM_CONFIDENCE_EFFECTIVE_COUNT
        and dispersion <= MEDIUM_CONFIDENCE_MAX_DISPERSION
        and weighted_age_days <= MEDIUM_CONFIDENCE_MAX_AGE_DAYS
    ):
        confidence = "medium"
    if (
        effective >= HIGH_CONFIDENCE_EFFECTIVE_COUNT
        and dispersion <= HIGH_CONFIDENCE_MAX_DISPERSION
        and weighted_age_days <= HIGH_CONFIDENCE_MAX_AGE_DAYS
        and local_share >= HIGH_CONFIDENCE_MIN_LOCAL_SHARE
        and beta_source == "huber"
    ):
        confidence = "high"
    return confidence


def _format_evaluation_response(
    rows: list[Comparable],
    query: ValuationQuery,
    as_of: date,
    *,
    target: float | None,
    mileage_source: MileageSource,
    beta_source: str,
    adjusted_logs: list[float],
    dispersion: float,
    weights: list[float],
) -> EvaluationResult:
    """Format calculated prices, comparable evidence, and confidence metadata."""

    prices = [exp(value) for value in adjusted_logs]
    low, price, high = _weighted_quantiles(prices, weights, [0.1, 0.5, 0.9])
    effective = effective_count(weights)
    age = sum(
        weight * (as_of - row.observed_on).days
        for row, weight in zip(rows, weights, strict=True)
    )
    query_region = (query.region or "").strip().casefold()
    local_share = sum(
        weight
        for row, weight in zip(rows, weights, strict=True)
        if query_region and (row.region or "").strip().casefold() == query_region
    )
    confidence = _confidence_level(effective, dispersion, age, local_share, beta_source)

    return {
        "currency": query.currency,
        "estimatedAskingPrice": _rounded_price(price),
        "marketRange": {"low": _rounded_price(low), "high": _rounded_price(high)},
        "referenceMileageKm": target,
        "requestedMileageKm": query.mileage_km,
        "mileageSource": mileage_source,
        "confidence": {
            "level": confidence,
            "independentVehicles": sum(weight > 0 for weight in weights),
        },
        "comparables": [
            {
                "observationId": row.observation_id,
                "vehicleId": row.vehicle_id,
                "listingPrice": row.price,
                "mileageKm": row.mileage_km,
                "location": {
                    "city": row.city,
                    "region": row.region,
                    "country": row.country,
                },
                "observedOn": row.observed_on.isoformat(),
                "weight": weight,
            }
            for row, weight in zip(rows, weights, strict=True)
        ],
    }


def evaluate(
    rows: list[Comparable], query: ValuationQuery, as_of: date, settings: ModelASettings
) -> EvaluationResult:
    """Value a prevalidated cohort using comparable mileage and calibrated fallbacks.

    Callers supply distinct vehicles with positive finite prices, nonnegative finite
    mileage (or None), and source dates no later than ``as_of``. Use ``valuate``
    for latest-snapshot selection and validation. All mileage values are kilometers.

    Listings with mileage use a weighted mileage adjustment; below the observed mileage
    range, raw asking prices are retained. Insufficient evidence raises
    :class:`InsufficientEvidence`; invalid calibration values raise :class:`ValueError`.
    """

    rows, target, mileage_source = _select_mileage(rows, query)
    if len(rows) < settings.minimum_effective:
        raise InsufficientEvidence(
            f"Fewer than {_count_label(settings.minimum_effective)} independent comparable vehicles"
        )

    mileages = _recorded_mileages(rows) if target is not None else []
    if target is not None:
        _check_price_populations(rows, settings.minimum_mileage_span_km)

    # Freshness and mileage proximity shape each row's initial influence before dealer
    # balancing constrains the total contribution from a single source.
    raw_weights = [
        exp(-(as_of - row.observed_on).days / settings.freshness_days)
        * (
            exp(-abs(mileages[index] - target) / settings.mileage_decay_km)
            if target is not None
            else 1
        )
        for index, row in enumerate(rows)
    ]
    balanced = dealer_weights(rows, raw_weights, settings.dealer_share_cap)
    if target is not None:
        beta, beta_source = fit_beta(rows, balanced, settings)
        adjusted_logs = [
            log(row.price) + beta * (target - mileage) / 1000
            for row, mileage in zip(rows, mileages, strict=True)
        ]
    else:
        beta_source = "unavailable"
        adjusted_logs = [log(row.price) for row in rows]

    if any(
        not isfinite(value) or value > 700 or value < -700 for value in adjusted_logs
    ):
        raise InsufficientEvidence("Mileage adjustment is numerically unstable")
    _check_adjusted_populations(adjusted_logs)

    dispersion, weights = _outlier_weights(rows, raw_weights, adjusted_logs, settings)
    if effective_count(weights) + 1e-9 < settings.minimum_effective:
        raise InsufficientEvidence(
            f"Fewer than {_count_label(settings.minimum_effective)} effective comparables"
        )

    return _format_evaluation_response(
        rows,
        query,
        as_of,
        target=target,
        mileage_source=mileage_source,
        beta_source=beta_source,
        adjusted_logs=adjusted_logs,
        dispersion=dispersion,
        weights=weights,
    )


def _count_label(count: float) -> str:
    """Format configured minimums while retaining familiar default error wording."""

    if count == 5:
        return "five"
    return f"{count:g}"


def valuate(
    rows: list[Comparable],
    query: ValuationQuery,
    as_of: date,
    settings: ModelASettings = _DEFAULT_SETTINGS,
) -> ValuationResult:
    """Select one eligible listing per vehicle and return the first supported estimate.

    Future observations and country mismatches are ineligible before latest-row
    selection. Price and mileage validity are checked after that selection, so an
    invalid newest eligible observation does not revive an older listing. Current
    windows try region/90 days, region/365 days, market/90 days, then market/365
    days (region windows are skipped when not requested). Historical search samples
    at most 24 dates, newest first, trying 90 then 365 days and region before market
    within each window. The first supported estimate wins.
    ``as_of`` and observation dates are calendar dates; mileage is in kilometers.
    Raises :class:`InsufficientEvidence` when no cohort meets the configured thresholds.
    """

    # Remove duplicate vehicles here too, since callers can use the valuation service
    # without going through the database query.
    latest: dict[int, Comparable] = {}

    for row in rows:
        if row.observed_on > as_of:
            continue

        if query.country and row.country != query.country:
            continue

        previous = latest.get(row.vehicle_id)
        if previous is None or (row.observed_on, row.observation_id) > (
            previous.observed_on,
            previous.observation_id,
        ):
            latest[row.vehicle_id] = row

    # Validate only the selected observation. Filtering here, rather than before the
    # per-vehicle selection, prevents stale but valid rows from replacing bad latest data.
    latest = {
        vehicle_id: row
        for vehicle_id, row in latest.items()
        if row.price > 0
        and isfinite(row.price)
        and (
            row.mileage_km is None or (row.mileage_km >= 0 and isfinite(row.mileage_km))
        )
    }
    query_region = (query.region or "").strip().casefold()

    failure = "No eligible comparable observations"

    # Give local listings up to a year of history before widening the search. A small
    # recent local sample should not hide usable older listings in the same region.
    current_scopes: list[Literal["region", "market"]] = (
        ["region", "market"] if query.region else ["market"]
    )

    for scope in current_scopes:
        for days in (90, 365):
            cohort = [
                row
                for row in latest.values()
                if (as_of - row.observed_on).days <= days
                and (
                    scope != "region"
                    or (row.region or "").strip().casefold() == query_region
                )
            ]

            try:
                result = evaluate(cohort, query, as_of, settings)
                return _finalize_result(
                    result, cohort, as_of, "current", settings, query, scope
                )
            except InsufficientEvidence as error:
                failure = str(error)

    # Measure historical windows from source listing dates so older groups get the same
    # relative freshness weighting as recent ones.
    historical_dates = sorted(
        {row.observed_on for row in latest.values()}, reverse=True
    )
    if len(historical_dates) > MAX_HISTORICAL_REFERENCE_DATES:
        # Include the newest and oldest dates, then spread the other checks across the
        # history instead of using the whole budget on nearby dates.
        last_index = len(historical_dates) - 1
        historical_dates = [
            historical_dates[index * last_index // (MAX_HISTORICAL_REFERENCE_DATES - 1)]
            for index in range(MAX_HISTORICAL_REFERENCE_DATES)
        ]

    for reference_date in historical_dates:
        eligible = [row for row in latest.values() if row.observed_on <= reference_date]

        for days in (90, 365):
            historical = [
                row
                for row in eligible
                if (reference_date - row.observed_on).days <= days
            ]
            historical_scopes: list[
                tuple[Literal["region", "market"], list[Comparable]]
            ] = [("market", historical)]
            if query.region:
                historical_scopes.insert(
                    0,
                    (
                        "region",
                        [
                            r
                            for r in historical
                            if (r.region or "").strip().casefold() == query_region
                        ],
                    ),
                )

            for scope, cohort in historical_scopes:
                try:
                    result = evaluate(cohort, query, reference_date, settings)
                    return _finalize_result(
                        result, cohort, as_of, "historical", settings, query, scope
                    )
                except InsufficientEvidence as error:
                    failure = str(error)

    raise InsufficientEvidence(failure)


def _finalize_result(
    result: EvaluationResult,
    cohort: list[Comparable],
    as_of: date,
    basis: Literal["current", "historical"],
    settings: ModelASettings,
    query: ValuationQuery,
    scope: Literal["region", "market"],
) -> ValuationResult:
    """Apply the time adjustment and attach the selected location scope."""

    if any(row.mileage_km is not None for row in cohort):
        cohort = [row for row in cohort if row.mileage_km is not None]
    timed_result = _add_time_provenance(
        result, cohort, as_of, basis, settings.projection
    )
    regional = scope == "region"
    if query.region and not regional:
        timed_result["confidence"]["level"] = "low"

    return {
        **timed_result,
        "locationSelection": {
            "country": query.country,
            "region": query.region,
            "scope": "region" if regional else "country" if query.country else "all",
            "fallback": bool(query.region and not regional),
        },
    }


def _rounded_price(value: float) -> int:
    """Round asking prices to the nearest hundred, with halfway values rounded up."""

    return int(floor(value / 100 + 0.5) * 100)


def _add_time_provenance(
    result: EvaluationResult,
    rows: list[Comparable],
    as_of: date,
    basis: Literal["current", "historical"],
    settings: ProjectionSettings,
) -> EvaluationResultWithTime:
    """Apply configured historical adjustments, retaining the original chart price."""

    valuation_basis: Literal["current", "historical", "projected"] = basis
    historical_reference: HistoricalReferenceResponse = {
        "estimatedAskingPrice": result["estimatedAskingPrice"],
    }
    if basis == "historical":
        result["confidence"]["level"] = "low"

    if basis == "historical" and settings.enabled:
        # Use the same positive-weight listings as the price to date the projection.
        weights = [entry["weight"] for entry in result["comparables"]]
        reference_date = weighted_quantile(
            [row.observed_on for row in rows], weights, 0.5
        )
        gap_years = max(0, (as_of - reference_date).days) / 365.25
        if _project_historical_price(result, gap_years, settings):
            valuation_basis = "projected"

    return {
        **result,
        "valuationBasis": valuation_basis,
        "historicalReference": historical_reference,
    }


def _project_historical_price(
    result: EvaluationResult, gap_years: float, settings: ProjectionSettings
) -> bool:
    """Apply a safe time projection only when all calibration settings are supplied."""

    depreciation_rate = settings.annual_depreciation_rate
    market_change_rate = settings.annual_market_change_rate
    uncertainty_rate = settings.annual_log_uncertainty
    if (
        not settings.enabled
        or gap_years <= 0
        or depreciation_rate is None
        or market_change_rate is None
        or uncertainty_rate is None
        or not (settings.source and settings.source.strip())
    ):
        return False

    try:
        age_factor = (1 - depreciation_rate) ** gap_years
        market_factor = (1 + market_change_rate) ** gap_years
        combined_factor = age_factor * market_factor
        projected_logs = [
            log(result["estimatedAskingPrice"]) + log(combined_factor),
            log(result["marketRange"]["low"])
            + log(combined_factor)
            - uncertainty_rate * gap_years,
            log(result["marketRange"]["high"])
            + log(combined_factor)
            + uncertainty_rate * gap_years,
        ]
        if not all(
            isfinite(value) and -700 < value < 700 for value in projected_logs
        ) or not all(
            isfinite(value) for value in (age_factor, market_factor, combined_factor)
        ):
            return False

        # Leave the historical result intact unless every projected value is safe.
        result["estimatedAskingPrice"] = _rounded_price(exp(projected_logs[0]))
        result["marketRange"] = {
            "low": _rounded_price(exp(projected_logs[1])),
            "high": _rounded_price(exp(projected_logs[2])),
        }
        return True
    except (OverflowError, ValueError):
        return False
