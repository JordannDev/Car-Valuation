"""Count supporting VINs before accepting make and model names into the catalog."""

import polars as pl

from ..config import Config

MINIMUM_TRIM_VINS = 5


def validate_make_models(frame: pl.LazyFrame, config: Config) -> pl.LazyFrame:
    """Count supporting VINs for makes, models, and trims before splitting the import into
    chunks.
    """

    errors = [
        name for name in frame.collect_schema().names() if name.startswith("error_")
    ]
    basic_valid = ~pl.any_horizontal(pl.col(name) for name in errors)

    # Count distinct VINs across the whole input so repeated rows do not inflate support
    # for a model.
    model_counts = (
        frame.filter(basic_valid & pl.col("vin").is_not_null())
        .group_by("make_normalized", "model_normalized")
        .agg(pl.col("vin").n_unique().alias("model_vin_count"))
    )
    frame = (
        frame.join(
            model_counts,
            on=["make_normalized", "model_normalized"],
            how="left",
            maintain_order="left",
        )
        .with_columns(pl.col("model_vin_count").fill_null(0))
        .with_columns(
            (
                pl.col("make").is_not_null()
                & pl.col("model").is_not_null()
                & (pl.col("model_vin_count") < config.minimum_model_vins)
            ).alias("error_model_vins")
        )
    )

    # Only count models that pass the basic checks and have enough supporting VINs
    # toward a make's support threshold.
    make_counts = (
        frame.filter(
            basic_valid
            & (pl.col("model_vin_count") >= config.minimum_model_vins)
            & pl.col("model_normalized").is_not_null()
        )
        .group_by("make_normalized")
        .agg(pl.col("model_normalized").n_unique().alias("make_model_count"))
    )
    frame = (
        frame.join(make_counts, on="make_normalized", how="left", maintain_order="left")
        .with_columns(pl.col("make_model_count").fill_null(0))
        .with_columns(
            (
                pl.col("make").is_not_null()
                & (pl.col("make_model_count") < config.minimum_make_models)
            ).alias("error_models_make")
        )
    )

    # Check trim support within its make, model, and year. Sparse trims are cleared
    # without rejecting the vehicle.
    trim_counts = (
        frame.filter(
            basic_valid
            & pl.col("trim_normalized").is_not_null()
            & pl.col("vin").is_not_null()
        )
        .group_by("make_normalized", "model_normalized", "year", "trim_normalized")
        .agg(pl.col("vin").n_unique().alias("trim_vin_count"))
    )

    return (
        frame.join(
            trim_counts,
            on=["make_normalized", "model_normalized", "year", "trim_normalized"],
            how="left",
            maintain_order="left",
        )
        .with_columns(pl.col("trim_vin_count").fill_null(0))
        .with_columns(
            (
                pl.col("trim_normalized").is_not_null()
                & (pl.col("trim_vin_count") < MINIMUM_TRIM_VINS)
            ).alias("warning_trim_vins")
        )
        .with_columns(
            pl.when(pl.col("warning_trim_vins"))
            .then(None)
            .otherwise(pl.col("trim"))
            .alias("trim"),
            pl.when(pl.col("warning_trim_vins"))
            .then(None)
            .otherwise(pl.col("trim_normalized"))
            .alias("trim_normalized"),
        )
    )
