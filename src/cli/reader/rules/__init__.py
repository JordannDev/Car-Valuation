"""Build rules as native column expressions once, instead of calling Python for every row."""

import re
from collections.abc import Callable
from dataclasses import dataclass

import polars as pl

from ..config import Config
from ..makes import normalize_makes
from ..schema import BOOLEANS, COLUMNS, DATES, NUMBERS
from .make_models import validate_make_models
from .models import repair_models


@dataclass(frozen=True)
class Rule:
    """Describe a cleanup step, the columns it needs, and the columns it produces."""

    id: str
    dependencies: tuple[str, ...]
    requires: tuple[str, ...]
    produces: tuple[str, ...]
    apply: Callable[[pl.LazyFrame, Config], pl.LazyFrame]


def formatting(frame: pl.LazyFrame, config: Config) -> pl.LazyFrame:
    """Trim text and replace configured placeholders and unknown trim labels."""

    expressions = []

    for column in COLUMNS:
        value = pl.col(column).str.strip_chars()
        placeholders = [v.strip().lower() for v in config.placeholders.get(column, [])]
        missing = (value == "") | value.str.to_lowercase().is_in(placeholders)

        # Treat provider labels containing [unspecified] as missing trim information.
        if column == "trim":
            missing = missing | value.str.to_lowercase().str.contains(
                "[unspecified]", literal=True
            )

        expressions.append(pl.when(missing).then(None).otherwise(value).alias(column))

    return frame.with_columns(expressions)


def normalization(frame: pl.LazyFrame, config: Config) -> pl.LazyFrame:
    """Clean up catalog names, then match attribute labels against configured aliases."""

    # Repair names before creating matching keys so all later checks use the same
    # cleaned values.
    frame = normalize_makes(frame, config.aliases.get("make", {}))
    frame = repair_models(frame)
    expressions = [pl.col("vin").str.to_uppercase()]
    expressions.extend(
        pl.col(c).str.to_lowercase().alias(f"{c}_normalized")
        for c in ("make", "model", "trim")
    )

    # Use exact alias matches first. Unmatched values stay empty for warnings or
    # optional fallback rules.
    for column, aliases in config.aliases.items():
        if column == "make":
            continue

        mapping = {key.strip().lower(): value for key, value in aliases.items()}
        expressions.append(
            pl.col(column)
            .str.to_lowercase()
            .replace_strict(mapping, default=None, return_dtype=pl.String)
            .alias(f"{column}_code")
        )

    return frame.with_columns(expressions)


def color_fallback(frame: pl.LazyFrame, config: Config) -> pl.LazyFrame:
    """Try whole color words after exact matches, leaving mixed colors unmatched.

    Use single-word aliases as the searchable color list. Different words for the same
    code count as one match. OTHER is an exact category, not a color word to search for.
    """

    expressions = []

    for column in ("interior_color", "exterior_color"):
        groups: dict[str, list[str]] = {}

        for alias, code in config.aliases.get(column, {}).items():
            token = alias.strip().lower()
            if token.isalpha() and code not in ("OTHER", "UNKNOWN"):
                groups.setdefault(code, []).append(re.escape(token))

        if not groups:
            continue

        # Group synonyms by code so two words for the same color count as one match.
        matches = [
            (
                code,
                pl.col(column)
                .str.to_lowercase()
                .str.contains(r"\b(?:" + "|".join(tokens) + r")\b")
                .fill_null(False),
            )
            for code, tokens in groups.items()
        ]

        # Only fill in a color when exactly one code matches. Keep an existing exact
        # match in preference to a fallback.
        count = pl.sum_horizontal([match.cast(pl.UInt8) for _, match in matches])
        candidate = pl.coalesce(
            [pl.when(match).then(pl.lit(code)) for code, match in matches]
        )
        expressions.append(
            pl.coalesce(
                pl.col(f"{column}_code"),
                pl.when(count == 1).then(candidate),
            ).alias(f"{column}_code")
        )

    return frame.with_columns(expressions)


