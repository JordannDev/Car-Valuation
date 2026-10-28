"""Clean up make names and apply explicitly configured aliases."""

import html
import re
import unicodedata

import polars as pl

"""
These are default aliases for commonly mis-spelled makes.
"""
DEFAULT_ALIASES = {
    "ford": "Ford",
    "ford truck": "Ford",
    "harley davidson": "Harley-Davidson",
    "harley-davidson": "Harley-Davidson",
    "mercedes": "Mercedes-Benz",
    "mercedes benz": "Mercedes-Benz",
    "mercedes-benz": "Mercedes-Benz",
}
DASHES = str.maketrans({char: "-" for char in "‐‑‒–—−"})


def clean_make(value: str) -> str:
    """Remove formatting noise without guessing a different manufacturer."""

    # Providers sometimes escape the same HTML entity twice, for example &amp;#174;.
    for _ in range(3):
        decoded = html.unescape(value)
        if decoded == value:
            break

        value = decoded

    # Clean up Unicode, dash spacing, and trademark symbols without guessing a different
    # make.
    value = unicodedata.normalize("NFC", value).translate(DASHES)
    value = re.sub("[®™℠]", "", value)
    value = re.sub(r"\s*-\s*", "-", value)

    return " ".join(value.split())


def make_key(value: str) -> str:
    """Build a case-insensitive matching key from a cleaned-up make name."""

    return clean_make(value).casefold()


def canonical_make(value: str, aliases: dict[str, str]) -> str:
    """Clean up unknown names but only merge names with an exact configured alias."""

    cleaned = clean_make(value)

    return aliases.get(cleaned.casefold(), cleaned)


def normalize_makes(frame: pl.LazyFrame, aliases: dict[str, str]) -> pl.LazyFrame:
    """Decode each distinct name once, then join the result back into the data."""

    mapping = (
        frame.select("make")
        .unique()
        .with_columns(
            pl.col("make")
            .map_elements(
                lambda value: canonical_make(value, aliases), return_dtype=pl.String
            )
            .alias("_canonical_make")
        )
    )

    # Map the small list of distinct names instead of calling Python once for every
    # source row.
    return (
        frame.join(
            mapping, on="make", how="left", nulls_equal=True, maintain_order="left"
        )
        .with_columns(
            pl.when(pl.col("make") != pl.col("_canonical_make"))
            .then(pl.lit("normalize.make"))
            .otherwise(None)
            .alias("make_rule"),
            pl.when(pl.col("_canonical_make") == "")
            .then(None)
            .otherwise(pl.col("_canonical_make"))
            .alias("make"),
        )
        .drop("_canonical_make")
    )
