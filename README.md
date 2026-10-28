# ValueApi

A web app, API, and CLI for estimating vehicle values from sample vehicle listing data. Estimates use asking prices from similar vehicles, rather than confirmed sale prices.

## Getting started

Start the local database below, then follow the [API setup](src/api/README.md#setup) and [frontend setup](src/frontend/README.md#run-locally). Apply the [database migrations](src/api/README.md#migrations) before [importing data](src/cli/README.md#importing).

### Local database

```sh
cp .env.example .env
# Set MYSQL_PASSWORD and MYSQL_ROOT_PASSWORD in .env.
docker compose up -d --wait mysql
```

This starts MySQL 8.4 on `localhost:3306`, with `valueapi` as the database and user. Set `MYSQL_PORT` in `.env` if that port is taken. The API and migrations load the root `.env`; `DATABASE_URL` overrides its MySQL settings.

`docker compose down` stops the database and keeps its data. Password settings only initialize new volumes.

## Project layout

- `src/frontend/` — React and TypeScript web app.
- `src/api/` — Flask API, database models, and migrations.
- `src/cli/` — Data normalization, validation, and imports.
- `sample_data/` — Sample inventory files.

## More details

- [Database diagram](docs/database.dbml)
- [API and configuration](src/api/README.md)
- [CLI commands and imports](src/cli/README.md)
- [Inventory quality tests and reviewed sample outcomes](tests/inventory/README.md)
- [Docker deployment](docs/deployment.md)

## Python formatting

Ruff is included in both Python projects' dev dependencies. After setting up the API, run from the repository root:

```sh
src/api/.venv/bin/ruff format src/api src/cli scripts
src/api/.venv/bin/ruff format --check src/api src/cli scripts
```
