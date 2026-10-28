import type {JsonField} from '../models/json.ts';
import assert from 'node:assert/strict';
import test from 'node:test';
import {fetchModelA} from '../services/model-a.ts';

const base = {
  estimatedAskingPrice: 20_000, currency: 'USD',
  referenceMileageKm: 100_000, requestedMileageKm: 100_000, mileageSource: 'provided',
  marketRange: {low: 18_000, high: 22_000},
  confidence: {level: 'medium', independentVehicles: 24},
  valuationBasis: 'current',
  historicalReference: {estimatedAskingPrice: 20_000},
  locationSelection: {country: null, region: null, scope: 'all', fallback: false},
  listingSummary: {totalListings: 125, latestListingDate: '2026-10-27', lowestRecordedPrice: 5_000, highestRecordedPrice: 40_000},
  comparables: [{
    observationId: 1, vehicleId: 42, listingPrice: 21_500, mileageKm: 80_467.2,
    observedOn: '2026-10-27', weight: 1, location: {city: 'Detroit', region: 'MI', country: 'US'},
  }],
};
const vehicle = {year: 2020, make: 'Toyota', model: 'Camry'};

async function parse(payload: JsonField) {
  const result = await fetchModelA(vehicle, new AbortController().signal, async () => Response.json(payload));
  assert.equal(result.status, 'success');
  if (result.status !== 'success') throw new Error('Expected successful valuation');
  return result.valuation;
}

test('keeps recorded listing totals and prices separate from valuation comparables', async () => {
  const listingSummary = {totalListings: 125, latestListingDate: '2026-10-27', lowestRecordedPrice: 5_000, highestRecordedPrice: 40_000};
  assert.deepEqual((await parse({...base, listingSummary})).listingSummary, listingSummary);
  await assert.rejects(parse({...base, listingSummary: undefined}));
  const empty = {totalListings: 0, latestListingDate: null, lowestRecordedPrice: null, highestRecordedPrice: null};
  assert.deepEqual((await parse({...base, listingSummary: empty})).listingSummary, empty);
  for (const invalid of [
    {...listingSummary, totalListings: -1}, {...listingSummary, totalListings: 1.5},
    {...listingSummary, latestListingDate: '2026-02-30'},
    {...listingSummary, lowestRecordedPrice: 50_000},
    {...listingSummary, highestRecordedPrice: null},
  ]) await assert.rejects(parse({...base, listingSummary: invalid}));
});

test('sends exact YMM, explicit currency and mileage units, and forwards cancellation', async () => {
  const controller = new AbortController();
  const result = await fetchModelA({...vehicle, model: 'Camry & Hybrid', mileageMiles: 0}, controller.signal, async (input, init) => {
    const url = new URL(String(input), 'http://localhost');
    assert.equal(url.pathname, '/api/valuations/model-a');
    assert.deepEqual(Object.fromEntries(url.searchParams), {
      year: '2020', make: 'Toyota', model: 'Camry & Hybrid', currency: 'USD', mileage: '0', mileageUnit: 'MI',
    });
    assert.equal(init?.signal, controller.signal);
    assert.equal(init?.cache, 'no-store');
    return Response.json(base);
  });
  assert.equal(result.status, 'success');
  if (result.status === 'success') assert.equal(result.valuation.estimatedAskingPrice, 20_000);
});

test('omits mileage and unit when not provided', async () => {
  await fetchModelA(vehicle, new AbortController().signal, async (input) => {
    const params = new URL(String(input), 'http://localhost').searchParams;
    assert.equal(params.has('mileage'), false);
    assert.equal(params.has('mileageUnit'), false);
    return Response.json({...base, mileageSource: 'comparable_median', requestedMileageKm: null});
  });
});

