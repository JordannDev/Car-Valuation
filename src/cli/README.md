# ValueApi CLI

Explore, normalize, validate, and import vehicle inventory files. Python 3.12+
with Polars.

## Setup

From the repository root:

```sh
python3 -m venv src/cli/.venv
source src/cli/.venv/bin/activate
python -m pip install -e 'src/cli[dev]'
python src/cli/main.py --help
```

For database imports, also install the API and import dependencies:

```sh
python -m pip install -e src/api
python -m pip install -e 'src/cli[import]'
```

Follow the [API setup](../api/README.md#local-setup) for database configuration
and migrations. The CLI connects directly to MySQL; it does not need the HTTP server.

Or use Docker:

```sh
docker compose build cli
docker compose run --rm cli preview /data/inventory_sm.csv --limit 5
```

Docker mounts `sample_data/` at `/data` read-only. Set `IMPORT_INPUT_DIR` to an
absolute path for other inputs. See [deployment instructions](../../docs/deployment.md)
for deployment Compose usage.

## Usage

```sh
python src/cli/main.py preview sample_data/inventory_sm.csv --limit 5
python src/cli/main.py profile sample_data/inventory.csv --group-by make,model
python src/cli/main.py validate sample_data/inventory.csv --config src/cli/rules.toml
python src/cli/main.py diff sample_data/inventory.csv --verbose
python src/cli/main.py benchmark sample_data/inventory.csv --runs 3
```

Use `--help` on any command for options. Repeat `--filter COLUMN=VALUE` to combine
exact-match filters. `--limit` bounds displayed diagnostics, not validation or exports.
`preview` only checks the displayed portion.

## Rules and output

Configure normalization and validation in [rules.toml](rules.toml). Use
[rules.sample.toml](rules.sample.toml) for small samples. The main config requires
200 distinct valid VINs per model and five supported models per make.

Pass `--aliases src/cli/aliases.example.json` for [alias overrides](aliases.example.json).
Raw values and rule IDs are retained; ambiguous values stay unresolved.
Changing rules does not reprocess completed imports.

Validation prints diagnostics and a final summary. Export normalized data with
`--output`, issues with `--issues`, or full JSONL diagnostics with `--unmatched`
and `--invalid-rows`. Normalized output includes rejected rows and their flags.
Existing output files are refused.

## Importing

Apply API migrations first, then supply metadata that matches the dataset:

```sh
python src/cli/main.py import sample_data/inventory.csv \
  --source inventory --observed-at 2026-10-27T12:00:00Z \
  --currency USD --mileage-unit MI --country-code US \
  --config src/cli/rules.toml --aliases src/cli/aliases.example.json

python src/cli/main.py import-status BATCH_ID
python src/cli/main.py import-resume BATCH_ID
```

Validation is mandatory. Imports commit in chunks; resume skips committed rows.
Keep `~/.local/share/valueapi/imports` intact for resume, or set `--work-dir`.
Docker persists this directory in the `import_data` volume; use the same Compose
project for import, status, and resume.

`--dry-run` reads the database and may create local artifacts, but does not write
to the database. Completed files are deduplicated by source and SHA-256.

Exit codes: `0` success, `1` rejected rows (validation/diff/import), `2` fatal error.
Warnings alone do not fail validation.

## Development

```sh
python -m pytest src/cli/tests -q
```

Rules live in `reader/rules/`. Prefer native Polars expressions over per-row Python.
