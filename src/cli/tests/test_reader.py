"""Tests for preserving source values, applying cleanup rules, and running CLI commands."""

import csv
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from reader import Config, build
from reader.reporting import summary
from reader.schema import COLUMNS


def vin(index: int) -> str:
    """Return a different valid-looking VIN for each test record."""
    return f"1HGCM82633A{index:06d}"


def supported_model(
    model: str, *, start: int = 1, **changes: str
) -> list[dict[str, str]]:
    """Create the 50 different VINs needed for a model to pass the support threshold."""
    return [
        {"model": model, "vin": vin(start + offset), **changes} for offset in range(50)
    ]


def supporting_models() -> list[dict[str, str]]:
    """Give unrelated test data enough VINs to pass the make and model thresholds."""
    return [
        row
        for index, name in enumerate(("Forte", "Rio", "Soul", "Optima", "Sportage"))
        for row in supported_model(name, start=1_000 + index * 50)
    ]


def dataset(tmp_path: Path, changes: list[dict[str, str]]) -> Path:
    path = tmp_path / "input.csv"
    defaults = {
        "vin": "KNAFK4A61F5428652",
        "year": "2015",
        "make": " Kia ",
        "model": "FORTE",
        "dealer_zip": "00123",
        "used": "FALSE",
        "certified": "TRUE",
    }
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS, delimiter="|")
        writer.writeheader()
        writer.writerows(defaults | row for row in changes)
    return path


def test_values_and_source_identity(tmp_path):
    path = dataset(
        tmp_path,
        [
            {
                "vin": "knafk4a61f5428652",
                "listing_price": "0",
                "listing_mileage": "0",
                "interior_color": "N/a",
                "exterior_color": " Oxford White ",
                "driven_wheels": "AWD",
            },
            {"listing_price": "", "driven_wheels": "4x4"},
            *supporting_models(),
        ],
    )
    rows = build(path, Config()).collect().to_dicts()
    assert [row["source_record_number"] for row in rows] == list(range(2, 254))
    assert rows[0]["vin"] == "KNAFK4A61F5428652"
    assert rows[0]["raw_vin"] == "knafk4a61f5428652"
    assert rows[0]["make"] == "Kia" and rows[0]["make_normalized"] == "kia"
    assert rows[0]["dealer_zip"] == "00123"
    assert rows[0]["used"] is False
    assert rows[0]["listing_price"] == Decimal("0.00")
    assert rows[0]["listing_mileage"] == 0
    assert rows[0]["interior_color"] is None
    assert rows[0]["exterior_color_code"] == "WHITE"
    assert rows[0]["driven_wheels_code"] == "AWD"
    assert rows[1]["driven_wheels_code"] == "4WD"
    assert rows[1]["listing_price"] is None
    assert summary(build(path, Config()))["rejected"][0] == 0


@pytest.mark.parametrize(
    "source,canonical",
    [
        ("Harley-Davidson&#174;", "Harley-Davidson"),
        ("Harley Davidson", "Harley-Davidson"),
        ("Harley-Davidson", "Harley-Davidson"),
        ("Harley-Davidson&amp;#174;", "Harley-Davidson"),
        ("Harley‑Davidson™", "Harley-Davidson"),
        ("HARLEY - DAVIDSON", "Harley-Davidson"),
        ("Ford Truck", "Ford"),
        ("MERCEDES", "Mercedes-Benz"),
        ("Mercedes&nbsp;Benz", "Mercedes-Benz"),
        ("Unknown &amp; Sons Truck", "Unknown & Sons Truck"),
        ("Grand Wagoneer", "Grand Wagoneer"),
    ],
)
def test_make_canonicalization_preserves_source(tmp_path, source, canonical):
    frame = build(dataset(tmp_path, [{"make": source}] * 2), Config()).collect()
    assert frame["make"].to_list() == [canonical, canonical]
    assert frame["raw_make"].to_list() == [source, source]
    assert frame["make_normalized"].to_list() == [canonical.lower()] * 2
    assert frame["source_record_number"].to_list() == [2, 3]
    expected_rule = "normalize.make" if source != canonical else None
    assert frame["make_rule"].to_list() == [expected_rule] * 2


