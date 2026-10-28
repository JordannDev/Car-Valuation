import assert from 'node:assert/strict';
import test from 'node:test';
import type {ModelAValuation} from '../services/model-a.ts';
import {createAxisScale, getPriceMileageBands, getPriceMileagePoints, type PriceMileagePoint} from '../utils/price-mileage.ts';

type Comparable = ModelAValuation['comparables'][number];

function comparable(observationId: number, vehicleId: number, mileageKm: number | null, options: {
  observedOn?: string;
  listingPrice?: number;
  weight?: number;
} = {}): Comparable {
  return {
    observationId,
    vehicleId,
    listingPrice: options.listingPrice ?? 20_000 + observationId,
    mileageKm,
    observedOn: options.observedOn ?? '2026-10-27',
    weight: options.weight ?? 1,
    location: {city: null, region: null, country: null},
  };
}

test('keeps latest eligible observation per known vehicle and excludes ineligible rows', () => {
  const rows = [
    comparable(1, 10, 20_000, {observedOn: '2026-10-25'}),
    comparable(2, 10, 30_000, {observedOn: '2026-10-27'}),
    comparable(3, 10, 25_000, {observedOn: '2026-10-27'}),
    comparable(4, 11, 40_000),
    comparable(5, 12, 50_000),
    comparable(6, 13, 60_000),
    comparable(7, 14, 70_000),
    comparable(9, 15, 90_000, {weight: 0}),
    comparable(10, 16, null),
  ];

  const points = getPriceMileagePoints(rows, 'kilometers');
  assert.deepEqual(points.map(point => point.vehicleId), [10, 11, 12, 13, 14]);
  assert.equal(points[0]!.observationId, 3, 'ties on date use the larger observation id');
  assert.equal(points[0]!.listingPrice, 20_003, 'the plot uses the original asking price');
});

test('omits chart data below five distinct known vehicles or two mileage values', () => {
  const fourVehicles = [1, 2, 3, 4].map(id => comparable(id, id, id * 1_000));
  assert.deepEqual(getPriceMileagePoints(fourVehicles, 'miles'), []);

  const sameMileage = [1, 2, 3, 4, 5].map(id => comparable(id, id, 10_000));
  assert.deepEqual(getPriceMileagePoints(sameMileage, 'miles'), []);
});

test('converts chart mileage to the selected unit', () => {
  const rows = [1, 2, 3, 4, 5].map(id => comparable(id, id, id * 1_609.344));
  const kilometers = getPriceMileagePoints(rows, 'kilometers');
  const miles = getPriceMileagePoints(rows, 'miles');
  assert.equal(kilometers[0]!.mileage, 1_609.344);
  assert.equal(miles[0]!.mileage, 1_000);
});

test('expands equal-value axes to a nonzero range with readable ticks', () => {
  for (const value of [0, 20_000]) {
    const scale = createAxisScale([value, value]);
    assert.ok(scale.maximum > scale.minimum);
    assert.ok(scale.ticks.length >= 2);
    assert.ok(scale.ticks.every(Number.isFinite));
  }

  for (const values of [[0, 0], [0, 1]]) {
    const nonnegativeScale = createAxisScale(values, 5, 0);
    assert.equal(nonnegativeScale.minimum, 0);
    assert.ok(nonnegativeScale.maximum > 0);
    assert.ok(nonnegativeScale.ticks.every(tick => tick >= 0));
  }

  for (const values of [[1, 1], [20_000, 20_000]]) {
    assert.ok(createAxisScale(values, 5, 0).minimum >= 0);
  }
});

function chartPoint(mileage: number, listingPrice: number, vehicleId: number): PriceMileagePoint {
  return {mileage, listingPrice, vehicleId, observationId: vehicleId, observedOn: '2026-10-27'};
}

test('fits mileage bounds to the data without rounding out to empty tick intervals', () => {
  const scale = createAxisScale([12_345, 147_781, 428_086], 6, 0, 0);
  assert.equal(scale.minimum, 12_345);
  assert.equal(scale.maximum, 428_086);
  assert.ok(scale.ticks.every(tick => tick >= scale.minimum && tick <= scale.maximum));
  assert.ok(scale.ticks.includes(400_000));
  assert.ok(!scale.ticks.includes(500_000));
});

test('adds only proportional price padding while retaining readable tick spacing', () => {
  const scale = createAxisScale([7_000, 16_200, 21_000], 5, 0);
  assert.equal(scale.minimum, 6_300);
  assert.equal(scale.maximum, 21_700);
  assert.ok(scale.ticks.every(tick => tick >= scale.minimum && tick <= scale.maximum));
});

test('summarizes recorded prices with median and interpolated quartiles, including the last mileage', () => {
  const points = [
    chartPoint(0, 10_000, 1), chartPoint(10, 20_000, 2),
    chartPoint(20, 30_000, 3), chartPoint(30, 100_000, 4),
    chartPoint(100, 5_000, 5),
  ];
  const bands = getPriceMileageBands(points);
  assert.deepEqual(bands, [
    {index: 0, start: 0, end: 50, count: 4, median: 25_000, lower: 17_500, upper: 47_500},
    {index: 1, start: 50, end: 100, count: 1, median: 5_000, lower: 5_000, upper: 5_000},
  ]);
  assert.equal(bands.reduce((sum, band) => sum + band.count, 0), points.length);
  assert.equal(points[3]!.listingPrice, 100_000);
});

test('retains gaps between populated mileage bands rather than inventing prices', () => {
  const points = Array.from({length: 16}, (_, index) => chartPoint(index < 8 ? 0 : 100, 20_000, index));
  assert.deepEqual(getPriceMileageBands(points).map(band => band.index), [0, 3]);
  assert.deepEqual(getPriceMileageBands([]), []);
  assert.deepEqual(getPriceMileageBands(points.slice(0, 8)), []);
});
