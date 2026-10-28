"""Supported vehicle body types."""

from app.extensions import db

from ._mixins import AttributeLookupMixin


class BodyType(AttributeLookupMixin, db.Model):
    """A supported vehicle body type."""

    __tablename__ = "body_types"