def test_make_alias_configuration_and_empty_names(tmp_path):
    aliases = tmp_path / "aliases.json"
    aliases.write_text('{"make":{"Ford Truck":null,"Acme Motors":"Acme"}}')
    config = Config.load(None, [], [], aliases)
    path = dataset(
        tmp_path, [{"make": "Acme Motors"}, {"make": "Ford Truck"}, {"make": "®"}]
    )
    rows = build(path, config).collect().to_dicts()
    assert [row["make"] for row in rows] == ["Acme", "Ford Truck", None]
    assert rows[2]["error_required_make"] is True
    aliases.write_text('{"make":{"Harley-Davidson®":"A","harley-davidson":"B"}}')
    with pytest.raises(ValueError, match="Conflicting"):
        Config.load(None, [], [], aliases)
    aliases.write_text('{"make":{"A":"B","B":"A"}}')
    with pytest.raises(ValueError, match="canonical"):
        Config.load(None, [], [], aliases)


@pytest.mark.parametrize("model_count,rejected", [(4, True), (5, False)])
def test_make_requires_five_distinct_supported_models(tmp_path, model_count, rejected):
    rows = [
        row | {"make": "Ford" if index % 2 else "Ford Truck"}
        for index in range(model_count)
        for row in supported_model(f"Model {index}", start=10_000 + index * 50)
    ]
    rows += [
        {"make": "FORD", "model": " model 0 ", "year": "2020", "vin": vin(20_000 + i)}
        for i in range(5)
    ]
    rows += [
        {"make": "Other", "model": "One", "vin": vin(21_000 + i)} for i in range(5)
    ]
    frame = build(dataset(tmp_path, rows), Config()).collect()
    ford = frame.filter(pl.col("make") == "Ford")
    assert ford["make_model_count"].to_list() == [model_count] * (model_count * 50 + 5)
    assert ford["error_models_make"].to_list() == [rejected] * (model_count * 50 + 5)
    assert frame.filter(pl.col("make") == "Other")["error_models_make"].all()


def test_invalid_records_and_missing_models_cannot_qualify_a_make(tmp_path):
    rows = [
        row
        for name in ("Forte", "Rio", "Soul", "Optima")
        for row in supported_model(name, start=30_000 + len(name) * 10)
    ] + [
        {"model": "Sportage", "vin": "INVALID"},
        {"model": ""},
        {"make": ""},
    ]
    frame = build(dataset(tmp_path, rows), Config()).collect()
    assert frame["make_model_count"].to_list() == [4] * 202 + [0]
    assert frame["model_vin_count"].to_list()[200] == 0
    assert frame["error_model_vins"].to_list()[200]
    assert frame["error_models_make"].to_list() == [True] * 202 + [False]
    assert frame["error_required_make"][-1]


def test_make_evidence_is_computed_before_preview_filters(tmp_path):
    from reader.pipeline import filtered

    frame = build(dataset(tmp_path, supporting_models()), Config())
    row = filtered(frame, ["model=Forte"]).collect().row(0, named=True)
    assert row["make_model_count"] == 5
    assert row["error_models_make"] is False


