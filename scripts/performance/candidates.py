"""Experimental lookup caches, changed-row writes, and batch sizes for performance testing."""

from collections import Counter, OrderedDict

from app.importing import repository
from sqlalchemy import event, select, tuple_
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

OriginalResolver = repository.Resolver
ORIGINAL_CODES = repository.lookup_codes
CACHE_LIMIT = 20_000
CATALOG = {"makes", "models", "model_years", "trims"}
KEYS = {
    "makes": (("normalized_name",),),
    "models": (("make_id", "normalized_name"),),
    "model_years": (("model_id", "year"),),
    "trims": (("model_year_id", "normalized_name"),),
    "vehicles": (("vin",),),
    "dealers": (("identity_key",),),
    "listings": (
        ("source_name", "source_listing_id"),
        ("source_name", "fallback_identity_key"),
    ),
}
PROJECTION = {
    "makes": ("id", "normalized_name"),
    "models": ("id", "make_id", "normalized_name"),
    "model_years": ("id", "model_id", "year"),
    "trims": ("id", "model_year_id", "normalized_name"),
    "vehicles": (
        "id",
        "vin",
        "model_year_id",
        "trim_id",
        "drivetrain_code",
        "fuel_type_code",
        "exterior_color_code",
        "interior_color_code",
    ),
}
COMMITTED = OrderedDict()
PENDING = {}
CODES = None
PENDING_CODES = None
COUNTS = Counter()
WRITE_ROWS = 500


def reset_run():
    global CODES, PENDING_CODES
    COMMITTED.clear()
    PENDING.clear()
    COUNTS.clear()
    CODES = None
    PENDING_CODES = None


def publish(session):
    global CODES, PENDING_CODES
    # After a failed transaction, discard cached IDs and lookup state so they cannot be
    # reused.
    for key, value in PENDING.items():
        COMMITTED[key] = dict(value)
        COMMITTED.move_to_end(key)
        while len(COMMITTED) > CACHE_LIMIT:
            COMMITTED.popitem(last=False)
    PENDING.clear()
    if PENDING_CODES is not None:
        CODES = PENDING_CODES
        PENDING_CODES = None


def discard(session):
    global PENDING_CODES
    PENDING.clear()
    PENDING_CODES = None


def lookup_codes(resolver):
    global PENDING_CODES
    if CODES is not None:
        COUNTS["lookup_code_cache_hits"] += 1
        return CODES
    PENDING_CODES = ORIGINAL_CODES(resolver)
    return PENDING_CODES


class CachedResolver(OriginalResolver):
    def __init__(self, connection, dry_run):
        super().__init__(connection, dry_run)
        self.cached = {}

    def fetch(self, table, columns, keys):
        cache = self.cached.setdefault((table.name, columns), {})
        missing = []
        for key in keys:
            if key in cache:
                COUNTS["chunk_cache_keys"] += 1
                continue
            shared_key = (table.name, columns, key)
            if table.name in CATALOG and shared_key in COMMITTED:
                cache[key] = dict(COMMITTED[shared_key])
                COMMITTED.move_to_end(shared_key)
                COUNTS["catalog_cache_keys"] += 1
            else:
                missing.append(key)
        found = self.fetch_missing(table, columns, missing)
        for key in missing:
            cache[key] = dict(found[key]) if key in found else None
            if table.name in CATALOG and key in found:
                PENDING[(table.name, columns, key)] = dict(found[key])
        # Callers add details to these dictionaries, so keep a separate copy for
        # comparison.
        return {key: dict(cache[key]) for key in keys if cache[key] is not None}

    def fetch_missing(self, table, columns, keys):
        selected = PROJECTION.get(table.name)
        if selected is None:
            selected = tuple(
                c.name
                for c in table.columns
                if c.name not in ("created_at", "updated_at")
            )
        result = {}
        for chunk in repository.batches(keys):
            predicate = tuple_(*(table.c[name] for name in columns)).in_(chunk)
            for record in self.connection.execute(
                select(*(table.c[name] for name in selected)).where(predicate)
            ).mappings():
                result[tuple(record[name] for name in columns)] = dict(record)
        return result

    def snapshot(self, table, row):
        for columns in KEYS.get(table.name, ()):
            if all(name in row and row[name] is not None for name in columns):
                cache = self.cached.get((table.name, columns), {})
                key = tuple(row[name] for name in columns)
                if key in cache:
                    return cache, key, cache[key]
        return None, None, None

    def write(self, table, rows, updates=()):
        if self.dry_run:
            return
        selected = []
        for row in rows:
            cache, key, snapshot = self.snapshot(table, row)
            if snapshot is not None and (
                not updates
                or all(snapshot.get(name) == row.get(name) for name in updates)
            ):
                COUNTS[f"{table.name}_unchanged_rows_skipped"] += 1
            else:
                selected.append(row)
        self.emit(table, selected, updates)
        for row in selected:
            cache, key, snapshot = self.snapshot(table, row)
            if snapshot is not None:
                snapshot.update({name: row[name] for name in updates})
            elif cache is not None:
                # Read newly assigned IDs back from MySQL after inserting rows.
                cache.pop(key, None)

    def emit(self, table, rows, updates):
        for offset in range(0, len(rows), WRITE_ROWS):
            chunk = list(rows[offset : offset + WRITE_ROWS])
            statement = insert(table)
            assignments = {name: statement.inserted[name] for name in updates}
            primary = next(iter(table.primary_key.columns))
            self.connection.execute(
                statement.on_duplicate_key_update(
                    assignments or {primary.name: primary}
                ),
                chunk,
            )
            COUNTS[f"{table.name}_rows_sent"] += len(chunk)
            COUNTS[f"{table.name}_write_calls"] += 1


def statistics():
    return {
        **dict(COUNTS),
        "catalog_cache_entries": len(COMMITTED),
        "catalog_cache_limit": CACHE_LIMIT,
        "pending_cache_entries": len(PENDING),
        "write_batch_rows": WRITE_ROWS,
    }


def install(write_rows=500):
    global WRITE_ROWS
    WRITE_ROWS = write_rows
    repository.Resolver = CachedResolver
    repository.lookup_codes = lookup_codes
    event.listen(Session, "after_commit", publish)
    event.listen(Session, "after_rollback", discard)
