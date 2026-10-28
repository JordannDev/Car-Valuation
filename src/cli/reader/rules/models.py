"""Repair model names that contain embedded metadata, using native column expressions."""

import polars as pl


# Handle provider fragments with unquoted keys as well as quoted JSON keys.
FIELD = (
    r'(?i)(?:"[^"\\]+"\s*:'
    r"|\b(?:model|trim|body_?type|fuel_?type|ext_?color|int_?color|price|bodystyle|acode|vin)\s*:)"
)
MODEL_FIELD = r'(?i)(?:^|[,{])\s*"model"\s*:\s*"([^"\\]+)"'


def repair_models(frame: pl.LazyFrame) -> pl.LazyFrame:
    """Recover a model name only when the source states it; never guess it from the trim,
    make, or VIN.

    Decode repeatedly escaped quotes to inspect provider fragments. Keep ordinary
    display names and raw columns unchanged. Leave nested objects and duplicate model
    fields unrepaired.
    """

    text = pl.col("model").str.replace_all(r'\\+"', '"')
    fragment = (
        text.str.contains(FIELD)
        | text.str.contains(r"[{}]|^\s*\[")
        | text.str.starts_with('"')
        | text.str.ends_with('"')
    ).fill_null(False)

    # Look for a model stated before metadata, in a model field, or as a single quoted
    # value.
    prefix = text.str.extract(r"^(.*?)" + FIELD, 1).str.strip_chars(' \t\r\n,;"{')
    explicit = text.str.extract(MODEL_FIELD, 1).str.strip_chars()
    quoted = text.str.extract(r'^"([^"\\]+)"$', 1).str.strip_chars()

    def usable(value: pl.Expr) -> pl.Expr:
        """Accept only a plain name with no leftover metadata or missing-value label."""

        return (
            value.is_not_null()
            & (value != "")
            & value.str.contains(r"[\p{L}\p{N}]")
            & ~value.str.contains(r'[{}\[\]:"\\]')
            & ~value.str.contains(FIELD)
            & ~value.str.to_lowercase().is_in(["null", "unknown", "n/a", "undefined"])
        ).fill_null(False)

    # Leave multiple or nested objects and conflicting names unrepaired because the
    # intended model is unclear.
    unambiguous = (
        ~text.str.contains(r"^\s*\[")
        & (text.str.count_matches(r"\{") <= 1)
        & (text.str.count_matches(r"\}") <= 1)
        & (text.str.count_matches(r'(?i)"model"\s*:') <= 1)
        & ~(usable(prefix) & usable(explicit) & (prefix != explicit)).fill_null(False)
    )

    # Prefer an explicit model field, then a plain-text prefix, then a single quoted
    # name.
    candidate = pl.coalesce(
        pl.when(usable(explicit)).then(explicit),
        pl.when(usable(prefix)).then(prefix),
        pl.when(usable(quoted)).then(quoted),
    )
    repaired = pl.when(unambiguous).then(candidate)

    # Keep the repair result alongside a rule flag so reports can explain whether the
    # name was repaired or rejected.
    return frame.with_columns(
        pl.when(fragment).then(repaired).otherwise(pl.col("model")).alias("model"),
        pl.when(fragment)
        .then(
            pl.when(repaired.is_not_null())
            .then(pl.lit("model_json_repaired"))
            .otherwise(pl.lit("model_json_removed"))
        )
        .otherwise(pl.lit("model_preserved"))
        .alias("model_rule"),
    )
