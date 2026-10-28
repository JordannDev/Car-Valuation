import type { JsonField, JsonObject } from '../../models/json.ts';
import { isIsoDate, isNonnegative, isNullablePositive, isPositive, isRecord } from './validation.ts';

// Listing, mileage, and location parsers

/** Validate the original listings used by the table and mileage chart. */
export function parseComparables(value: JsonField) {
  if (!Array.isArray(value)) throw new Error('Invalid comparable listings');

  return value.map((entry: JsonField) => {
    // Each listing must be an object before its fields can be checked.
    if (!isRecord(entry)) {
      throw new Error('Invalid comparable listing');
    }

    // Require stable identities for listing rows and chart deduplication.
    if (
      !Number.isSafeInteger(entry.observationId) ||
      !isPositive(entry.observationId) ||
      !Number.isSafeInteger(entry.vehicleId) ||
      !isPositive(entry.vehicleId)
    ) {
      throw new Error('Invalid comparable listing');
    }

    // Validate the price, nullable mileage, source date, weight, and location.
    if (
      !isPositive(entry.listingPrice) ||
      !(entry.mileageKm === null || isNonnegative(entry.mileageKm)) ||
      !isIsoDate(entry.observedOn) ||
      !isNonnegative(entry.weight) ||
      !isRecord(entry.location)
    ) {
      throw new Error('Invalid comparable listing');
    }

    const { city, region, country } = entry.location;
    if (
      !(city === null || typeof city === 'string') ||
      !(region === null || typeof region === 'string') ||
      !(country === null || typeof country === 'string')
    )
      throw new Error('Invalid listing location');

    return {
      observationId: entry.observationId,
      vehicleId: entry.vehicleId,
      listingPrice: entry.listingPrice,
      mileageKm: entry.mileageKm,
      observedOn: entry.observedOn,
      weight: entry.weight,
      location: { city, region, country },
    };
  });
}

/** Validate recorded totals separately from the comparable price range. */
export function parseListingSummary(value: JsonField) {
  if (!isRecord(value)) {
    throw new Error('Invalid listing summary');
  }

  // Check the listing count and optional source date independently of the price range.
  if (
    !Number.isSafeInteger(value.totalListings) ||
    !isNonnegative(value.totalListings) ||
    !(value.latestListingDate === null || isIsoDate(value.latestListingDate))
  ) {
    throw new Error('Invalid listing summary');
  }

  // Recorded price limits must be positive when supplied.
  if (
    !isNullablePositive(value.lowestRecordedPrice) ||
    !isNullablePositive(value.highestRecordedPrice)
  ) {
    throw new Error('Invalid listing summary');
  }

  // Require both limits together, then check their order.
  if ((value.lowestRecordedPrice === null) !== (value.highestRecordedPrice === null)) {
    throw new Error('Invalid listing summary');
  }

  if (
    value.lowestRecordedPrice !== null &&
    value.highestRecordedPrice !== null &&
    value.lowestRecordedPrice > value.highestRecordedPrice
  ) {
    throw new Error('Invalid listing summary');
  }

  return {
    totalListings: value.totalListings,
    latestListingDate: value.latestListingDate,
    lowestRecordedPrice: value.lowestRecordedPrice,
    highestRecordedPrice: value.highestRecordedPrice,
  };
}

/** Check how the requested mileage relates to the distance used for pricing. */
export function parseMileage(value: JsonObject) {
  const source = value.mileageSource;
  if (
    source !== 'provided' &&
    source !== 'comparable_median' &&
    source !== 'rounded_down' &&
    source !== 'unavailable'
  ) {
    throw new Error('Invalid valuation mileage source');
  }

  if (!(value.requestedMileageKm === null || isNonnegative(value.requestedMileageKm))) {
    throw new Error('Invalid requested mileage');
  }

  if (source === 'unavailable') {
    if (value.referenceMileageKm !== null) throw new Error('Invalid unavailable mileage');
  } else {
    if (!isNonnegative(value.referenceMileageKm)) throw new Error('Invalid reference mileage');

    switch (source) {
      case 'provided':
        if (value.referenceMileageKm !== value.requestedMileageKm) {
          throw new Error('Provided mileage must match requested mileage');
        }
        break;

      case 'comparable_median':
        if (value.requestedMileageKm !== null) {
          throw new Error('Comparable median requires no requested mileage');
        }
        break;

      case 'rounded_down':
        if (
          value.requestedMileageKm === null ||
          value.referenceMileageKm >= value.requestedMileageKm
        ) {
          throw new Error('Rounded mileage must be below requested mileage');
        }
        break;
    }
  }

  return {
    mileageSource: source,
    referenceMileageKm: value.referenceMileageKm,
    requestedMileageKm: value.requestedMileageKm,
  };
}

/** Check the selected location and whether pricing fell back beyond the requested region. */
export function parseLocationSelection(value: JsonField) {
  if (!isRecord(value)) throw new Error('Invalid location selection');

  const { country, region, scope, fallback } = value;

  if (
    country !== null &&
    (typeof country !== 'string' || !/^[A-Z]{2}$/.test(country))
  ) {
    throw new Error('Invalid location selection');
  }

  if (
    region !== null &&
    (typeof region !== 'string' || region.trim().length === 0 || region.length > 80)
  ) {
    throw new Error('Invalid location selection');
  }

  if (scope !== 'region' && scope !== 'country' && scope !== 'all') {
    throw new Error('Invalid location selection');
  }

  // Each geographic scope requires its corresponding location value.
  if (scope === 'region' && region === null) {
    throw new Error('Invalid location selection');
  }

  if (scope === 'country' && country === null) {
    throw new Error('Invalid location selection');
  }

  // Fallback means pricing searched beyond the requested region.
  const searchedBeyondRegion = region !== null && scope !== 'region';
  if (fallback !== searchedBeyondRegion) {
    throw new Error('Invalid location selection');
  }

  return { country, region, scope, fallback };
}
