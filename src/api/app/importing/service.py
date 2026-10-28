"""Run imports in chunks, save their progress, and make their data available once complete."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.importing import repository
from app.importing.contracts import ChunkResult, ImportRow, ImportSpec
from app.models import ImportBatch, ImportChunk, ListingObservation
from app.models.enums import ImportBatchStatus

_LOCK_NAME = "valueapi_importer"
_RETRYABLE_ERRNOS = {1205, 1213, 2006, 2013}
process_rows = repository.process_rows


def _validate_spec(spec: ImportSpec) -> None:
    """Check the source details and units before starting an import."""

    if not spec.source_name or len(spec.source_name) > 80:
        raise ValueError("source_name must contain 1 to 80 characters")

    # Resuming must use the same content and settings; a matching filename is not
    # enough.
    hashes = (spec.source_file_hash, spec.rules_hash, spec.artifact_hash)
    if any(
        len(value) != 64
        or any(char not in "0123456789abcdef" for char in value.lower())
        for value in hashes
    ):
        raise ValueError(
            "source, rules, and artifact hashes must be SHA-256 hex digests"
        )

    if (
        spec.total_rows < 0
        or not spec.manifest_uri
        or not spec.artifact_uri
        or not spec.source_file_uri
        or not spec.normalization_version
    ):
        raise ValueError(
            "total_rows and immutable manifest/artifact metadata are required"
        )

    for value, maximum in (
        (spec.source_file_name, 255),
        (spec.normalization_version, 40),
        (spec.source_file_uri, 2048),
        (spec.manifest_uri, 2048),
        (spec.artifact_uri, 2048),
    ):
        if not value or len(value) > maximum:
            raise ValueError(f"import metadata must contain 1 to {maximum} characters")

    if spec.currency_code is not None and not re.fullmatch(
        "[A-Z]{3}", spec.currency_code
    ):
        raise ValueError("currency must contain three uppercase letters")

    if spec.country_code is not None and not re.fullmatch(
        "[A-Z]{2}", spec.country_code
    ):
        raise ValueError("country must contain two uppercase letters")

    if spec.mileage_unit not in (None, "MI", "KM"):
        raise ValueError("mileage unit must be MI or KM")

    if spec.observed_at is not None and spec.observed_at.tzinfo is None:
        raise ValueError("observation time must include a timezone")


def _spec_hash(spec: ImportSpec) -> str:
    """Hash all import settings so resuming cannot change how source rows are interpreted."""

    return hashlib.sha256(
        json.dumps(asdict(spec), sort_keys=True, default=str).encode()
    ).hexdigest()


def _local_path(value: str) -> Path:
    """Read saved files from a local path or a file URI."""

    return (
        Path(unquote(urlparse(value).path))
        if value.startswith("file://")
        else Path(value)
    )


def _acquire_lock(engine: Engine) -> Connection:
    """Take the importer lock and keep its database connection open until the import finishes."""

    # Keep this connection open so it holds the lock for the entire import.
    conn = engine.connect()

    try:
        acquired = conn.scalar(select(func.get_lock(_LOCK_NAME, 0)))
        if acquired == 1:
            return conn
    except Exception:
        conn.close()
        raise

    conn.close()
    raise RuntimeError("another import is running")


def _release_lock(conn: Connection) -> None:
    """Release the importer lock and close its connection, even if releasing it fails."""

    try:
        if not conn.invalidated:
            conn.execute(select(func.release_lock(_LOCK_NAME)))
    except DBAPIError:
        # If the server connection was lost, MySQL has already released its locks.
        conn.invalidate()
    finally:
        conn.close()


def _check_lock(conn: Connection) -> None:
    """Stop if the original database connection no longer holds the importer lock."""

    # A server restart can lose this lock even if the pooled write connections reconnect
    # successfully.
    if (
        conn.invalidated
        or conn.scalar(select(func.is_used_lock(_LOCK_NAME) == func.connection_id()))
        != 1
    ):
        raise RuntimeError(
            "importer lock was lost; resume after connectivity is restored"
        )


def _as_uri(path: Path) -> str:
    """Return the saved file's absolute file URI."""

    return path.resolve().as_uri()


