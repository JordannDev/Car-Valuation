"""Supported attribute codes. Unrecognized source labels do not add new codes."""

from app.models import Color, Drivetrain, FuelType

# Keep broad source categories broad; don't turn them into a more specific vehicle
# configuration.
LOOKUPS = {
    Drivetrain: {
        "FWD": ("Front-wheel drive", True),
        "RWD": ("Rear-wheel drive", True),
        "AWD": ("All-wheel drive", True),
        "4WD": ("Four-wheel drive", True),
        "2WD": ("Two-wheel drive", False),
        "AWD_OR_4WD": ("AWD or 4WD", False),
        "OTHER": ("Other", False),
    },
    FuelType: {
        "GASOLINE": ("Gasoline", True),
        "DIESEL": ("Diesel", True),
        "BIODIESEL": ("Biodiesel", True),
        "FLEX_FUEL": ("Flex fuel", True),
        "ELECTRIC": ("Electric", True),
        "HYBRID": ("Hybrid source label", False),
    },
    Color: {
        code: (code.title(), code != "OTHER")
        for code in (
            "WHITE",
            "BLACK",
            "SILVER",
            "GRAY",
            "BLUE",
            "RED",
            "TAN",
            "BROWN",
            "BEIGE",
            "GREEN",
            "YELLOW",
            "ORANGE",
            "PURPLE",
            "GOLD",
            "BRONZE",
            "OTHER",
        )
    },
}