@pytest.mark.parametrize(
    "reference_count,expected_count", [(49, 49), (50, 50), (51, 51)]
)
def test_model_support_counts_distinct_vins_across_years(
    tmp_path, reference_count, expected_count
):
    rows = [
        {"model": "Forte", "year": str(2020 + index % 2), "vin": vin(40_000 + index)}
        for index in range(reference_count)
    ]
    rows += [
        {"model": model, "vin": vin(41_000 + model_index * 50 + offset)}
        for model_index, model in enumerate(("Rio", "Soul", "Optima", "Sportage"))
        for offset in range(50)
    ]
    frame = build(dataset(tmp_path, rows), Config()).collect()
    forte = frame.filter(pl.col("model_normalized") == "forte")
    assert forte["model_vin_count"].to_list() == [expected_count] * reference_count
    assert (
        forte["error_model_vins"].to_list() == [reference_count < 50] * reference_count
    )
    assert frame["make_model_count"].to_list() == [
        5 if reference_count >= 50 else 4
    ] * len(rows)


def test_repeated_vin_cannot_satisfy_model_threshold(tmp_path):
    rows = [{"vin": vin(50_000), "model": "Forte"}] * 50
    frame = build(dataset(tmp_path, rows), Config()).collect()
    assert frame["model_vin_count"].to_list() == [1] * 50
    assert frame["error_model_vins"].all()
    assert frame["make_model_count"].to_list() == [0] * 50


def test_invalid_records_cannot_push_model_over_fifty_vin_threshold(tmp_path):
    rows = supported_model("Forte")[:49]
    rows.append({"model": "Forte", "vin": vin(50), "year": "invalid"})
    rows.extend(supporting_models()[50:])
    frame = build(dataset(tmp_path, rows), Config()).collect()
    forte = frame.filter(pl.col("model_normalized") == "forte")
    assert forte["model_vin_count"].to_list() == [49] * 50
    assert forte["error_model_vins"].all()
    assert frame["make_model_count"].to_list() == [4] * len(rows)


def test_trim_support_is_year_scoped_and_low_support_falls_back_to_model(tmp_path):
    rows = [
        {"model": "Forte", "year": "2020", "trim": "EX", "vin": vin(60_000 + i)}
        for i in range(3)
    ] + [
        {"model": "Forte", "year": "2021", "trim": "EX", "vin": vin(60_003 + i)}
        for i in range(2)
    ]
    rows += supported_model("Forte", start=60_100)[:45]
    rows += [
        row
        for model_index, model in enumerate(("Rio", "Soul", "Optima", "Sportage"))
        for row in supported_model(model, start=61_000 + model_index * 50)
    ]
    frame = build(dataset(tmp_path, rows), Config()).collect()
    forte = frame.filter(pl.col("raw_trim") == "EX")
    assert forte["model_vin_count"].to_list() == [50] * 5
    assert forte["trim_vin_count"].to_list() == [3] * 3 + [2] * 2
    assert forte["warning_trim_vins"].all()
    assert forte["trim"].null_count() == 5
    assert forte["trim_normalized"].null_count() == 5
    assert forte["error_model_vins"].sum() == 0
    assert forte["error_models_make"].sum() == 0


@pytest.mark.parametrize("source", ["EX [Unspecified]", " ex [uNsPeCiFiEd] package "])
def test_unspecified_trim_substrings_clear_trim_and_keep_raw(tmp_path, source):
    row = (
        build(dataset(tmp_path, [{"trim": source}]), Config())
        .collect()
        .row(0, named=True)
    )
    assert row["trim"] is None
    assert row["trim_normalized"] is None
    assert row["raw_trim"] == source


def test_supported_trim_remains_normalized(tmp_path):
    rows = [
        {"model": "Forte", "trim": " EX ", "vin": vin(70_000 + index)}
        for index in range(5)
    ]
    rows += supported_model("Forte", start=70_100)[:45]
    rows += [
        row
        for model_index, model in enumerate(("Rio", "Soul", "Optima", "Sportage"))
        for row in supported_model(model, start=71_000 + model_index * 50)
    ]
    forte = (
        build(dataset(tmp_path, rows), Config())
        .collect()
        .filter(pl.col("raw_trim") == " EX ")
    )
    assert forte["trim_vin_count"].to_list() == [5] * 5
    assert forte["warning_trim_vins"].sum() == 0
    assert forte["trim"].to_list() == ["EX"] * 5
    assert forte["trim_normalized"].to_list() == ["ex"] * 5