def _write_chunk_report(
    spec: ImportSpec,
    batch_id: int,
    first: int,
    last: int,
    issues: list[dict[str, object]],
) -> str:
    """Save the issue report before linking it to the chunk in the database."""

    manifest_path = _local_path(spec.manifest_uri)
    directory = manifest_path.parent / "issues" / str(batch_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{first}-{last}-{uuid4().hex}.json"

    # Write and flush the full report before renaming it into place.
    temporary = target.with_suffix(".json.tmp")

    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(issues, stream, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())

    temporary.replace(target)

    return _as_uri(target)


def _retryable(error: DBAPIError) -> bool:
    """Check whether a temporary MySQL error allows the whole transaction to be retried."""

    original = getattr(error, "orig", None)
    args = getattr(original, "args", ())

    return bool(args and args[0] in _RETRYABLE_ERRNOS)


def _get_or_create_batch(
    engine: Engine, spec: ImportSpec, resume_id: int | None
) -> ImportBatch:
    """Find an existing batch, check an explicit resume request, or create a new batch."""

    with Session(engine, expire_on_commit=False) as session, session.begin():
        # Only resume an unfinished batch whose source details and settings still match.
        if resume_id is not None:
            batch = session.scalar(
                select(ImportBatch).where(ImportBatch.id == resume_id).with_for_update()
            )
            if batch is None:
                raise ValueError(f"import batch {resume_id} does not exist")

            if batch.status == ImportBatchStatus.COMPLETED:
                raise ValueError("completed batches cannot be resumed")

            _validate_resume(batch, spec)

            return batch

        batch = session.scalar(
            select(ImportBatch)
            .where(
                ImportBatch.source_name == spec.source_name,
                ImportBatch.source_file_hash == spec.source_file_hash,
            )
            .with_for_update()
        )
        if batch is not None:
            if batch.status == ImportBatchStatus.COMPLETED:
                return batch

            raise ValueError(
                "an existing incomplete file requires explicit resume_batch_id"
            )

        # Create the batch once. The unique source-file key prevents duplicate imports.
        batch = ImportBatch(
            source_name=spec.source_name,
            source_file_name=spec.source_file_name,
            source_file_hash=spec.source_file_hash,
            source_file_uri=spec.source_file_uri,
            normalization_version=spec.normalization_version,
            rules_hash=spec.rules_hash,
            manifest_uri=spec.manifest_uri,
            artifact_uri=spec.artifact_uri,
            artifact_hash=spec.artifact_hash,
            spec_hash=_spec_hash(spec),
            total_rows=spec.total_rows,
            status=ImportBatchStatus.RUNNING,
        )
        session.add(batch)
        session.flush()

        return batch


def _validate_resume(batch: ImportBatch, spec: ImportSpec) -> None:
    """Check that the source and processing settings still match the original import."""

    # Changes to the source, cleanup rules, or observation details require a new import.
    identities = (
        batch.spec_hash == _spec_hash(spec),
        batch.source_name == spec.source_name,
        batch.source_file_hash == spec.source_file_hash,
        batch.source_file_uri == spec.source_file_uri,
        batch.source_file_name == spec.source_file_name,
        batch.normalization_version == spec.normalization_version,
        batch.manifest_uri == spec.manifest_uri,
        batch.rules_hash == spec.rules_hash,
        batch.artifact_hash == spec.artifact_hash,
        batch.artifact_uri == spec.artifact_uri,
        batch.total_rows == spec.total_rows,
    )
    if not all(identities):
        raise ValueError("resume metadata differs from the immutable original import")


def _mark_failed(engine: Engine, batch_id: int) -> None:
    """Mark an unfinished batch as failed while keeping its progress available for a resume."""

    with Session(engine, expire_on_commit=False) as session, session.begin():
        session.execute(
            update(ImportBatch)
            .where(
                ImportBatch.id == batch_id,
                ImportBatch.status != ImportBatchStatus.COMPLETED,
            )
            .values(status=ImportBatchStatus.FAILED)
        )


def _mark_running(engine: Engine, batch_id: int) -> int:
    """Mark the batch as running and return its last saved source row number."""

    with Session(engine, expire_on_commit=False) as session, session.begin():
        batch = session.scalar(
            select(ImportBatch).where(ImportBatch.id == batch_id).with_for_update()
        )
        if batch is None:
            raise ValueError("import batch disappeared")

        batch.status = ImportBatchStatus.RUNNING
        if batch.started_at is None:
            batch.started_at = func.utc_timestamp(6)

        batch.completed_at = None

        return batch.last_processed_record


def _process_chunk(
    engine: Engine,
    spec: ImportSpec,
    batch_id: int,
    rows: list[ImportRow],
    lock: Connection,
) -> None:
    """Save the chunk's rows, issues, counts, and checkpoint in one transaction that can be retried."""

    prediction = process_rows

    # Retry the whole transaction so the rows and checkpoint are saved together.
    first, last = rows[0].source_record_number, rows[-1].source_record_number

    for attempt in range(3):
        try:
            _check_lock(lock)

            with Session(engine, expire_on_commit=False) as session, session.begin():
                conn = session.connection()
                batch = session.scalar(
                    select(ImportBatch)
                    .where(ImportBatch.id == batch_id)
                    .with_for_update()
                )
                if batch is None:
                    raise ValueError("import batch disappeared")

                if batch.last_processed_record >= last:
                    return

                # A report only counts once its database link is committed. A failed
                # attempt may leave an unlinked file.
                result = prediction(conn, rows, batch_id, spec)
                issue_items = [asdict(issue) for issue in result.issues]
                report_uri = _write_chunk_report(
                    spec, batch_id, first, last, issue_items
                )
                session.add(
                    ImportChunk(
                        import_batch_id=batch_id,
                        first_record=first,
                        last_record=last,
                        report_uri=report_uri,
                    )
                )

                # Save the counts and checkpoint together with the rows and report link.
                batch.accepted_rows += result.accepted
                batch.rejected_rows += result.rejected
                batch.last_processed_record = last

                return
        except DBAPIError as error:
            if attempt == 2 or not _retryable(error):
                raise

            time.sleep(0.1 * (attempt + 1))

    raise RuntimeError("unreachable retry state")


def run_import(
    engine: Engine,
    spec: ImportSpec,
    chunks: Iterable[list[ImportRow]],
    *,
    resume_batch_id: int | None = None,
    dry_run: bool = False,
    on_progress: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    """Import source rows in order and save a checkpoint after each chunk."""

    _validate_spec(spec)
    if dry_run:
        return _run_dry_run(engine, spec, chunks, on_progress)

    lock = _acquire_lock(engine)
    batch_id: int | None = None
    completed = False

    try:
        batch = _get_or_create_batch(engine, spec, resume_batch_id)
        batch_id = batch.id
        if batch.status == ImportBatchStatus.COMPLETED:
            return {**import_status(engine, batch.id), "no_op": True}

        checkpoint = _mark_running(engine, batch.id)

        for chunk in chunks:
            # Skip rows already saved, then check that the remaining rows continue
            # without gaps.
            pending = [row for row in chunk if row.source_record_number > checkpoint]
            if not pending:
                continue

            if pending[0].source_record_number != checkpoint + 1:
                raise ValueError(
                    "input chunks must be contiguous in original source order"
                )

            if pending[-1].source_record_number > spec.total_rows + 1:
                raise ValueError(
                    "input contains more records than declared by the immutable spec"
                )

            for expected, row in enumerate(pending, start=checkpoint + 1):
                if row.source_record_number != expected:
                    raise ValueError(
                        "records within each chunk must be contiguous and ordered"
                    )

            # Save this chunk before reporting progress or advancing the local
            # checkpoint.
            _process_chunk(engine, spec, batch.id, pending, lock)
            checkpoint = pending[-1].source_record_number
            if on_progress:
                on_progress(
                    {
                        "type": "progress",
                        "batch_id": batch.id,
                        "checkpoint": checkpoint,
                        "total_rows": spec.total_rows,
                    }
                )

        # Completing the batch makes its data available to readers. First check the lock
        # and confirm every source row was processed.
        _check_lock(lock)

        with Session(engine, expire_on_commit=False) as session, session.begin():
            batch = session.scalar(
                select(ImportBatch).where(ImportBatch.id == batch.id).with_for_update()
            )
            if batch is None:
                raise ValueError("import batch disappeared")

            if batch.last_processed_record != spec.total_rows + 1:
                raise ValueError(
                    "input ended before all source records were checkpointed"
                )

            batch.status = ImportBatchStatus.COMPLETED
            batch.completed_at = func.utc_timestamp(6)

        completed = True
        result = import_status(engine, batch.id)

        return result
    except Exception:
        if batch_id is not None and not completed:
            try:
                _mark_failed(engine, batch_id)
            except DBAPIError:
                # If the database is unreachable, the batch stays running and can still
                # be resumed.
                pass

        raise
    finally:
        _release_lock(lock)


def _run_dry_run(
    engine: Engine,
    spec: ImportSpec,
    chunks: Iterable[list[ImportRow]],
    on_progress: Callable[[dict[str, object]], None] | None,
) -> dict[str, object]:
    """Check rows and write issue reports without saving changes to the database."""

    # Run the same checks without saving database changes, but still write issue
    # reports.
    totals = ChunkResult()
    issue_count = 0
    report_uris = []

    with engine.connect() as conn:
        for rows in chunks:
            result = process_rows(conn, rows, 0, spec, dry_run=True)
            totals.accepted += result.accepted
            totals.rejected += result.rejected
            issue_count += len(result.issues)
            if rows:
                report_uris.append(
                    _write_chunk_report(
                        spec,
                        0,
                        rows[0].source_record_number,
                        rows[-1].source_record_number,
                        [asdict(issue) for issue in result.issues],
                    )
                )

            if on_progress:
                on_progress(
                    {
                        "type": "progress",
                        "status": "dry_run",
                        "processed_rows": totals.accepted + totals.rejected,
                        "total_rows": spec.total_rows,
                    }
                )

    return {
        "type": "import",
        "batch_id": None,
        "status": "dry_run",
        "total_rows": spec.total_rows,
        "accepted_rows": totals.accepted,
        "rejected_rows": totals.rejected,
        "checkpoint": 1,
        "issues": issue_count,
        "issue_report_uris": report_uris,
    }


def import_status(engine: Engine, batch_id: int) -> dict[str, object]:
    """Return saved progress and links to the source files and reports."""

    with Session(engine) as session:
        batch = session.scalar(select(ImportBatch).where(ImportBatch.id == batch_id))
        if batch is None:
            raise ValueError(f"import batch {batch_id} does not exist")

        # Read the saved report links so status stays accurate after a restart.
        issue_report_uris = list(
            session.scalars(
                select(ImportChunk.report_uri)
                .where(ImportChunk.import_batch_id == batch_id)
                .order_by(ImportChunk.last_record)
            )
        )

        return {
            "type": "import",
            "batch_id": batch.id,
            "status": batch.status.value,
            "total_rows": batch.total_rows,
            "accepted_rows": batch.accepted_rows,
            "rejected_rows": batch.rejected_rows,
            "checkpoint": batch.last_processed_record,
            "manifest_uri": batch.manifest_uri,
            "artifact_uri": batch.artifact_uri,
            "artifact_hash": batch.artifact_hash,
            "source_file_uri": batch.source_file_uri,
            "issue_report_uris": issue_report_uris,
        }


def existing_import(
    engine: Engine, source_name: str, file_hash: str
) -> dict[str, object] | None:
    """Check whether this source file is already known before preparing another large copy."""

    with engine.connect() as connection:
        batch_id = connection.scalar(
            select(ImportBatch.id).where(
                ImportBatch.source_name == source_name,
                ImportBatch.source_file_hash == file_hash,
            )
        )

    return import_status(engine, batch_id) if batch_id is not None else None


def completed_observations():
    """Select snapshots from completed imports only."""

    return (
        select(ListingObservation)
        .join(ImportBatch)
        .where(ImportBatch.status == ImportBatchStatus.COMPLETED)
    )
