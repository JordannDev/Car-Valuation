import { fromMiles, toMiles, type MileageUnit } from './mileage.ts';
import type { ModelAValuation } from '../services/model-a.ts';

export interface PriceMileagePoint {
  vehicleId: number;
  observationId: number;
  observedOn: string;
  listingPrice: number;
  mileage: number;
}

/** Choose the latest usable listing for each vehicle with a known ID. */
export function getPriceMileagePoints(
  comparables: ModelAValuation['comparables'],
  unit: MileageUnit,
): PriceMileagePoint[] {
  const latestByVehicle = new Map<number, PriceMileagePoint>();

  for (const comparable of comparables) {
    // Skip excluded listings and missing mileage rather than inventing chart points.
    if (
      comparable.weight <= 0 ||
      comparable.listingPrice <= 0 ||
      !Number.isFinite(comparable.listingPrice) ||
      comparable.mileageKm === null ||
      !Number.isFinite(comparable.mileageKm) ||
      comparable.mileageKm < 0
    )
      continue;

    const point: PriceMileagePoint = {
      vehicleId: comparable.vehicleId,
      observationId: comparable.observationId,
      observedOn: comparable.observedOn,
      listingPrice: comparable.listingPrice,
      mileage: fromMiles(toMiles(comparable.mileageKm, 'kilometers'), unit),
    };

    // Keep one observation per vehicle, using its ID to break ties on the same source date.
    const existing = latestByVehicle.get(point.vehicleId);
    if (
      !existing ||
      point.observedOn > existing.observedOn ||
      (point.observedOn === existing.observedOn && point.observationId > existing.observationId)
    ) {
      latestByVehicle.set(point.vehicleId, point);
    }
  }

  const points = [...latestByVehicle.values()].sort(
    (a, b) => a.mileage - b.mileage || a.vehicleId - b.vehicleId,
  );
  const distinctMileage = new Set(points.map((point) => point.mileage));

  // A chart needs at least five vehicles and two mileages to show a useful comparison.
  return points.length >= 5 && distinctMileage.size >= 2 ? points : [];
}

export interface AxisScale {
  minimum: number;
  maximum: number;
  step: number;
  ticks: number[];
}

export interface PriceMileageBand {
  index: number;
  start: number;
  end: number;
  count: number;
  median: number;
  lower: number;
  upper: number;
}

/** Group listings into equal mileage bands and calculate their price quartiles without weights. */
export function getPriceMileageBands(points: readonly PriceMileagePoint[]): PriceMileageBand[] {
  if (points.length === 0) return [];

  const minimum = Math.min(...points.map((point) => point.mileage));
  const maximum = Math.max(...points.map((point) => point.mileage));
  if (minimum === maximum) return [];

  // Use more bands for larger samples, while keeping the chart between two and ten bands.
  const count = Math.max(2, Math.min(10, Math.floor(Math.sqrt(points.length))));
  const step = (maximum - minimum) / count;
  const buckets = Array.from({ length: count }, () => [] as number[]);

  for (const point of points) {
    const index = Math.min(count - 1, Math.floor((point.mileage - minimum) / step));
    buckets[index]!.push(point.listingPrice);
  }

  return buckets.flatMap((prices, index) => {
    if (prices.length === 0) return [];

    prices.sort((a, b) => a - b);

    // Interpolate between sorted prices to calculate the median and middle 50% of each band.
    const quantile = (fraction: number) => {
      const position = (prices.length - 1) * fraction;
      const lower = Math.floor(position);

      return prices[lower]! + (prices[Math.ceil(position)]! - prices[lower]!) * (position - lower);
    };

    return [
      {
        index,
        start: minimum + index * step,
        end: minimum + (index + 1) * step,
        count: prices.length,
        median: quantile(0.5),
        lower: quantile(0.25),
        upper: quantile(0.75),
      },
    ];
  });
}

/** Fit the axis to the data and round the tick spacing without adding empty space at either end. */
export function createAxisScale(
  values: readonly number[],
  desiredTickCount = 5,
  minimumFloor = Number.NEGATIVE_INFINITY,
  paddingFraction = 0.05,
): AxisScale {
  const rawMinimum = Math.min(...values);
  const rawMaximum = Math.max(...values);

  // Expand a single-valued axis into a usable range; otherwise add proportional padding only.
  const fallbackSpan = Math.max(Math.abs(rawMinimum) * 0.1, 1);
  const span = rawMaximum === rawMinimum ? fallbackSpan : rawMaximum - rawMinimum;
  const paddedMinimum =
    rawMaximum === rawMinimum ? rawMinimum - span / 2 : rawMinimum - span * paddingFraction;
  const paddedMaximum =
    rawMaximum === rawMinimum ? rawMaximum + span / 2 : rawMaximum + span * paddingFraction;
  const rawStep = (paddedMaximum - paddedMinimum) / Math.max(1, desiredTickCount - 1);

  // Choose a readable tick interval without rounding the axis outward into empty space.
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const normalizedStep = rawStep / magnitude;
  const multiplier =
    normalizedStep <= 1
      ? 1
      : normalizedStep <= 2
        ? 2
        : normalizedStep <= 2.5
          ? 2.5
          : normalizedStep <= 5
            ? 5
            : 10;
  const step = multiplier * magnitude;
  const minimum = Math.max(minimumFloor, paddedMinimum);
  const maximum = paddedMaximum;
  const ticks: number[] = [];
  const firstTick = Math.ceil(minimum / step) * step;

  for (let tick = firstTick; tick <= maximum + step * 1e-9; tick += step)
    ticks.push(Number(tick.toPrecision(12)));

  return { minimum, maximum, step, ticks };
}
