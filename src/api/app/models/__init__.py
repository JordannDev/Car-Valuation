"""Import the models here so Alembic can find their table definitions."""

from .body_type import BodyType
from .color import Color
from .dealer import Dealer
from .drivetrain import Drivetrain
from .fuel_type import FuelType
from .import_batch import ImportBatch
from .import_chunk import ImportChunk
from .listing import Listing
from .listing_observation import ListingObservation
from .make import Make
from .model import Model
from .model_year import ModelYear
from .powertrain_type import PowertrainType
from .trim import Trim
from .vehicle import Vehicle

__all__ = [
    "Make",
    "Model",
    "ModelYear",
    "Trim",
    "Drivetrain",
    "BodyType",
    "FuelType",
    "PowertrainType",
    "Color",
    "Vehicle",
    "Dealer",
    "ImportBatch",
    "ImportChunk",
    "Listing",
    "ListingObservation",
]