@pytest.mark.parametrize("trim_references,under_supported", [(4, True), (5, False)])
def test_trim_requires_five_distinct_vins(tmp_path, trim_references, under_supported):
    rows = [
        {"model": "Forte", "trim": "EX", "vin": vin(72_000 + index)}
        for index in range(trim_references)
    ]
    rows += supported_model("Forte", start=72_100)[: 50 - trim_references]
    rows += [
        row
        for model_index, model in enumerate(
            ("Rio", "Soul", "Optima", "Sportage", "Avalon")
        )
        for row in supported_model(model, start=73_000 + model_index * 50)
    ]
    forte = (
        build(dataset(tmp_path, rows), Config())
        .collect()
        .filter(pl.col("raw_trim") == "EX")
    )
    assert forte["warning_trim_vins"].to_list() == [under_supported] * trim_references
    assert forte["trim_vin_count"].to_list() == [trim_references] * trim_references
    assert forte["model_vin_count"].to_list() == [50] * trim_references
    assert forte["error_model_vins"].sum() == 0
    if under_supported:
        assert forte["trim"].null_count() == trim_references
        assert forte["trim_normalized"].null_count() == trim_references
    else:
        assert forte["trim"].to_list() == ["EX"] * trim_references
        assert forte["trim_normalized"].to_list() == ["ex"] * trim_references


def test_invalid_values_and_unresolved_aliases(tmp_path):
    path = dataset(
        tmp_path,
        [
            {
                "vin": "INVALID",
                "year": "abc",
                "listing_price": "1.234",
                "listing_mileage": "-1",
                "used": "maybe",
                "first_seen_date": "2022-02-30",
                "driven_wheels": "4X2",
            },
            {"fuel_type": "mystery"},
            *supporting_models(),
        ],
    )
    frame = build(path, Config())
    row = frame.collect().row(0, named=True)
    for flag in (
        "error_format_vin",
        "error_type_year",
        "error_type_listing_price",
        "error_negative_listing_mileage",
        "error_type_used",
        "error_type_first_seen_date",
        "warning_unresolved_driven_wheels",
    ):
        assert row[flag] is True
    assert summary(frame)["rejected"][0] == 1
    assert summary(frame)["warning_unresolved_fuel_type"][0] == 1


def test_configuration_and_rule_dependencies(tmp_path):
    with pytest.raises(ValueError, match="requires"):
        Config.load(None, [], ["format"])
    with pytest.raises(ValueError, match="Unknown rules"):
        Config.load(None, ["typo"], [])
    path = tmp_path / "rules.toml"
    path.write_text(
        'version = "custom"\nenabled = ["format", "normalize"]\n[aliases.driven_wheels]\n"4x2" = "RWD"\n'
    )
    config = Config.load(path, [], [])
    frame = build(dataset(tmp_path, [{"driven_wheels": "4X2"}]), config).collect()
    assert frame["driven_wheels_code"][0] == "RWD"
    assert not any(c.startswith("error_") for c in frame.columns)
    path.write_text('[aliases.driven_wheels]\n"AWD" = "AWD"\n"awd" = "4WD"\n')
    with pytest.raises(ValueError, match="Conflicting"):
        Config.load(path, [], [])


def test_quoted_multiline_and_malformed_csv(tmp_path):
    path = dataset(tmp_path, [{"dealer_name": "Dealer | North\nLocation"}])
    row = build(path, Config(quote_char='"')).collect().row(0, named=True)
    assert row["raw_dealer_name"] == "Dealer | North\nLocation"
    assert row["source_record_number"] == 2
    with path.open("a") as stream:
        stream.write("|".join(["bad"] * 26) + "\n")
    with pytest.raises(pl.exceptions.PolarsError):
        summary(build(path, Config()))


