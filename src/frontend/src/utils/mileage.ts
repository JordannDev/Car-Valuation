export type MileageUnit = 'miles' | 'kilometers';

const KILOMETERS_PER_MILE = 1.609344;
const mileageDigitsAndCommas = /^[\d,]+$/;
const wholeNumberFormatter = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });

/** Read nonnegative whole mileage, returning undefined for blank input and null for invalid input. */
export function parseMileage(value: string): number | undefined | null {
  const normalized = value.trim();
  if (normalized === '') return undefined;

  // Accept plain digits or correctly grouped thousands; malformed commas must remain invalid.
  if (!/^(?:\d+|\d{1,3}(?:,\d{3})+)$/.test(normalized)) return null;

  const mileage = Number(normalized.replaceAll(',', ''));

  return Number.isSafeInteger(mileage) && mileage >= 0 ? mileage : null;
}

/** Add commas for readability while leaving invalid or unsafe input unchanged. */
export function formatMileageInput(value: string): string {
  if (!mileageDigitsAndCommas.test(value) || !/\d/.test(value)) return value;

  const digits = value.replaceAll(',', '');
  if (!Number.isSafeInteger(Number(digits))) return value;

  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

/** Round a distance to a whole number and add commas, such as 12,345. */
export function formatMileage(value: number): string {
  return wholeNumberFormatter.format(Math.round(value));
}

/** Convert the entered distance to the miles expected by the valuation request. */
export function toMiles(value: number, unit: MileageUnit): number {
  return unit === 'kilometers' ? value / KILOMETERS_PER_MILE : value;
}

/** Convert miles to the unit selected for display. */
export function fromMiles(value: number, unit: MileageUnit): number {
  return unit === 'kilometers' ? value * KILOMETERS_PER_MILE : value;
}

/** Default to kilometers in Canada and miles in the US. Use miles if the country is unknown. */
export function defaultMileageUnit(
  languages: readonly string[],
  countryCode?: string | null,
): MileageUnit {
  const serverCountry = countryCode?.toUpperCase();
  if (serverCountry === 'CA') return 'kilometers';

  if (serverCountry === 'US') return 'miles';

  for (const language of languages) {
    try {
      const region = new Intl.Locale(language).region;
      if (region === 'CA') return 'kilometers';

      if (region === 'US') return 'miles';
    } catch {
      // Skip language tags the browser cannot parse and try the next one.
    }
  }

  return 'miles';
}

export interface FormattedMileageEdit {
  value: string;
  selectionStart: number;
  selectionEnd: number;
}

export interface MileageSelection {
  start: number;
  end: number;
}

/** Select the digit beside a comma so Backspace or Delete removes the digit normally. */
export function getAdjacentDigitSelection(
  value: string,
  selectionStart: number,
  selectionEnd: number,
  key: 'Backspace' | 'Delete',
): MileageSelection | null {
  if (selectionStart !== selectionEnd) return null;

  if (key === 'Backspace' && value[selectionStart - 1] === ',') {
    const digitIndex = selectionStart - 2;
    if (digitIndex >= 0 && /\d/.test(value[digitIndex]!)) {
      return { start: digitIndex, end: digitIndex + 1 };
    }
  }

  if (key === 'Delete' && value[selectionStart] === ',') {
    const digitIndex = selectionStart + 1;
    if (digitIndex < value.length && /\d/.test(value[digitIndex]!)) {
      return { start: digitIndex, end: digitIndex + 1 };
    }
  }

  return null;
}

/** Format the input and keep the selection beside the same digits as commas move. */
export function formatMileageEdit(
  value: string,
  selectionStart: number,
  selectionEnd: number,
): FormattedMileageEdit {
  const formatted = formatMileageInput(value);
  if (formatted === value) {
    return {
      value,
      selectionStart: Math.min(selectionStart, value.length),
      selectionEnd: Math.min(selectionEnd, value.length),
    };
  }

  return {
    value: formatted,
    selectionStart: positionAfterDigits(formatted, countDigits(value.slice(0, selectionStart))),
    selectionEnd: positionAfterDigits(formatted, countDigits(value.slice(0, selectionEnd))),
  };
}

/** Count digits without including grouping commas or other formatting. */
function countDigits(value: string): number {
  return (value.match(/\d/g) ?? []).length;
}

/** Find the caret position after a given number of digits in formatted text. */
function positionAfterDigits(value: string, digitCount: number): number {
  if (digitCount === 0) return 0;

  let digitsSeen = 0;

  for (let index = 0; index < value.length; index += 1) {
    if (/\d/.test(value[index]!)) digitsSeen += 1;

    if (digitsSeen === digitCount) return index + 1;
  }

  return value.length;
}
