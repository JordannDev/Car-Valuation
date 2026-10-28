import type { CatalogMake, CatalogMakeDetail, CatalogSelection } from '../models/catalog';

export interface VehicleSuggestion {
  label: string;
  selection?: CatalogSelection;
}

/** Ignore case and punctuation when matching typed vehicle names. */
function normalize(value: string): string {
  return value
    .trim()
    .toLocaleLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ');
}

/** Find a complete make after the four-digit year, preferring the longest matching name. */
export function makeForQuery(
  query: string,
  makes: readonly CatalogMake[],
): CatalogMake | undefined {
  const match = query.trim().match(/^(\d{4})\s+(.+)$/);
  if (!match) return undefined;

  const remaining = normalize(match[2]!);

  // Prefer the longest matching make name so a shorter name cannot consume part of a
  // multiword make.
  return makes
    .filter(
      (make) =>
        remaining === normalize(make.name) || remaining.startsWith(`${normalize(make.name)} `),
    )
    .sort((left, right) => right.name.length - left.name.length)[0];
}

/** Suggest makes first, then models recorded for the chosen make and year. */
export function suggestionsFor(
  query: string,
  makes: readonly CatalogMake[],
  detail: CatalogMakeDetail | undefined,
): VehicleSuggestion[] {
  const yearMatch = query.trim().match(/^(\d{4})(?:\s+(.*))?$/);
  if (!yearMatch) return [];

  const year = Number(yearMatch[1]);
  const remaining = yearMatch[2]?.trim() ?? '';
  if (!remaining) return makes.slice(0, 10).map((make) => ({ label: `${year} ${make.name}` }));

  // Suggest makes until one is fully matched; only then suggest models available for that
  // year.
  const exactMake = makeForQuery(query, makes);
  if (!exactMake) {
    const partial = normalize(remaining);

    return makes
      .filter((make) => normalize(make.name).startsWith(partial))
      .slice(0, 10)
      .map((make) => ({ label: `${year} ${make.name}` }));
  }

  // Wait for details belonging to this make rather than showing models from the previous
  // request.
  if (!detail || detail.id !== exactMake.id) return [];

  const modelQuery = normalize(remaining).slice(normalize(exactMake.name).length).trim();

  return detail.models
    .flatMap((model) => {
      const modelYear = model.years.find((entry) => entry.year === year);
      if (!modelYear || (modelQuery && !normalize(model.name).startsWith(normalize(modelQuery))))
        return [];

      return [
        {
          label: `${year} ${exactMake.name} ${model.name}`,
          selection: { make: exactMake, model, modelYear },
        },
      ];
    })
    .slice(0, 10);
}