def conversions(frame: pl.LazyFrame, config: Config) -> pl.LazyFrame:
    """Keep a copy of the cleaned source text before converting types, preserving missing
    values and rejecting lossy conversions.
    """

    frame = frame.with_columns(
        pl.col(c).alias(f"input_{c}") for c in (*NUMBERS, *BOOLEANS, *DATES)
    )
    expressions = [
        pl.col("year").cast(pl.Int32, strict=False),
        pl.col("listing_mileage").cast(pl.Int64, strict=False),
    ]

    # Accept prices only when converting to database precision will not discard decimal
    # places.
    price = pl.col("listing_price")
    expressions.append(
        pl.when(price.str.contains(r"^\d+(\.\d{1,2})?$"))
        .then(price)
        .otherwise(None)
        .cast(pl.Decimal(18, 2), strict=False)
        .alias("listing_price")
    )
    expressions.extend(
        pl.col(c)
        .str.to_lowercase()
        .replace_strict(
            {"true": True, "false": False}, default=None, return_dtype=pl.Boolean
        )
        .alias(c)
        for c in BOOLEANS
    )
    expressions.extend(
        pl.col(c).str.to_date("%Y-%m-%d", strict=False).alias(c) for c in DATES
    )

    return frame.with_columns(expressions)


def validation(frame: pl.LazyFrame, config: Config) -> pl.LazyFrame:
    """Keep every row and mark validation errors separately from unmatched aliases."""

    expressions = [
        (pl.col("model_rule") == "model_json_removed").alias("error_format_model"),
        (pl.col("model_rule") == "model_json_repaired").alias("warning_json_model"),
    ]

    for column in ("vin", "year", "make", "model"):
        expressions.append(pl.col(column).is_null().alias(f"error_required_{column}"))

    expressions.append(
        (
            pl.col("vin").is_not_null()
            & ~pl.col("vin").str.contains(r"^[A-HJ-NPR-Z0-9]{17}$")
        )
        .fill_null(False)
        .alias("error_format_vin")
    )

    # A supplied value that became null during conversion is an error, not ordinary
    # missing data.
    for column in (*NUMBERS, *BOOLEANS, *DATES):
        expressions.append(
            (pl.col(f"input_{column}").is_not_null() & pl.col(column).is_null()).alias(
                f"error_type_{column}"
            )
        )

    for column in NUMBERS:
        expressions.append(
            (pl.col(column) < 0).fill_null(False).alias(f"error_negative_{column}")
        )

    # Unknown optional attributes are warnings; keep the row and leave those attributes
    # unresolved.
    for column in config.aliases:
        if column == "make":
            continue

        expressions.append(
            (pl.col(column).is_not_null() & pl.col(f"{column}_code").is_null()).alias(
                f"warning_unresolved_{column}"
            )
        )

    return validate_make_models(frame.with_columns(expressions), config)


RULES = (
    Rule("format", (), COLUMNS, COLUMNS, formatting),
    Rule(
        "normalize",
        ("format",),
        COLUMNS,
        (
            "vin",
            "make_normalized",
            "make_rule",
            "model_rule",
            "model_normalized",
            "trim_normalized",
            "driven_wheels_code",
            "fuel_type_code",
            "exterior_color_code",
            "interior_color_code",
        ),
        normalization,
    ),
    Rule(
        "color_fallback",
        ("normalize",),
        (
            "interior_color",
            "exterior_color",
            "interior_color_code",
            "exterior_color_code",
        ),
        ("interior_color_code", "exterior_color_code"),
        color_fallback,
    ),
    Rule(
        "types",
        ("format",),
        (*NUMBERS, *BOOLEANS, *DATES),
        (*NUMBERS, *BOOLEANS, *DATES),
        conversions,
    ),
    Rule(
        "validate",
        ("format", "normalize", "types"),
        COLUMNS,
        (
            "model_vin_count",
            "error_model_vins",
            "make_model_count",
            "error_models_make",
            "trim_vin_count",
            "warning_trim_vins",
        ),
        validation,
    ),
)
