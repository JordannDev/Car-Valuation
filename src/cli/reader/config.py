"""Versioned reader settings. Database settings belong elsewhere."""

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .makes import DEFAULT_ALIASES, clean_make, make_key

# Keep the rule list and its version together so config defaults stay in sync.
NORMALIZATION_VERSION = "10"
RULE_IDS = (
    "format",
    "normalize",
    "color_fallback",
    "types",
    "validate",
)


@dataclass(frozen=True)
class Config:
    """Reader settings shared by commands, including enabled rules and source aliases."""

    # General reader and rule settings.
    version: str = NORMALIZATION_VERSION
    quote_char: str | None = None
    enabled: frozenset[str] = frozenset(RULE_IDS)
    minimum_model_vins: int = 50
    minimum_make_models: int = 5

    # Values treated as missing during normalization.
    placeholders: dict[str, list[str]] = field(
        default_factory=lambda: {
            "fuel_type": ["awd"],
            "exterior_color": ["n/a", "na", "unknown"],
            "interior_color": ["n/a", "na", "unknown"],
        }
    )

    # Alternate input spellings mapped to standard values.
    aliases: dict[str, dict[str, str]] = field(
        default_factory=lambda: {
            "make": dict(DEFAULT_ALIASES),
            "driven_wheels": {
                "front-wheel drive": "FWD",
                "fwd": "FWD",
                "4x4": "4WD",
                "4wd": "4WD",
                "awd": "AWD",
                "rear-wheel drive": "RWD",
                "rwd": "RWD",
            },
            "fuel_type": {
                "gasoline": "GASOLINE",
                "diesel": "DIESEL",
                "biodiesel": "BIODIESEL",
            },
            "exterior_color": {
                "white": "WHITE",
                "oxford white": "WHITE",
                "black": "BLACK",
                "silver": "SILVER",
                "gray": "GRAY",
                "grey": "GRAY",
            },
            "interior_color": {
                "white": "WHITE",
                "black": "BLACK",
                "silver": "SILVER",
                "gray": "GRAY",
                "grey": "GRAY",
            },
        }
    )

    def __post_init__(self) -> None:
        """Require positive whole-number support thresholds, including for saved configs."""

        for name in ("minimum_model_vins", "minimum_make_models"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")

    @classmethod
    def load(
        cls,
        path: Path | None,
        enable: list[str],
        disable: list[str],
        aliases_path: Path | None = None,
    ) -> "Config":
        """Check settings and required rules before opening the input file."""

        data = tomllib.loads(path.read_text()) if path else {}

        # Reject misspelled or unsupported top-level settings.
        unknown = set(data) - {
            "version",
            "quote_char",
            "enabled",
            "placeholders",
            "aliases",
            "aliases_file",
            "minimum_model_vins",
            "minimum_make_models",
        }
        if unknown:
            raise ValueError(f"Unknown configuration keys: {sorted(unknown)}")

        # Resolve enabled rules and make sure their dependencies are included.
        version = data.get("version", NORMALIZATION_VERSION)
        if not isinstance(version, str) or not version.strip():
            raise ValueError("version must be a nonempty string")

        # Start with configured rules, then apply explicit command-line additions and
        # removals.
        configured = data.get("enabled", list(RULE_IDS))
        if not isinstance(configured, list) or not all(
            isinstance(v, str) for v in configured
        ):
            raise ValueError("enabled must be an array of rule IDs")

        from .rules import RULES

        rule_ids = {rule.id for rule in RULES}
        requested = set(configured) | set(enable) | set(disable)
        if requested - rule_ids:
            raise ValueError(f"Unknown rules: {sorted(requested - rule_ids)}")

        if set(enable) & set(disable):
            raise ValueError("A rule cannot be both enabled and disabled")

        enabled = (set(configured) | set(enable)) - set(disable)

        # Reject missing dependencies rather than running a rule against incomplete
        # input.
        dependencies = {rule.id: set(rule.dependencies) for rule in RULES}

        for rule in enabled:
            missing = dependencies.get(rule, set()) - enabled
            if missing:
                raise ValueError(f"{rule} requires {sorted(missing)}")

        # An empty quote character keeps literal quote marks in source values.
        quote_char = data.get("quote_char", "")
        if quote_char not in ("", '"'):
            raise ValueError(
                "quote_char must be empty (literal text) or a double quote"
            )

        # Start from built-in defaults, then apply inline and file-based aliases.
        defaults = cls()
        placeholders = data.get("placeholders", defaults.placeholders)
        aliases = merge_aliases(defaults.aliases, data.get("aliases", {}))
        configured_file = data.get("aliases_file")
        if configured_file is not None and (
            not isinstance(configured_file, str) or not configured_file.strip()
        ):
            raise ValueError("aliases_file must be a nonempty path string")

        if aliases_path is None and configured_file is not None:
            aliases_path = (path.parent if path else Path.cwd()) / configured_file

        if aliases_path is not None:
            overrides = json.loads(
                aliases_path.read_text(), object_pairs_hook=unique_json_keys
            )
            aliases = merge_aliases(aliases, overrides, allow_remove=True)

        # Validate table shapes and ensure placeholders refer to known columns.
        from .schema import COLUMNS

        if not isinstance(placeholders, dict) or not isinstance(aliases, dict):
            raise ValueError("placeholders and aliases must be tables")

        for column, values in placeholders.items():
            if (
                column not in COLUMNS
                or not isinstance(values, list)
                or not all(isinstance(v, str) for v in values)
            ):
                raise ValueError(f"Invalid placeholders for {column}")

        return cls(
            version=version,
            quote_char=quote_char or None,
            enabled=frozenset(enabled),
            placeholders=placeholders,
            aliases=aliases,
            minimum_model_vins=data.get(
                "minimum_model_vins", defaults.minimum_model_vins
            ),
            minimum_make_models=data.get(
                "minimum_make_models", defaults.minimum_make_models
            ),
        )


def unique_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON keys so an override cannot be silently lost."""

    result: dict[str, object] = {}

    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")

        result[key] = value

    return result


def merge_aliases(
    base: dict[str, dict[str, str]],
    overrides: object,
    *,
    allow_remove: bool = False,
) -> dict[str, dict[str, str]]:
    """Merge aliases using cleaned-up keys. A JSON null removes an existing mapping."""

    if not isinstance(overrides, dict):
        raise ValueError(
            "aliases must be an object mapping column names to alias objects"
        )

    merged = {column: dict(values) for column, values in base.items()}

    # Normalize alias keys before adding, replacing, or removing mappings.
    for column, values in overrides.items():
        if column not in merged or not isinstance(values, dict):
            raise ValueError(f"Unsupported alias column: {column}")

        column_aliases = merged[column]
        seen: set[str] = set()

        for key, value in values.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"Invalid alias key for {column}")

            normalized = make_key(key) if column == "make" else key.strip().lower()
            if not normalized:
                raise ValueError("Make aliases must contain a manufacturer name")

            if normalized in seen:
                raise ValueError(f"Conflicting normalized aliases for {column}")

            seen.add(normalized)

            # A null override removes the alias only when removals are enabled.
            if value is None and allow_remove:
                column_aliases.pop(normalized, None)
                continue

            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Invalid alias value for {column}.{key}")

            canonical = clean_make(value) if column == "make" else value.strip()
            if not canonical:
                raise ValueError(f"Invalid alias value for {column}.{key}")

            column_aliases[normalized] = canonical

    # Make aliases point directly to canonical manufacturer names.
    makes = merged.get("make", {})

    for value in makes.values():
        if makes.get(make_key(value), value) != value:
            raise ValueError("Make aliases must point directly to a canonical name")

    return merged
