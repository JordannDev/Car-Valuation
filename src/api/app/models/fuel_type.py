"""Fuel types. Hybrid and electric powertrain details are stored separately."""

from app.extensions import db

from ._mixins import AttributeLookupMixin


class FuelType(AttributeLookupMixin, db.Model):
    """A fuel type; hybrid technology is described by the powertrain type."""

    __tablename__ = "fuel_types"
