import type { JsonField } from '../../models/json.ts';
import { isNonnegative, isRecord } from './validation.ts';

/** Keep the unprojected price used by the listing chart. */
export function parseHistoricalReference(value: JsonField) {
  if (!isRecord(value) || !isNonnegative(value.estimatedAskingPrice)) {
    throw new Error('Invalid historical valuation reference');
  }

  return { estimatedAskingPrice: value.estimatedAskingPrice };
}
