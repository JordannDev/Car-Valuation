# Docker deployment

Requires Docker Engine, Buildx, and Compose v2.23.1+. Run from the repository root.

## Setup

Copy `.env.example` to `.env`, set both MySQL passwords, and remove `DATABASE_URL`
to use bundled MySQL. Keep `.env` private (`chmod 600 .env`).

```sh
export COMPOSE_FILE=compose.deploy.yaml
docker compose build
docker compose up -d --wait mysql
docker compose run --rm --no-deps api alembic upgrade head
docker compose up -d --wait
```

Open `http://SERVER_IP` (port 80 by default; override with `HTTP_PORT`).
For an existing HTTPS reverse proxy, set `HTTP_BIND_ADDRESS=127.0.0.1` and
`HTTP_PORT=8080`, then proxy to `127.0.0.1:8080`.

## Updates

Back up MySQL, pull the latest code, then run with `COMPOSE_FILE` set as above:

```sh
docker compose build
docker compose stop frontend api
docker compose run --rm --no-deps api alembic upgrade head
docker compose up -d --wait
```

For transferred images, load with `docker image load -i images.tar` and set
`API_IMAGE`, `FRONTEND_IMAGE`, and `CLI_IMAGE` in `.env` to the loaded tags.
Keep both Compose files and `.env` together. Skip builds, add `--pull never`
to migration commands, and use `up -d --wait --no-build`.

## Imports and storage

Build the importer with `docker compose build cli`, then follow the
[CLI commands](../src/cli/README.md#docker).

Deployment uses a separate database volume. `down` preserves it;
`down --volumes` deletes it. Changing `.env` passwords does not update existing databases.