test('sends selected location and preserves regional fallback provenance', async () => {
  const locationSelection = {country: 'US', region: 'FL', scope: 'country', fallback: true};
  const result = await fetchModelA({...vehicle, country: 'US', region: 'FL'}, new AbortController().signal, async (input) => {
    const params = new URL(String(input), 'http://localhost').searchParams;
    assert.equal(params.get('country'), 'US');
    assert.equal(params.get('region'), 'FL');
    return Response.json({...base, confidence: {...base.confidence, level: 'low'}, locationSelection});
  });
  assert.equal(result.status, 'success');
  if (result.status === 'success') assert.deepEqual(result.valuation.locationSelection, locationSelection);
  const regionOnly = await parse({...base, locationSelection: {country: null, region: 'FL', scope: 'region', fallback: false}});
  assert.equal(regionOnly.locationSelection?.country, null);
  for (const invalid of [
    {...locationSelection, fallback: false}, {...locationSelection, scope: 'nearby'},
    {...locationSelection, country: 'USA'}, {...locationSelection, region: null},
  ]) await assert.rejects(parse({...base, locationSelection: invalid}));
});


test('parses mileage fallback states and keeps missing mileage null', async () => {
  const rounded = await parse({...base, mileageSource: 'rounded_down', referenceMileageKm: 180_000,
    requestedMileageKm: 200_000});
  assert.equal(rounded.referenceMileageKm, 180_000);
  assert.equal(rounded.requestedMileageKm, 200_000);
  const unavailable = await parse({...base, mileageSource: 'unavailable', referenceMileageKm: null,
    requestedMileageKm: 200_000});
  assert.equal(unavailable.referenceMileageKm, null);
  const belowRange = await parse({...base, mileageSource: 'unavailable', referenceMileageKm: null,
    requestedMileageKm: 20_000});
  assert.equal(belowRange.mileageSource, 'unavailable');
  const provided = await parse(base);
  assert.equal(provided.requestedMileageKm, base.referenceMileageKm);
  const median = await parse({...base, mileageSource: 'comparable_median', requestedMileageKm: null});
  assert.equal(median.requestedMileageKm, null);
});

test('rejects inconsistent mileage fallback combinations', async () => {
  const invalid = [
    {...base, mileageSource: 'unavailable', referenceMileageKm: 0, requestedMileageKm: 100_000},
    {...base, mileageSource: 'rounded_down', referenceMileageKm: 100_001, requestedMileageKm: 100_000},
    {...base, mileageSource: 'rounded_down', referenceMileageKm: 100_000, requestedMileageKm: 100_000},
    {...base, mileageSource: 'provided', referenceMileageKm: 100_000, requestedMileageKm: 99_000},
    {...base, mileageSource: 'comparable_median', referenceMileageKm: 100_000, requestedMileageKm: 99_000},
    {...base, requestedMileageKm: undefined},
    {...base, mileageSource: 'unknown', requestedMileageKm: 100_000},
    {...base, mileageSource: 'rounded_down', referenceMileageKm: 100_000, requestedMileageKm: -1},
  ];
  for (const payload of invalid) {
    await assert.rejects(fetchModelA(vehicle, new AbortController().signal, async () => Response.json(payload)));
  }
  const nonFinite = {...base, mileageSource: 'rounded_down', referenceMileageKm: 1, requestedMileageKm: Number.NaN};
  await assert.rejects(fetchModelA(vehicle, new AbortController().signal, async () => ({
    status: 200, json: async () => nonFinite,
  } as Response)));
});

test('parses current, historical passthrough, and projected chart prices', async () => {
  assert.equal((await parse(base)).valuationBasis, 'current');
  const historical = await parse({...base, valuationBasis: 'historical', confidence: {...base.confidence, level: 'low'}});
  assert.equal(historical.estimatedAskingPrice, historical.historicalReference.estimatedAskingPrice);
  const projected = await parse({
    ...base, estimatedAskingPrice: 17_600, marketRange: {low: 14_000, high: 22_000},
    valuationBasis: 'projected', confidence: {...base.confidence, level: 'low'},
  });
  assert.equal(projected.valuationBasis, 'projected');
  assert.equal(projected.historicalReference.estimatedAskingPrice, 20_000);
});

