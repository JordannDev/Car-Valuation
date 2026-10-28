"""Powertrain categories, such as hybrid or electric."""

from app.extensions import db

from ._mixins import AttributeLookupMixin


class PowertrainType(AttributeLookupMixin, db.Model):
    """A supported powertrain category, such as hybrid or electric."""

    __tablename__ = "powertrain_types"
