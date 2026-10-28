import type { MileageUnit } from '../utils/mileage';

const storageKey = 'valueapi:mileage-unit';

/** Read a supported mileage preference, ignoring missing or blocked browser storage. */
export function getSavedMileageUnit(): MileageUnit | undefined {
  try {
    const value = window.localStorage.getItem(storageKey);

    return value === 'miles' || value === 'kilometers' ? value : undefined;
  } catch {
    return undefined;
  }
}

/** Remember the chosen mileage unit when browser storage allows it. */
export function saveMileageUnit(unit: MileageUnit): void {
  try {
    window.localStorage.setItem(storageKey, unit);
  } catch {
    // If browser storage is disabled, keep using the preference saved in memory.
  }
}
