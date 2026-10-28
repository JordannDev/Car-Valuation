# Inventory quality tests

These tests run `sample_data/inventory_sm.csv` through the real CLI, inspect its
Parquet/JSONL exports, and compare results with the original CSV and reviewed
attribute meanings. They do not generate supporting vehicles to satisfy gates.

`src/cli/rules.sample.toml` explicitly sets `minimum_model_vins = 1` and
`minimum_make_models = 1` and loads the supplied aliases. The regular defaults
remain 50 and 5; all 999 sample records fail those population gates. Trim support
still requires five VINs per make/model/year/trim and retains unsupported raw labels.

Expected sample outcomes:

- Normalization: 998 accepted; record 552 is missing both make and model.
- Database import: 995 accepted; records 577, 655, and 713 also lack dealer ZIPs.
  These online-only dealers remain rejected under the existing address requirement.
- Missing prices, mileage, and source dates remain missing. Unknown fuel labels and
  ambiguous color combinations remain unresolved, rather than receiving guessed values.
- Valuation receives exactly six priced 2020 Chevrolet Malibus, using their original
  prices, converted mileage, and source dates. The seventh has no price. Without
  configured calibration this sparse cohort must not produce an unsupported estimate.

The source has asking prices, not confirmed sales or independently verified vehicle
values. These tests establish data fidelity and reviewed normalization quality;
they cannot establish market-price accuracy or projection calibration.

Run from the repository root with the CLI environment and API/import dependencies
installed as described in the CLI README:

```sh
POLARS_MAX_THREADS=4 PYTHONPATH=src/api:src/cli \
  src/cli/.venv/bin/python -m pytest tests/inventory -q
```

MySQL tests skip unless `VALUEAPI_IMPORT_TEST_URL` points to a disposable MySQL
database named **valueapi_import_test**. They migrate and clear that database,
import the full sample across multiple chunks, check each stored source record
and rejection report, and verify reimport does not duplicate inventory. Supply
that environment variable through your local credential configuration to include
them. They never reset the regular `valueapi` database.

The existing focused synthetic tests remain for malformed input, concurrency,
transaction recovery, and statistical edge cases absent from the sample.
