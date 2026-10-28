"""Drivetrain categories, including broad labels where the exact layout is unknown."""

from app.extensions import db

from ._mixins import AttributeLookupMixin


class Drivetrain(AttributeLookupMixin, db.Model):
    """A drivetrain category. Broad labels do not identify an exact layout."""

    __tablename__ = "drivetrains"
