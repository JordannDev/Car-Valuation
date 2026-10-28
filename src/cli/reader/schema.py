"""Vehicle inventory source fields. Cleaned-up rows are separate from database models."""

COLUMNS = tuple(
    (
        "vin year make model trim dealer_name dealer_street dealer_city dealer_state "
        "dealer_zip listing_price listing_mileage used certified style driven_wheels "
        "engine fuel_type exterior_color interior_color seller_website "
        "first_seen_date last_seen_date dealer_vdp_last_seen_date listing_status"
    ).split()
)
DATES = ("first_seen_date", "last_seen_date", "dealer_vdp_last_seen_date")
NUMBERS = ("year", "listing_price", "listing_mileage")
BOOLEANS = ("used", "certified")
