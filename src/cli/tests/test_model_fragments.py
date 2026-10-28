"""Check that repeated scraper mistakes cannot become supported model names."""

import polars as pl
import pytest

from reader import Config, build
from reader.reporting import invalid_rows, rejection_reason
from reader.rules.models import repair_models
from reader.schema import COLUMNS


@pytest.mark.parametrize(
    "source,expected",
    [
        ('{ "model": "F-150", "trim": "XLT" }', "F-150"),
        (r"{\"model\":\"F-150\",\"trim\":\"XLT\"}", "F-150"),
        (r"{\\\"model\\\":\\\"F-150\\\",\\\"trim\\\":\\\"XLT\\\"}", "F-150"),
        ('{"make":"Ford","model":"F-150","bodyType":[]}', "F-150"),
        ('F-150,"trim":"XLT"', "F-150"),
        ('F-150 {"trim":"XLT"}', "F-150"),
        ("F-150 trim:XLT ext_color:Black", "F-150"),
        ('F-150,"@provider-field":"metadata"', "F-150"),
        ('"F-150"', "F-150"),
        (r",\"trim\":", None),
        (',"bodyType":[],"fuelType":', None),
        (',"@provider-field":"metadata"', None),
        ("trim:A35-AMG-4MATICext_color:Night-Black", None),
        ('{"model":null,"trim":"XLT"}', None),
        ('{"model":"unknown"}', None),
        ('{"model":""}', None),
        ('{"model":"F-150","model":"Escape"}', None),
        ('{"vehicle":{"model":"F-150"}}', None),
        ('Escape,"model":"F-150"', None),
        ('["F-150","Escape"]', None),
        ('[{"model":"F-150"}]', None),
        ('{"model":"trim:XLT"}', None),
        ("-trim:-UNKNOWN-acode:", None),
    ],
)
def test_fragment_repair_requires_unambiguous_model(source, expected):
    frame = repair_models(
        pl.LazyFrame({"model": [source], "raw_model": [source]})
    ).collect()
    assert frame["model"].to_list() == [expected]
    assert frame["raw_model"].to_list() == [source]
    assert frame["model_rule"].item() == (
        "model_json_repaired" if expected else "model_json_removed"
    )


def test_legitimate_models_are_preserved():
    names = [
        "F-150",
        "1500 Classic",
        "CX-5",
        "Model 3",
        "Town & Country",
        "C/K 1500",
        "GLC 300 4MATIC SUV []",
        "Golf [Mk7]",
        None,
    ]
    frame = repair_models(pl.LazyFrame({"model": names})).collect()
    assert frame["model"].to_list() == names
    assert frame["model_rule"].to_list() == ["model_preserved"] * len(names)


def test_corruption_is_removed_before_support_counts_and_import(tmp_path):
    # Keep the provider's quotes literal rather than escaping them as CSV. Eight copies
    # reproduce the original failure; repaired names should count toward the real model.
    models = (
        [r",\"trim\":"] * 8
        + [
            '{"model":"F-150","trim":"XLT"}',
            'F-150,"trim":"XLT"',
            "F-150",
            "F-150",
            "F-150",
        ]
        + ["F-150"] * 45
        + [name for name in ("Escape", "Explorer", "Edge", "Focus") for _ in range(50)]
    )
    source = tmp_path / "fragments.csv"
    records = []
    for index, model in enumerate(models):
        row = {
            "vin": f"1HGCM82633A{index:06d}",
            "year": "2021",
            "make": "Ford",
            "model": model,
        }
        records.append("|".join(row.get(column, "") for column in COLUMNS))
    source.write_text("\n".join(["|".join(COLUMNS), *records]) + "\n")

    frame = build(source, Config()).collect()
    broken = frame.head(8)
    assert broken["model"].null_count() == 8
    assert broken["error_format_model"].all()
    assert broken["error_required_model"].all()
    assert broken["model_vin_count"].to_list() == [0] * 8
    assert frame["make_model_count"].to_list() == [5] * len(models)
    assert frame.slice(8, 50)["model_vin_count"].to_list() == [50] * 50
    assert frame.slice(8, 5)["model_normalized"].to_list() == ["f-150"] * 5
    assert frame["warning_json_model"].sum() == 2
    errors = [name for name in frame.columns if name.startswith("error_")]
    assert not frame.slice(8).select(pl.any_horizontal(errors).any()).item()

    diagnostics = invalid_rows(frame.lazy()).collect()
    assert diagnostics.height == 8
    assert "JSON fragment" in rejection_reason("error_format_model")
    assert diagnostics["changes"][0].to_list()[0]["before"] == models[0]

    # Carry the rejected row through to the API even when it falls in another chunk.
    pytest.importorskip("app")
    pytest.importorskip("pyarrow")
    from ingestion.rows import chunks

    artifact = tmp_path / "normalized.parquet"
    frame.write_parquet(artifact)
    rows = [row for chunk in chunks(str(artifact), 3) for row in chunk]
    assert all(
        "error_format_model" in row.errors and row.model is None for row in rows[:8]
    )
    assert rows[8].model == "F-150" and not rows[8].errors
