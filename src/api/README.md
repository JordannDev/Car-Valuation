# ValueApi API

Flask, SQLAlchemy, and Alembic. Requires Python 3.12+ and MySQL.

## Local setup

Follow the [database setup](../../README.md#local-database), then:

```sh
cd src/api
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
alembic upgrade head
python -m flask --app app run --debug --port 5001
```

Runs at `http://127.0.0.1:5001`. Settings load from the root `.env`; environment
variables take precedence. Set `MYSQL_PASSWORD`. Host and port default to
`127.0.0.1:3306`, with `valueapi` as the database and user.
`DATABASE_URL` overrides the MySQL settings. See [config.py](app/config.py).

## Endpoints

- `GET /health` — app health; does not check the database.
- `GET /api/makes` — known makes.
- `GET /api/makes/<make_id>` — models, years, and trims for a make.
- `GET /api/location` — country hint (`CA`, `US`, or null) from CDN headers.
- `GET /api/valuations/locations` — recorded dealer countries and regions.
- `GET /api/valuations/model-a` — asking-price estimate.

Catalog responses are cached per worker for 15 minutes. Set
`CATALOG_CACHE_TTL_SECONDS` to change this. Imports appear after cache expiry.

## Valuations

```http
GET /api/valuations/model-a?year=2020&make=Toyota&model=Camry
```

Optional parameters: `mileage` with `mileageUnit=KM|MI`, `currency` (default `USD`),
`country`, and `region`. No currency conversion is performed.

Returns an estimated asking price, market range, confidence, mileage basis,
comparables, and listing summary. Results can be current, historical, or projected.
The range and confidence labels are provisional, not statistical guarantees.

Errors: `400` invalid input, `422` insufficient evidence, `503` database unavailable.

Time projection is disabled by default. Calibration and projection settings are
in [config.py](app/config.py); calculations and thresholds are in
[service.py](app/valuation/service.py) and [contracts.py](app/valuation/contracts.py).

## Development

Models live in `app/models/`; keep business logic in services. For schema changes:

```sh
alembic revision --autogenerate -m "describe schema change"
alembic upgrade head
alembic check
```

Review and commit generated migrations with model changes. Migrations never run
on startup.

Use the [CLI](../cli/README.md#importing) for imports. When reading source
observations, use `completed_observations()` from `app.importing.service` to
exclude incomplete batches.
