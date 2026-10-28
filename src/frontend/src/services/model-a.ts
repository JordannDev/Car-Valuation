/**
 * Model A estimates a vehicle's asking price from listings with the same year, make,
 * model, and currency. It adjusts for mileage when supported by the data, gives more
 * weight to recent and similar-mileage listings, limits any one dealer's influence,
 * and reduces the impact of unusual prices. The estimate is the weighted middle
 * price, with a range showing the spread of comparable asking prices.
 *
 * It can broaden the location search or use older listings when evidence is limited.
 * Any projection from older prices uses explicitly configured rates. The response
 * explains these fallbacks and reports insufficient evidence when needed.
 *
 * This client requests the estimate and validates the response; the API calculates it.
 */
import type { JsonField } from '../models/json.ts';
import { parseHistoricalReference } from './model-a/history.ts';
import { parseComparables, parseListingSummary, parseLocationSelection, parseMileage } from './model-a/listings.ts';
import { isNonnegative, isPriceRange, isRecord } from './model-a/validation.ts';

// Types and interfaces

type ValuationBasis = 'current' | 'historical' | 'projected';
type ConfidenceLevel = 'low' | 'medium' | 'high';

/** Fetch asking-price estimates from the API, sending mileage in miles. */
export interface ModelARequest {
  year: number;
  make: string;
  model: string;
  mileageMiles?: number;
  country?: string;
  region?: string;
}

export type ModelAValuation = ReturnType<typeof parseValuation>;
export type ModelAResult =
  | { status: 'success'; valuation: ModelAValuation }
  | { status: 'insufficient'; heading: string; reason: string };

// Valuation response validation

function isValuationBasis(value: JsonField): value is ValuationBasis {
  return value === 'current' || value === 'historical' || value === 'projected';
}

function isConfidenceLevel(value: JsonField): value is ConfidenceLevel {
  return value === 'low' || value === 'medium' || value === 'high';
}

/** Validate the API response and check that its prices, confidence, and historical chart price agree. */
function parseValuation(value: JsonField) {
  // Reject non-object payloads before reading the estimate.
  if (!isRecord(value)) {
    throw new Error('Invalid valuation response');
  }

  // Validate the price, currency, and range shown at the top of the result.
  if (
    !isNonnegative(value.estimatedAskingPrice) ||
    typeof value.currency !== 'string' ||
    !/^[A-Z]{3}$/.test(value.currency) ||
    !isPriceRange(value.marketRange, value.estimatedAskingPrice)
  ) {
    throw new Error('Invalid valuation response');
  }

  // Validate the confidence and time basis shown in the results.
  if (
    !isRecord(value.confidence) ||
    !isNonnegative(value.confidence.independentVehicles) ||
    !isConfidenceLevel(value.confidence.level) ||
    !isValuationBasis(value.valuationBasis)
  ) {
    throw new Error('Invalid valuation response');
  }

  // Historical and projected estimates must keep their low-confidence rating.
  if (value.valuationBasis !== 'current' && value.confidence.level !== 'low') {
    throw new Error('Inconsistent valuation response');
  }

  const historicalReference = parseHistoricalReference(value.historicalReference);
  if (
    value.valuationBasis !== 'projected' &&
    historicalReference.estimatedAskingPrice !== value.estimatedAskingPrice
  ) {
    throw new Error('Inconsistent valuation response');
  }

  return {
    estimatedAskingPrice: value.estimatedAskingPrice,
    currency: value.currency,
    ...parseMileage(value),
    locationSelection: parseLocationSelection(value.locationSelection),
    marketRange: { low: value.marketRange.low, high: value.marketRange.high },
    confidence: {
      level: value.confidence.level,
      independentVehicles: value.confidence.independentVehicles,
    },
    valuationBasis: value.valuationBasis,
    historicalReference,
    comparables: parseComparables(value.comparables),
    listingSummary: parseListingSummary(value.listingSummary),
  };
}

// API client

/** Fetch a fresh estimate each time. If pricing fails, show the failure instead of demo data. */
export async function fetchModelA(
  vehicle: ModelARequest,
  signal: AbortSignal,
  fetcher: typeof fetch = fetch,
): Promise<ModelAResult> {
  const parameters = new URLSearchParams({
    year: String(vehicle.year),
    make: vehicle.make,
    model: vehicle.model,
    currency: 'USD',
  });
  if (vehicle.country) parameters.set('country', vehicle.country);

  if (vehicle.region) parameters.set('region', vehicle.region);

  if (vehicle.mileageMiles !== undefined) {
    parameters.set('mileage', String(vehicle.mileageMiles));
    parameters.set('mileageUnit', 'MI');
  }

  const response = await fetcher(`/api/valuations/model-a?${parameters}`, {
    signal,
    cache: 'no-store',
  });

  // An evidence failure is a valid API outcome. Keep it separate from service failures and
  // malformed responses.
  if (response.status === 422) {
    const body: JsonField = await response.json();
    const code = isRecord(body) ? body.reasonCode : undefined;
    const reason = isRecord(body) && typeof body.reason === 'string' ? body.reason : undefined;
    if (code === 'source_metadata_missing') {
      return {
        status: 'insufficient',
        heading: 'Listing data needs verification',
        reason:
          'Matching listings exist, but their snapshot dates have not been confirmed. Source prices are treated as USD and mileage as miles; a dated market snapshot is still needed to estimate a price.',
      };
    }

    return {
      status: 'insufficient',
      heading: 'Not enough listings to estimate.',
      reason:
        reason ??
        'We could not find enough reliable comparable listings to estimate this vehicle’s asking price.',
    };
  }

  if (!response.ok) throw new Error('Valuation request failed');

  const body: JsonField = await response.json();

  return { status: 'success', valuation: parseValuation(body) };
}
