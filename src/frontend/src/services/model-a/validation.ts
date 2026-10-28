import type { JsonField, JsonObject } from '../../models/json.ts';

export function isRecord(value: JsonField): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function isNonnegative(value: JsonField): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}

export function isPositive(value: JsonField): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0;
}

export function isNullablePositive(value: JsonField): value is number | null {
  return value === null || isPositive(value);
}

/** Accept real calendar dates in YYYY-MM-DD form, rejecting dates that JavaScript would roll over. */
export function isIsoDate(value: JsonField): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;

  const date = new Date(`${value}T00:00:00Z`);

  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

/** Require a finite, nonnegative range that contains the estimated price. */
export function isPriceRange(value: JsonField, estimate: number): value is { low: number; high: number } {
  return (
    isRecord(value) &&
    isNonnegative(value.low) &&
    isNonnegative(value.high) &&
    value.low <= estimate &&
    value.high >= estimate
  );
}