def test_cli_export_and_failure_status(tmp_path):
    path = dataset(tmp_path, [{"listing_price": "bad"}])
    output = tmp_path / "normalized.parquet"
    issues = tmp_path / "issues.parquet"
    command = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "main.py"),
        "validate",
        str(path),
        "--output",
        str(output),
        "--issues",
        str(issues),
        "--limit",
        "1",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 1, result.stderr
    assert pl.read_parquet(output)["error_type_listing_price"][0]
    assert pl.read_parquet(issues)["source_record_number"][0] == 2
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 2 and "Refusing to overwrite" in result.stderr


def test_short_and_blank_records_fail_structural_check(tmp_path):
    from reader.reader import check_structure

    path = dataset(tmp_path, [{}])
    for malformed in ("short|record\n", "\n"):
        original = path.read_text()
        path.write_text(original + malformed)
        with pytest.raises(ValueError, match="record 3"):
            check_structure(path, Config())
        path.write_text(original)


def test_filter_keeps_original_record_number(tmp_path):
    from reader.pipeline import filtered

    frame = filtered(
        build(dataset(tmp_path, [{"make": "Kia"}, {"make": "Ford"}]), Config()),
        ["make=Ford"],
    )
    assert frame.collect()["source_record_number"].to_list() == [3]


def test_export_embeds_configuration(tmp_path):
    path = dataset(tmp_path, supporting_models())
    output = tmp_path / "data.parquet"
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "main.py"),
            "validate",
            str(path),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    import json

    metadata = pl.read_parquet_metadata(output)
    assert json.loads(metadata["normalization_config"])["version"] == Config().version


def test_cli_vin_diagnostic_and_example_config(tmp_path):
    import json

    path = dataset(tmp_path, [{"vin": "bad"}, *supporting_models()])
    cli = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(cli / "main.py"), "validate", str(path), "--verbose"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stderr
    example = next(
        json.loads(line)
        for line in result.stdout.splitlines()
        if line.startswith('{"type": "invalid_row"')
    )
    assert example["changes"] == [
        {"issue": "error_format_vin", "column": "vin", "before": "bad", "after": "BAD"}
    ]
    assert example["raw"]["vin"] == "bad"
    assert Config.load(cli / "rules.toml", [], []) == Config()


def test_json_alias_merge_precedence_and_removal(tmp_path):
    import json

    aliases = tmp_path / "aliases.json"
    aliases.write_text(
        json.dumps(
            {
                "fuel_type": {"Gas": "GASOLINE"},
                "driven_wheels": {" AWD ": "CUSTOM", "fwd": None},
            }
        )
    )
    config_file = tmp_path / "rules.toml"
    config_file.write_text(
        'aliases_file = "aliases.json"\n[aliases.driven_wheels]\n"rear drive" = "RWD"\n'
    )
    config = Config.load(config_file, [], [])
    assert config.aliases["fuel_type"]["gas"] == "GASOLINE"
    assert config.aliases["fuel_type"]["diesel"] == "DIESEL"
    assert config.aliases["driven_wheels"]["rear drive"] == "RWD"
    assert config.aliases["driven_wheels"]["awd"] == "CUSTOM"
    assert "fwd" not in config.aliases["driven_wheels"]
    assert Config().aliases["driven_wheels"]["awd"] == "AWD"
    override = tmp_path / "override.json"
    override.write_text("{}")
    selected = Config.load(config_file, [], [], override)
    assert selected.aliases["driven_wheels"]["awd"] == "AWD"
    assert "gas" not in selected.aliases["fuel_type"]
    frame = build(
        dataset(tmp_path, [{"fuel_type": "Gas", "driven_wheels": "FWD"}]), config
    ).collect()
    assert frame["fuel_type_code"][0] == "GASOLINE"
    assert frame["warning_unresolved_driven_wheels"][0]


