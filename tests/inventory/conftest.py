"""Export the real sample once; expected answers come from its original CSV."""

import csv
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import polars as pl
import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "sample_data/inventory_sm.csv"
SAMPLE_CONFIG = ROOT / "src/cli/rules.sample.toml"


@dataclass(frozen=True)
class Inventory:
    """Source rows and actual CLI exports, matched by source record number."""

    source: list[dict[str, str]]
    normalized: pl.DataFrame
    directory: Path
    summary: dict[str, object]
    invalid: list[dict[str, object]]
    unmatched: list[dict[str, object]]


@pytest.fixture(scope="session")
def inventory(tmp_path_factory) -> Inventory:
    """Run the shipped sample profile through the same export command users run."""

    directory = tmp_path_factory.mktemp("inventory-exports")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "src/cli/main.py"),
            "validate",
            str(SOURCE),
            "--config",
            str(SAMPLE_CONFIG),
            "--limit",
            "1",
            "--output",
            str(directory / "normalized.parquet"),
            "--invalid-rows",
            str(directory / "invalid.jsonl"),
            "--unmatched",
            str(directory / "unmatched.jsonl"),
        ],
        capture_output=True,
        check=False,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    messages = [
        json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
    ]
    with SOURCE.open(newline="") as stream:
        source = list(csv.DictReader(stream, delimiter="|", quoting=csv.QUOTE_NONE))

    return Inventory(
        source,
        pl.read_parquet(directory / "normalized.parquet"),
        directory,
        next(message for message in messages if message["type"] == "summary"),
        [
            json.loads(line)
            for line in (directory / "invalid.jsonl").read_text().splitlines()
        ],
        [
            json.loads(line)
            for line in (directory / "unmatched.jsonl").read_text().splitlines()
        ],
    )
