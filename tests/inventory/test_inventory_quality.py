"""Judge normalization against source records and manually reviewed label meanings."""

from collections import Counter
from datetime import date
from decimal import Decimal

import polars as pl
import pytest
from conftest import SAMPLE_CONFIG, SOURCE
from reader import Config, build
from reader.reporting import rejection_reason


def test_every_source_record_and_raw_field_survives_export(inventory):
    assert len(inventory.source) == inventory.normalized.height == 999
    for number, (source, normalized) in enumerate(
        zip(inventory.source, inventory.normalized.iter_rows(named=True), strict=True),
        2,
    ):
        assert normalized["source_record_number"] == number
        for field, value in source.items():
            assert normalized[f"raw_{field}"] == (value or None), (number, field)


def test_numeric_dates_and_booleans_match_source_without_invented_values(inventory):
    for source, row in zip(
        inventory.source, inventory.normalized.iter_rows(named=True), strict=True
    ):
        context = row["source_record_number"], source["vin"]
        assert row["year"] == int(source["year"]), context
        assert row["listing_price"] == (
            Decimal(source["listing_price"]) if source["listing_price"] else None
        ), context
        assert row["listing_mileage"] == (
            int(source["listing_mileage"]) if source["listing_mileage"] else None
        ), context
        for field in ("first_seen_date", "last_seen_date", "dealer_vdp_last_seen_date"):
            assert row[field] == (
                date.fromisoformat(source[field]) if source[field] else None
            ), (context, field)
        for field in ("used", "certified"):
            assert (
                row[field] == ({"TRUE": True, "FALSE": False, "": None}[source[field]])
            ), (context, field)


def test_known_identities_are_preserved_and_missing_identity_is_not_guessed(inventory):
    for source, row in zip(
        inventory.source, inventory.normalized.iter_rows(named=True), strict=True
    ):
        for field in ("vin", "make", "model"):
            assert row[field] == (source[field] or None), (
                row["source_record_number"],
                field,
            )
    missing = inventory.normalized.filter(pl.col("source_record_number") == 552).row(
        0, named=True
    )
    assert missing["vin"] == "1FAFP42X5WF130486"
    assert missing["make"] is None and missing["model"] is None
    assert missing["error_required_make"] and missing["error_required_model"]


def test_identifiable_inventory_is_accepted_and_only_missing_identity_is_rejected(
    inventory,
):
    # Reviewed source record 552 has neither make nor model. The other 998 records
    # have usable identities; rarity alone must not discard them in the sample profile.
    assert inventory.summary["accepted"] == 998
    assert inventory.summary["rejected"] == 1
    assert inventory.summary["error_counts"] == {
        "error_required_make": 1,
        "error_required_model": 1,
    }
    assert [row["source_record_number"] for row in inventory.invalid] == [552]
    rejected = inventory.invalid[0]
    assert rejected["raw"]["vin"] == "1FAFP42X5WF130486"
    assert {change["issue"] for change in rejected["changes"]} == {
        "error_required_make",
        "error_required_model",
    }


# These answers were reviewed from the actual source labels, not copied from the
# normalizer's output. Explicit broad categories and ambiguity remain intentional.
@pytest.mark.parametrize(
    "number,field,label,expected",
    [
        (3, "driven_wheels", "4X2", "2WD"),
        (20, "driven_wheels", "AWD4WD", "AWD_OR_4WD"),
        (531, "driven_wheels", "Other", "OTHER"),
        (715, "driven_wheels", "All-wheel drive (AWD)", "AWD"),
        (20, "fuel_type", "Biodiesel", "BIODIESEL"),
        (60, "fuel_type", "Flex Fuel Vehicle", "FLEX_FUEL"),
        (142, "fuel_type", "Gasoline Fuel", "GASOLINE"),
        (184, "fuel_type", "Regular Unleaded", "GASOLINE"),
        (650, "fuel_type", "Electric", "ELECTRIC"),
        (220, "fuel_type", "F", None),
        (293, "fuel_type", "RWD", None),
        (590, "fuel_type", "4WD", None),
        (517, "fuel_type", "INTELLIGENT VARIABLE (IVT) -INC: DRIVE MODE SELECT", None),
        (601, "fuel_type", "AWD", None),
        (
            682,
            "driven_wheels",
            "6-Speed Automatic HD Electronic with Overdrive / AWD",
            None,
        ),
        (20, "exterior_color", "Oxford White Exterior", "WHITE"),
        (74, "exterior_color", "Tuxedo Black Metallic", "BLACK"),
        (278, "exterior_color", "Black (charcoal)", None),
        (106, "interior_color", "Diesel Gray/black", None),
        (142, "interior_color", "Almond/mocha", None),
        (3, "interior_color", "N/a", None),
    ],
)
def test_reviewed_real_inventory_attributes(inventory, number, field, label, expected):
    row = inventory.normalized.filter(pl.col("source_record_number") == number).row(
        0, named=True
    )
    assert row[f"raw_{field}"] == label
    assert row[f"{field}_code"] == expected, (number, row["vin"], label)
    if number == 601 and field == "fuel_type":
        assert row["fuel_type"] is None
        assert row["driven_wheels_code"] == "AWD_OR_4WD"