@pytest.mark.parametrize(
    "content",
    [
        "[]",
        '{"unknown": {"x": "Y"}}',
        '{"fuel_type": {"gas": 123}}',
        '{"fuel_type": {"Gas": "A", " gas ": "B"}}',
        '{"fuel_type": {"gas": "A", "gas": "B"}}',
        "{bad json",
    ],
)
def test_invalid_json_alias_files_fail_before_reading(tmp_path, content):
    aliases = tmp_path / "aliases.json"
    aliases.write_text(content)
    with pytest.raises(ValueError):
        Config.load(None, [], [], aliases)


def test_diff_separates_unmatched_from_errors_and_exports_all(tmp_path):
    import json

    path = dataset(
        tmp_path,
        [
            {"fuel_type": "Mystery"},
            {"fuel_type": "Mystery"},
            {"vin": "bad", "listing_price": "invalid"},
            {"fuel_type": "Other"},
            *supporting_models(),
        ],
    )
    unmatched = tmp_path / "unmatched.jsonl"
    invalid = tmp_path / "invalid.jsonl"
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "main.py"),
            "diff",
            str(path),
            "--verbose",
            "--limit",
            "1",
            "--unmatched",
            str(unmatched),
            "--invalid-rows",
            str(invalid),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stderr
    lines = [
        json.loads(line)
        for line in result.stdout.splitlines()
        if line.startswith('{"type":')
    ]
    assert next(row for row in lines if row["type"] == "unmatched")["count"] == 2
    rejected = next(row for row in lines if row["type"] == "invalid_row")
    assert rejected["source_record_number"] == 4
    assert rejected["raw"]["listing_price"] == "invalid"
    assert {change["column"] for change in rejected["changes"]} == {
        "vin",
        "listing_price",
    }
    assert len(unmatched.read_text().splitlines()) == 2
    assert len(invalid.read_text().splitlines()) == 1
    assert json.loads(invalid.read_text())["changes"] == rejected["changes"]
    assert (
        next(row for row in lines if row.get("section") == "unmatched")["has_more"]
        is True
    )


def test_diff_without_issues_has_empty_exports(tmp_path):
    from reader.reporting import invalid_rows, unmatched_values

    frame = build(dataset(tmp_path, supporting_models()), Config())
    assert invalid_rows(frame).collect().is_empty()
    assert unmatched_values(frame).collect().is_empty()


def test_color_fallback_precedence_boundaries_and_ambiguity(tmp_path):
    from dataclasses import replace

    from reader.config import merge_aliases

    cli = Path(__file__).resolve().parents[1]
    config = Config.load(None, [], [], cli / "aliases.example.json")
    config = replace(
        config,
        aliases=merge_aliases(
            config.aliases,
            {
                "interior_color": {"black and tan": "TWO_TONE"},
                "exterior_color": {"black and tan": "TWO_TONE"},
            },
        ),
    )
    values = [
        "TAN LEATHER",
        "Charcoal Cloth",
        "Jet Black, Cloth Seat Trim",
        "Blueberry",
        "Black/White",
        "Gray and Grey",
        "Other",
        "Other Leather",
        "",
        "Black and Tan",
        "Gun Metallic",
        "Silver Ice Metallic",
    ]
    path = dataset(
        tmp_path,
        [{"interior_color": value, "exterior_color": value} for value in values],
    )
    result = build(path, config).collect()
    expected = [
        "TAN",
        "GRAY",
        "BLACK",
        None,
        None,
        "GRAY",
        "OTHER",
        None,
        None,
        "TWO_TONE",
        None,
        "SILVER",
    ]
    for column in ("interior_color", "exterior_color"):
        assert result[f"{column}_code"].to_list() == expected
        assert result[f"raw_{column}"][0] == "TAN LEATHER"
        assert result[f"warning_unresolved_{column}"][0] is False
        assert result[f"warning_unresolved_{column}"][4] is True
        assert result[f"warning_unresolved_{column}"][8] is False


