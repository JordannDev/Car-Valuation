"""Tests for preparing source files and passing them to the import service."""

import json
from pathlib import Path

import pytest

pytest.importorskip("app")
pytest.importorskip("pyarrow")

from ingestion.cli import emit_result, parse_observed_at
from ingestion.prepare import atomic_manifest, digest, prepare
from ingestion.rows import chunks
from main import parser
from reader import Config


def test_import_commands_follow_planned_interface():
    args = parser().parse_args(["import", "inventory.csv", "--dry-run"])
    assert args.import_action == "file"
    assert args.batch_size == 5000
    assert parser().parse_args(["import-resume", "42"]).batch_id == 42
    assert parser().parse_args(["import-status", "42"]).import_action == "status"


def test_staging_preserves_raw_rows_and_normalized_codes(tmp_path):
    original = Path(__file__).parents[3] / "sample_data/inventory_sm.csv"
    config = Config.load(Path(__file__).parents[1] / "rules.sample.toml", [], [])
    retained, artifact, manifest, source_hash, total, rules_hash = prepare(
        original, tmp_path / "imports", config
    )
    assert retained != manifest
    assert total == 999
    assert source_hash == digest(original) == digest(retained)
    assert len(rules_hash) == 64
    batches = list(chunks(str(artifact), 137))
    assert [len(batch) for batch in batches] == [137] * 7 + [40]
    assert batches[0][0].source_record_number == 2
    assert batches[0][0].drivetrain_code == "FWD"
    assert batches[0][0].listing_price is None
    assert batches[0][0].used is True
    assert batches[0][0].certified is False
    rows = [row for batch in batches for row in batch]
    assert [row.source_record_number for row in rows if row.errors] == [552]
    assert rows[550].errors == ("error_required_make", "error_required_model")
    assert rows[-1].source_record_number == 1000


def test_manifest_cannot_be_replaced(tmp_path):
    target = tmp_path / "manifest.json"
    atomic_manifest(target, {"original": True})
    with pytest.raises(FileExistsError):
        atomic_manifest(target, {"original": False})
    assert json.loads(target.read_text()) == {"original": True}


def test_make_model_validation_spans_import_chunks(tmp_path):
    import csv

    import polars as pl

    original = Path(__file__).parents[3] / "sample_data/inventory_sm.csv"
    with original.open() as stream:
        reader = csv.DictReader(stream, delimiter="|", quoting=csv.QUOTE_NONE)
        fields = reader.fieldnames
        base = next(reader)
    source = tmp_path / "five-models.csv"
    with source.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="|")
        writer.writeheader()
        records = [
            base
            | {
                "model": f"Model {model_index}",
                "year": str(2018 + offset % 2),
                "vin": base["vin"][:-6] + f"{model_index * 50 + offset:06d}",
            }
            for model_index in range(5)
            for offset in range(50)
        ]
        records.extend(
            base
            | {
                "model": "Model 5",
                "vin": base["vin"][:-6] + f"{250 + offset:06d}",
            }
            for offset in range(49)
        )
        records.append(
            records[0] | {"trim": "Limited", "first_seen_date": "2024-01-01"}
        )
        writer.writerows(records)
    _, artifact, _, _, _, _ = prepare(source, tmp_path / "imports", Config())
    batches = list(chunks(str(artifact), 2))
    assert [len(batch) for batch in batches] == [2] * 150
    assert all(
        "error_models_make" not in row.errors for batch in batches for row in batch
    )
    normalized = pl.read_parquet(artifact)
    assert normalized["model_vin_count"].to_list() == [50] * 250 + [49] * 49 + [50]
    rows = [row for batch in batches for row in batch]
    assert all(not row.errors for row in rows[:250] + rows[299:])
    assert all(row.errors == ("error_model_vins",) for row in rows[250:299])
    assert normalized["make_model_count"].to_list() == [5] * 300
    assert normalized["trim"].to_list()[-1] is None
    assert normalized["trim_normalized"].to_list()[-1] is None
    assert normalized["raw_trim"].to_list()[-1] == "Limited"
    assert normalized["warning_trim_vins"].to_list()[-1]
    assert batches[-1][-1].trim is None
    assert "warning_trim_vins" in batches[-1][-1].warnings


def test_rejections_set_cli_exit_code(capsys):
    assert emit_result({"status": "completed", "rejected_rows": 2}) == 1
    assert json.loads(capsys.readouterr().out)["rejected_rows"] == 2


def test_observation_timestamp_requires_utc():
    assert parse_observed_at("2026-10-27T12:00:00Z").utcoffset().total_seconds() == 0
    with pytest.raises(ValueError, match="UTC"):
        parse_observed_at("2026-10-27T12:00:00")
