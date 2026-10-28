"""Merge confirmed make aliases while keeping vehicle and listing IDs intact."""

from collections.abc import Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Make, Model, ModelYear, Trim, Vehicle


def merge_year(session: Session, source: ModelYear, target: ModelYear) -> None:
    """Match trims in the target year before moving vehicles so their trim/year links stay valid."""

    targets = {
        trim.normalized_name: trim
        for trim in session.scalars(select(Trim).where(Trim.model_year_id == target.id))
    }
    originals = list(
        session.scalars(select(Trim).where(Trim.model_year_id == source.id))
    )

    for trim in originals:
        replacement = targets.get(trim.normalized_name)
        if replacement is None:
            replacement = Trim(
                model_year_id=target.id,
                name=trim.name,
                normalized_name=trim.normalized_name,
            )
            session.add(replacement)
            session.flush()
            targets[trim.normalized_name] = replacement

        # Update both columns together so the trim still belongs to the vehicle's model
        # year.
        session.execute(
            update(Vehicle)
            .where(Vehicle.model_year_id == source.id, Vehicle.trim_id == trim.id)
            .values(model_year_id=target.id, trim_id=replacement.id)
        )

    # Move vehicles without trims before deleting the old model year.
    session.execute(
        update(Vehicle)
        .where(Vehicle.model_year_id == source.id, Vehicle.trim_id.is_(None))
        .values(model_year_id=target.id)
    )

    for trim in originals:
        session.delete(trim)

    session.flush()
    session.delete(source)
    session.flush()


def merge_model(session: Session, source: Model, target: Model) -> None:
    """Reuse years that already exist under the target model and move the others with their trim IDs intact."""

    targets = {
        year.year: year
        for year in session.scalars(
            select(ModelYear).where(ModelYear.model_id == target.id)
        )
    }

    for year in list(
        session.scalars(select(ModelYear).where(ModelYear.model_id == source.id))
    ):
        if year.year in targets:
            merge_year(session, year, targets[year.year])
        else:
            year.model_id = target.id

    session.flush()
    session.delete(source)
    session.flush()


def merge_make(session: Session, source: Make, target: Make) -> None:
    """Move models to the target make, merging matching models, years, and trims when needed."""

    targets = {
        model.normalized_name: model
        for model in session.scalars(select(Model).where(Model.make_id == target.id))
    }

    for model in list(session.scalars(select(Model).where(Model.make_id == source.id))):
        if model.normalized_name in targets:
            merge_model(session, model, targets[model.normalized_name])
        else:
            model.make_id = target.id

    session.flush()
    session.delete(source)
    session.flush()


def normalize_catalog_makes(
    session: Session, canonicalize: Callable[[str], str]
) -> list[dict[str, object]]:
    """Apply confirmed make names within the caller's transaction; let the caller decide when to commit."""

    # Lock the make records while applying the confirmed name mappings.
    makes = list(session.scalars(select(Make).order_by(Make.id).with_for_update()))
    targets = {make.normalized_name: make for make in makes}
    changes = []

    for make in makes:
        name = canonicalize(make.name)
        if not name:
            raise ValueError(f"Make {make.id} has no canonical manufacturer name")

        key = name.strip().lower()
        if (name, key) == (make.name, make.normalized_name):
            continue

        target = targets.get(key)
        changes.append({"id": make.id, "before": make.name, "after": name})
        if target is not None and target.id != make.id:
            merge_make(session, make, target)
            del targets[make.normalized_name]
        else:
            targets.pop(make.normalized_name)
            make.name, make.normalized_name = name, key
            targets[key] = make
            session.flush()

    return changes