def test_color_fallback_can_be_disabled_and_respects_removed_aliases(tmp_path):
    cli = Path(__file__).resolve().parents[1]
    path = dataset(
        tmp_path, [{"interior_color": "Tan"}, {"interior_color": "Tan Leather"}]
    )
    config = Config.load(None, [], ["color_fallback"], cli / "aliases.example.json")
    assert build(path, config).collect()["interior_color_code"].to_list() == [
        "TAN",
        None,
    ]
    aliases = tmp_path / "aliases.json"
    aliases.write_text('{"interior_color": {"black": null}}')
    config = Config.load(None, [], [], aliases)
    path = dataset(tmp_path, [{"interior_color": "Black Leather"}])
    assert build(path, config).collect()["interior_color_code"][0] is None


def test_ignored_fuel_keeps_source_without_hiding_drivetrain_or_other_issues(tmp_path):
    cli = Path(__file__).resolve().parents[1]
    config = Config.load(cli / "rules.toml", [], [], cli / "aliases.example.json")
    path = dataset(
        tmp_path,
        [
            {"fuel_type": " AWD ", "driven_wheels": "AWD"},
            {"fuel_type": "FWD"},
            {"fuel_type": "Flex Fuel"},
            {"fuel_type": "Flexible Fuel"},
        ],
    )
    result = build(path, config).collect()
    assert result["raw_fuel_type"][0] == " AWD "
    assert result["fuel_type"][0] is None
    assert result["fuel_type_code"].to_list() == [None, None, "FLEX_FUEL", "FLEX_FUEL"]
    assert result["warning_unresolved_fuel_type"].to_list() == [
        False,
        True,
        False,
        False,
    ]
    assert result["driven_wheels_code"][0] == "AWD"


@pytest.mark.parametrize("command", ["validate", "diff"])
def test_concise_rejections_and_final_counts_cover_all_rows(tmp_path, command):
    import json

    path = dataset(
        tmp_path,
        [
            {"make": "", "model": "", "fuel_type": "Mystery"},
            {"listing_mileage": "-1"},
            {},
            *supporting_models(),
        ],
    )
    exported = tmp_path / "invalid.jsonl"
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "main.py"),
            command,
            str(path),
            "--limit",
            "1",
            "--invalid-rows",
            str(exported),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stderr
    records = [
        json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
    ]
    example = next(row for row in records if row["type"] == "invalid_row")
    assert example == {
        "type": "invalid_row",
        "source_record_number": 2,
        "vin": "KNAFK4A61F5428652",
        "reasons": ["Missing required make", "Missing required model"],
    }
    assert not any(row["type"] == "configuration" for row in records)
    final = json.loads(result.stdout.splitlines()[-1])
    assert final["type"] == "summary"
    assert (final["total"], final["accepted"], final["rejected"]) == (253, 251, 2)
    assert final["error_count"] == 3
    assert final["error_counts"] == {
        "error_required_make": 1,
        "error_required_model": 1,
        "error_negative_listing_mileage": 1,
    }
    assert final["warning_count"] == 1
    assert final["warning_counts"] == {"warning_unresolved_fuel_type": 1}
    # Exports must include every issue even when terminal output only shows a limited
    # number.
    exported_rows = [json.loads(line) for line in exported.read_text().splitlines()]
    assert len(exported_rows) == 2
    assert exported_rows[0]["raw"]["make"] is None
    assert exported_rows[1]["changes"][0]["issue"] == "error_negative_listing_mileage"


def test_clean_validation_ends_with_zero_error_summary(tmp_path):
    import json

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "main.py"),
            "validate",
            str(dataset(tmp_path, supporting_models())),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    final = json.loads(result.stdout.splitlines()[-1])
    assert final["accepted"] == 250 and final["rejected"] == 0
    assert final["error_count"] == 0 and final["error_counts"] == {}
    assert final["warning_count"] == 0 and final["warning_counts"] == {}
