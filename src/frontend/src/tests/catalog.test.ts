import type {CatalogMakeDetail} from '../models/catalog.ts';
import assert from 'node:assert/strict';
import test from 'node:test';
import {AsyncRequestSequence, CATALOG_CACHE_TTL_MS, CatalogClient} from '../services/catalog.ts';

class MemoryStorage {
  private readonly values = new Map<string, string>();
  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

const makesResponse = () => new Response(JSON.stringify({makes: [{id: 1, name: 'Toyota'}]}), {status: 200});

test('catalog client deduplicates concurrent requests and keeps session responses for 900 seconds', async () => {
  const storage = new MemoryStorage();
  let now = 10;
  let requestCount = 0;
  const client = new CatalogClient(async () => { requestCount += 1; await Promise.resolve(); return makesResponse(); }, storage, () => now);

  const [first, second] = await Promise.all([client.getMakes(), client.getMakes()]);
  assert.deepEqual(first, second);
  assert.equal(requestCount, 1);
  assert.equal((await client.getMakes())[0]?.name, 'Toyota');
  assert.equal(requestCount, 1);
  now += CATALOG_CACHE_TTL_MS + 1;
  await client.getMakes();
  assert.equal(requestCount, 2);
});

test('failed requests are not cached and can be retried', async () => {
  let requestCount = 0;
  const client = new CatalogClient(async () => {
    requestCount += 1;
    return requestCount === 1 ? new Response('unavailable', {status: 503}) : makesResponse();
  }, new MemoryStorage());

  await assert.rejects(client.getMakes(), /503/);
  assert.equal((await client.getMakes())[0]?.id, 1);
  assert.equal(requestCount, 2);
});

test('blocked session storage falls back to an in-memory TTL cache', async () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage');
  Object.defineProperty(globalThis, 'sessionStorage', {configurable: true, get() { throw new DOMException('Blocked', 'SecurityError'); }});
  let now = 100;
  let requestCount = 0;
  const client = new CatalogClient(async () => { requestCount += 1; return makesResponse(); }, undefined, () => now);

  try {
    assert.equal((await client.getMakes())[0]?.id, 1);
    await client.getMakes();
    assert.equal(requestCount, 1);
    now += CATALOG_CACHE_TTL_MS + 1;
    await client.getMakes();
    assert.equal(requestCount, 2);
  } finally {
    if (original) Object.defineProperty(globalThis, 'sessionStorage', original);
    else Reflect.deleteProperty(globalThis, 'sessionStorage');
  }
});

test('storage read and quota errors still cache valid responses in memory', async () => {
  let requestCount = 0;
  const blockedStorage = {
    getItem() { throw new DOMException('Blocked', 'SecurityError'); },
    setItem() { throw new DOMException('Quota exceeded', 'QuotaExceededError'); },
    removeItem() { throw new DOMException('Blocked', 'SecurityError'); },
  };
  const client = new CatalogClient(async () => { requestCount += 1; return makesResponse(); }, blockedStorage);

  await client.getMakes();
  await client.getMakes();
  assert.equal(requestCount, 1);
});

test('make detail payloads retain catalog IDs and reject malformed descendants', async () => {
  const detail: CatalogMakeDetail = {
    id: 2,
    name: 'Land Rover',
    models: [{id: 20, name: 'Range Rover Sport', years: [{id: 200, year: 2022, trims: [{id: 2000, name: 'SE'}]}]}],
  };
  const client = new CatalogClient(async () => new Response(JSON.stringify(detail), {status: 200}), new MemoryStorage());
  assert.equal((await client.getMakeDetail(2)).models[0]?.years[0]?.trims[0]?.id, 2000);

  const invalid = new CatalogClient(async () => new Response(JSON.stringify({...detail, models: [{id: 20, name: 'Range Rover Sport', years: 'bad'}]}), {status: 200}), new MemoryStorage());
  await assert.rejects(invalid.getMakeDetail(2), /unexpected shape/);
});

test('a newer detail request makes an older async result stale', () => {
  const requests = new AsyncRequestSequence();
  const older = requests.begin();
  const current = requests.begin();
  assert.equal(requests.isCurrent(older), false);
  assert.equal(requests.isCurrent(current), true);
});