def test_unmatched_export_accounts_for_every_unresolved_source_label(inventory):
    expected = Counter()
    first_records = {}
    for row in inventory.normalized.iter_rows(named=True):
        for field in ("driven_wheels", "fuel_type", "interior_color", "exterior_color"):
            if row[f"warning_unresolved_{field}"]:
                key = field, row[f"raw_{field}"]
                expected[key] += 1
                first_records.setdefault(key, row["source_record_number"])
    actual = {(row["column"], row["before"]): row for row in inventory.unmatched}
    assert expected and actual.keys() == expected.keys()
    for key, count in expected.items():
        assert actual[key]["count"] == count
        assert actual[key]["first_source_record_number"] == first_records[key]
        assert actual[key]["after"] is None
    # The CLI display limit is one; the export must still contain all 11 instances.
    assert actual["exterior_color", "Black (charcoal)"]["count"] == 11


def test_sample_profile_does_not_claim_unobserved_trim_support(inventory):
    for row in inventory.normalized.iter_rows(named=True):
        if row["warning_trim_vins"]:
            assert row["raw_trim"] and row["trim"] is None
    row = inventory.normalized.filter(pl.col("source_record_number") == 27).row(
        0, named=True
    )
    assert row["raw_trim"] == "[Unspecified]" and row["trim"] is None


@pytest.mark.parametrize("minimum,expected_vins", [(24, 24), (25, 0)])
def test_configurable_gate_uses_actual_f150_population(minimum, expected_vins):
    from dataclasses import replace

    config = replace(Config.load(SAMPLE_CONFIG, [], []), minimum_model_vins=minimum)
    frame = build(SOURCE, config).collect()
    accepted = frame.filter(
        ~pl.any_horizontal(pl.col(c) for c in frame.columns if c.startswith("error_"))
    )
    assert accepted.height == expected_vins
    if expected_vins:
        assert accepted["make"].unique().to_list() == ["Ford"]
        assert accepted["model"].unique().to_list() == ["F-150"]
    assert str(minimum) in rejection_reason("error_model_vins", config)


def test_make_gate_can_require_more_than_one_supported_model_in_actual_inventory():
    from dataclasses import replace

    config = replace(
        Config.load(SAMPLE_CONFIG, [], []), minimum_model_vins=24, minimum_make_models=2
    )
    frame = build(SOURCE, config).collect()
    ford = frame.filter(pl.col("model") == "F-150")
    assert ford.height == 24
    assert ford["make_model_count"].to_list() == [1] * 24
    assert ford["error_models_make"].all()
    assert "2 distinct supported models" in rejection_reason(
        "error_models_make", config
    )


def test_default_profile_retains_population_gates_while_sample_profile_is_explicit():
    config = Config()
    assert (config.minimum_model_vins, config.minimum_make_models) == (50, 5)
    frame = build(SOURCE, config).collect()
    errors = [name for name in frame.columns if name.startswith("error_")]
    assert frame.select(pl.any_horizontal(errors).sum()).item() == 999


@pytest.mark.parametrize("name", ["minimum_model_vins", "minimum_make_models"])
@pytest.mark.parametrize("value", ["0", "-1", "1.5", "true", '"1"'])
def test_invalid_support_configuration_is_rejected_before_inventory_is_read(
    tmp_path, name, value
):
    path = tmp_path / "invalid.toml"
    path.write_text(f"{name} = {value}\n")
    with pytest.raises(ValueError, match=f"{name} must be a positive integer"):
        Config.load(path, [], [])
