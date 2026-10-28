import type { JsonField, JsonObject, JsonValue } from '../models/json.ts';
import type {
  CatalogMake,
  CatalogMakeDetail,
  CatalogModel,
  CatalogTrim,
  CatalogYear,
} from '../models/catalog.ts';

export const CATALOG_CACHE_TTL_MS = 900_000;
const CACHE_PREFIX = 'valueapi.catalog.v1.';

type CatalogStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

/** Use session storage when available, including browsers that may block access to it. */
function getSessionStorage(): CatalogStorage | undefined {
  try {
    return typeof sessionStorage === 'undefined' ? undefined : sessionStorage;
  } catch {
    return undefined;
  }
}

/** Identify the latest request so older responses can be ignored. */
export class AsyncRequestSequence {
  private sequence = 0;

  /** Start a new request sequence and make all earlier sequences stale. */
  begin(): number {
    this.sequence += 1;

    return this.sequence;
  }

  /** Check whether a response still belongs to the most recent request. */
  isCurrent(sequence: number): boolean {
    return sequence === this.sequence;
  }
}

function isRecord(value: JsonField): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isId(value: JsonField): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
}

/** Read a make only when its catalog ID and display name are valid. */
function parseMake(value: JsonField): CatalogMake | undefined {
  if (
    !isRecord(value) ||
    !isId(value.id) ||
    typeof value.name !== 'string' ||
    value.name.trim() === ''
  )
    return undefined;

  return { id: value.id, name: value.name };
}

/** Validate a make and all of its models, years, and trims before exposing them to the UI. */
function parseDetail(value: JsonField): CatalogMakeDetail | undefined {
  const make = parseMake(value);
  if (!make || !isRecord(value) || !Array.isArray(value.models)) return undefined;

  // Build fresh typed records at each catalog level; malformed children reject the response.
  const models: CatalogModel[] = [];

  for (const entry of value.models) {
    if (
      !isRecord(entry) ||
      !isId(entry.id) ||
      typeof entry.name !== 'string' ||
      !entry.name.trim() ||
      !Array.isArray(entry.years)
    )
      return undefined;

    const years: CatalogYear[] = [];

    for (const yearEntry of entry.years) {
      if (
        !isRecord(yearEntry) ||
        !isId(yearEntry.id) ||
        typeof yearEntry.year !== 'number' ||
        !Number.isInteger(yearEntry.year) ||
        !Array.isArray(yearEntry.trims)
      )
        return undefined;

      const trims: CatalogTrim[] = [];

      for (const trimEntry of yearEntry.trims) {
        if (!isRecord(trimEntry) || !isId(trimEntry.id) || typeof trimEntry.name !== 'string')
          return undefined;

        trims.push({ id: trimEntry.id, name: trimEntry.name });
      }

      years.push({ id: yearEntry.id, year: yearEntry.year, trims });
    }

    models.push({ id: entry.id, name: entry.name, years });
  }

  return { ...make, models };
}

/** Reject the whole make list if any entry is malformed. */
function parseMakes(value: JsonField): CatalogMake[] | undefined {
  if (!isRecord(value) || !Array.isArray(value.makes)) return undefined;

  const makes = value.makes.map(parseMake);

  return makes.every((make): make is CatalogMake => make !== undefined) ? makes : undefined;
}

interface StoredValue<T> {
  expiresAt: number;
  value: T;
}

/** Load catalog data with a short-lived cache and shared in-flight requests. */
export class CatalogClient {
  private readonly pending = new Map<string, Promise<JsonValue>>();
  private readonly memory = new Map<string, StoredValue<JsonValue>>();
  private readonly fetcher: typeof fetch;
  private readonly storage: CatalogStorage | undefined;
  private readonly now: () => number;

  constructor(
    fetcher: typeof fetch = fetch,
    storage: CatalogStorage | undefined = getSessionStorage(),
    now: () => number = Date.now,
  ) {
    this.fetcher = fetcher.bind(globalThis);
    this.storage = storage;
    this.now = now;
  }

  /** Load the available makes from cache or the API. */
  getMakes(): Promise<CatalogMake[]> {
    return this.get('makes', '/api/makes', parseMakes);
  }

  /** Load one make with its models, years, and trims. */
  getMakeDetail(makeId: number): Promise<CatalogMakeDetail> {
    if (!isId(makeId)) return Promise.reject(new Error('Invalid make id.'));

    return this.get(`make-${makeId}`, `/api/makes/${makeId}`, parseDetail);
  }

  /** Reuse valid cache entries or an in-flight fetch, validating each result with its parser. */
  private async get<T>(
    key: string,
    url: string,
    parse: (value: JsonField) => T | undefined,
  ): Promise<T> {
    // Check valid cached data first, then share any request already in flight for the same
    // key.
    const stored = this.read(key, parse);
    if (stored !== undefined) return stored;

    const existing = this.pending.get(key);
    if (existing) {
      const parsed = parse(await existing);
      if (parsed === undefined) throw new Error('Catalog response had an unexpected shape.');

      return parsed;
    }

    // Validate before caching. Failed requests are removed from the pending map so a retry
    // can start.
    const request = this.fetcher(url, { signal: AbortSignal.timeout(15_000) })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Catalog request failed (${response.status}).`);

        const payload: JsonValue = await response.json();
        const parsed = parse(payload);
        if (parsed === undefined) throw new Error('Catalog response had an unexpected shape.');

        this.write(key, payload);

        return payload;
      })
      .finally(() => this.pending.delete(key));
    this.pending.set(key, request);
    const parsed = parse(await request);
    if (parsed === undefined) throw new Error('Catalog response had an unexpected shape.');

    return parsed;
  }

  /** Read a still-valid cache entry, ignoring expired or malformed data. */
  private read<T>(key: string, parse: (value: JsonField) => T | undefined): T | undefined {
    const memory = this.memory.get(key);
    if (memory) {
      if (memory.expiresAt > this.now()) {
        const parsed = parse(memory.value);
        if (parsed !== undefined) return parsed;
      }

      this.memory.delete(key);
    }

    try {
      // Storage can contain stale or malformed data from an older session; validate it like
      // an API response.
      const serialized = this.storage?.getItem(CACHE_PREFIX + key);
      if (!serialized) return undefined;

      const entry: JsonField = JSON.parse(serialized);
      if (
        !isRecord(entry) ||
        typeof entry.expiresAt !== 'number' ||
        entry.expiresAt <= this.now()
      ) {
        this.storage?.removeItem(CACHE_PREFIX + key);

        return undefined;
      }

      const parsed = parse(entry.value);
      if (parsed === undefined) this.storage?.removeItem(CACHE_PREFIX + key);

      return parsed;
    } catch {
      return undefined;
    }
  }

  /** Cache a valid response in memory and, when available, session storage. */
  private write(key: string, value: JsonValue): void {
    const entry: StoredValue<JsonValue> = { expiresAt: this.now() + CATALOG_CACHE_TTL_MS, value };

    // Keep an in-memory copy even when browser storage is unavailable or full.
    this.memory.set(key, entry);

    try {
      this.storage?.setItem(CACHE_PREFIX + key, JSON.stringify(entry));
    } catch {
      // Keep the catalog usable when private browsing or storage limits prevent caching.
    }
  }
}

export const catalogClient = new CatalogClient();
