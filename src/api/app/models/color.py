"""Interior and exterior colors, with uncertain matches left empty."""

from app.extensions import db

from ._mixins import AttributeLookupMixin


class Color(AttributeLookupMixin, db.Model):
    """A supported interior or exterior color. Leave uncertain matches empty."""

    __tablename__ = "colors"