test('rejects malformed historical prices and inconsistent valuation bases', async () => {
  for (const payload of [
    {...base, valuationBasis: 'unknown'},
    {...base, valuationBasis: 'projected'},
    {...base, valuationBasis: 'historical'},
    {...base, historicalReference: undefined},
    {...base, historicalReference: {estimatedAskingPrice: -1}},
    {...base, historicalReference: {estimatedAskingPrice: 30_000}},
  ]) await assert.rejects(parse(payload));
});

test('insufficient evidence is distinct from unavailable service and malformed responses', async () => {
  assert.deepEqual(await fetchModelA(vehicle, new AbortController().signal,
    async () => Response.json({error: 'Insufficient evidence'}, {status: 422})), {status: 'insufficient', heading: 'Not enough evidence to estimate', reason: 'We could not find enough reliable comparable listings to estimate this vehicle’s asking price.'});
  for (const status of [400, 500, 503]) {
    await assert.rejects(fetchModelA(vehicle, new AbortController().signal,
      async () => Response.json({}, {status})));
  }
  for (const malformed of [{}, {...base, estimatedAskingPrice: '20000'}, {...base, marketRange: {low: 30_000, high: 40_000}}]) {
    await assert.rejects(fetchModelA(vehicle, new AbortController().signal, async () => Response.json(malformed)));
  }
  await assert.rejects(fetchModelA(vehicle, new AbortController().signal, async () => {throw new TypeError('Offline');}));
});

test('explains missing source metadata and statistical evidence separately', async () => {
  for (const [reasonCode, expected] of [
    ['source_metadata_missing', 'Listing data needs verification'],
    ['insufficient_comparables', 'Not enough evidence to estimate'],
  ]) {
    const result = await fetchModelA(vehicle, new AbortController().signal, async () =>
      Response.json({error: 'Insufficient evidence', reasonCode, reason: 'Target mileage is outside the comparable mileage range'}, {status: 422}));
    assert.equal(result.status, 'insufficient');
    if (result.status === 'insufficient') {
      assert.equal(result.heading, expected);
      if (reasonCode === 'insufficient_comparables') assert.match(result.reason, /Target mileage/);
    }
  }
});


test('preserves original listing prices, source dates, and nullable mileage and locations', async () => {
  const valuation = await parse({...base, comparables: [
    ...base.comparables,
    {observationId: 2, vehicleId: 43, listingPrice: 19_000, mileageKm: null, observedOn: '2026-10-25',
      weight: 0, location: {city: null, region: null, country: null}},
  ]});
  assert.equal(valuation.comparables[0]!.listingPrice, 21_500);
  assert.equal(valuation.comparables[0]!.mileageKm, 80_467.2);
  assert.equal(valuation.comparables[0]!.observedOn, '2026-10-27');
  assert.deepEqual(valuation.comparables[0]!.location, {city: 'Detroit', region: 'MI', country: 'US'});
  assert.equal(valuation.comparables[1]!.location.city, null);
  assert.equal(valuation.comparables[1]!.mileageKm, null);

  for (const comparable of [
    {...base.comparables[0], listingPrice: undefined},
    {...base.comparables[0], mileageKm: -1},
    {...base.comparables[0], observedOn: '2026-02-30'},
    {...base.comparables[0], location: {city: 123, region: 'MI', country: 'US'}},
  ]) {
    await assert.rejects(fetchModelA(vehicle, new AbortController().signal, async () =>
      Response.json({...base, comparables: [comparable]})));
  }
});

test('requires vehicle identity for listing chart deduplication', async () => {
  const current = await parse(base);
  assert.equal(current.comparables[0]!.vehicleId, 42);

  for (const vehicleId of [undefined, 0, -1, 1.5, '42', null]) {
    await assert.rejects(parse({...base, comparables: [{...base.comparables[0], vehicleId}]}));
  }
});
