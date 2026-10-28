import csv
import hashlib
import io
import json
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, File, Header, HTTPException, Query, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.dependencies import CurrentUser, Session, require_admin, require_owner, require_writer
from app.models import AuditLog, Category, ImportBatch, Record, User
from app.schemas import (CategoryInput, CategoryOutput, ImportResult, RecordInput,
                         RecordOutput, RecordPage, RecordPatch, utc_time)

router = APIRouter(prefix="/api/v1", tags=["records"])
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 1000


async def ensure_category(session, category_id):
    if await session.get(Category, category_id) is None:
        raise HTTPException(422, "Unknown category_id")


def audit(session, user, action, resource_id=None, details=""):
    session.add(AuditLog(actor_id=user.id, action=action, resource_id=resource_id, details=details))


@router.get("/categories", response_model=list[CategoryOutput])
async def categories(session: Session, user: CurrentUser):
    return (await session.scalars(select(Category).order_by(Category.id))).all()


@router.post("/categories", response_model=CategoryOutput, status_code=201)
async def create_category(data: CategoryInput, session: Session, user: CurrentUser):
    require_admin(user)
    category = Category(name=data.name)
    session.add(category)
    await session.flush()
    audit(session, user, "category.create", category.id)
    await session.commit()
    return category


@router.get("/records", response_model=RecordPage)
async def list_records(session: Session, user: CurrentUser,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    category_id: int | None = Query(None, gt=0), owner_id: int | None = Query(None, gt=0),
    source: Literal["manual", "import"] | None = None,
    start: datetime | None = None, end: datetime | None = None,
    sort_by: Literal["id", "title", "value", "timestamp", "created_at"] = "timestamp",
    order: Literal["asc", "desc"] = "desc"):
    try:
        start = utc_time(start) if start else None
        end = utc_time(end) if end else None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if start and end and start >= end:
        raise HTTPException(422, "start must be earlier than end")
    if start and end and (end - start).days > 366:
        raise HTTPException(422, "Time range cannot exceed 366 days")
    filters = []
    for column, value in [(Record.category_id, category_id), (Record.owner_id, owner_id), (Record.source, source)]:
        if value is not None:
            filters.append(column == value)
    if start:
        filters.append(Record.timestamp >= start)
    if end:
        filters.append(Record.timestamp < end)
    column = getattr(Record, sort_by)
    ordering = [column.asc(), Record.id.asc()] if order == "asc" else [column.desc(), Record.id.desc()]
    total = await session.scalar(select(func.count(Record.id)).where(*filters))
    rows = (await session.scalars(select(Record).where(*filters).order_by(*ordering)
        .offset((page - 1) * page_size).limit(page_size))).all()
    return RecordPage(items=[RecordOutput.model_validate(row) for row in rows], total=total, page=page, page_size=page_size)


@router.post("/records", response_model=RecordOutput, status_code=201)
async def create_record(data: RecordInput, session: Session, user: CurrentUser):
    require_writer(user)
    await ensure_category(session, data.category_id)
    row = Record(**data.model_dump(), owner_id=user.id, source="manual")
    session.add(row)
    await session.flush()
    audit(session, user, "record.create", row.id)
    await session.commit()
    return row


async def find_record(session, record_id):
    row = await session.get(Record, record_id)
    if row is None:
        raise HTTPException(404, "Record not found")
    return row


@router.get("/records/{record_id}", response_model=RecordOutput)
async def get_record(record_id: int, session: Session, user: CurrentUser):
    return await find_record(session, record_id)


@router.patch("/records/{record_id}", response_model=RecordOutput)
async def update_record(record_id: int, data: RecordPatch, session: Session, user: CurrentUser):
    row = await find_record(session, record_id)
    require_owner(user, row.owner_id)
    if row.version != data.version:
        raise HTTPException(409, "Record changed; reload before retrying")
    if data.category_id is not None:
        await ensure_category(session, data.category_id)
    for key, value in data.model_dump(exclude_unset=True, exclude={"version"}).items():
        setattr(row, key, value)
    audit(session, user, "record.update", row.id)
    await session.commit()
    return row


@router.delete("/records/{record_id}", status_code=204)
async def delete_record(record_id: int, session: Session, user: CurrentUser,
                        version: int = Query(..., ge=1)):
    row = await find_record(session, record_id)
    require_owner(user, row.owner_id)
    if row.version != version:
        raise HTTPException(409, "Record changed; reload before retrying")
    audit(session, user, "record.delete", row.id)
    await session.delete(row)
    await session.commit()
    return Response(status_code=204)


def parse_import(content: bytes, filename: str) -> list[RecordInput]:
    try:
        text = content.decode("utf-8-sig")
        if filename.lower().endswith(".json"):
            rows = json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite number")))
            if not isinstance(rows, list):
                raise ValueError("JSON must contain an array of objects")
        elif filename.lower().endswith(".csv"):
            reader = csv.DictReader(io.StringIO(text), strict=True)
            expected = {"title", "value", "category_id", "timestamp"}
            if not reader.fieldnames or len(reader.fieldnames) != len(expected) or set(reader.fieldnames) != expected:
                raise ValueError("CSV headers must be title,value,category_id,timestamp")
            rows = []
            for row in reader:
                rows.append(row)
                if len(rows) > MAX_IMPORT_ROWS:
                    break
        else:
            raise ValueError("Only .csv and .json files are supported")
    except (UnicodeDecodeError, ValueError, csv.Error, RecursionError):
        raise HTTPException(422, "Invalid UTF-8 CSV/JSON structure or unsupported file type") from None
    if not 1 <= len(rows) <= MAX_IMPORT_ROWS:
        raise HTTPException(422, "Import must contain 1 to 1000 rows")
    validated, errors = [], []
    for index, row in enumerate(rows, 1):
        try:
            validated.append(RecordInput.model_validate(row))
        except ValidationError as exc:
            errors.append({"row": index, "errors": [
                {"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()]})
    if errors:
        raise HTTPException(422, {"message": "No rows imported", "rows": errors[:50], "error_rows": len(errors)})
    return validated


@router.post("/record-imports", response_model=ImportResult, status_code=201)
async def import_records(session: Session, user: CurrentUser,
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Header(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")],
    preview: bool = False):
    require_writer(user)
    try:
        content = await file.read(MAX_FILE_BYTES + 1)
    finally:
        await file.close()
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(413, "File exceeds 2 MiB")
    rows = parse_import(content, file.filename or "")
    known_categories = set((await session.scalars(select(Category.id))).all())
    missing = [{"row": i, "category_id": row.category_id} for i, row in enumerate(rows, 1)
               if row.category_id not in known_categories]
    if missing:
        raise HTTPException(422, {"message": "Unknown categories; no rows imported", "rows": missing[:50]})
    if preview:
        return ImportResult(row_count=len(rows), preview=True)
    # Serialize imports for this owner. Locking reads below see the latest commit
    # and read a completed previous batch before attempting a duplicate insert.
    await session.scalar(select(User.id).where(User.id == user.id).with_for_update())
    digest = hashlib.sha256(content + b"\0" + (file.filename or "").lower().split(".")[-1].encode()).hexdigest()
    async def existing_result():
        batch = await session.scalar(select(ImportBatch).where(
            ImportBatch.owner_id == user.id, ImportBatch.idempotency_key == idempotency_key).with_for_update())
        if batch:
            if batch.content_hash != digest:
                raise HTTPException(409, "Idempotency key already used for different content")
            return ImportResult(batch_id=batch.id, row_count=batch.row_count, preview=False, replayed=True)
    existing = await existing_result()
    if existing:
        return existing
    batch = ImportBatch(owner_id=user.id, idempotency_key=idempotency_key,
                        content_hash=digest, row_count=len(rows))
    session.add(batch)
    owner_id = user.id
    try:
        await session.flush()
        session.add_all([Record(**row.model_dump(), owner_id=owner_id, source="import", import_batch_id=batch.id)
                         for row in rows])
        audit(session, user, "record.import", batch.id, f"rows={len(rows)}")
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # rollback expires ORM objects; use the captured owner id for the retry lookup.
        previous = await session.scalar(select(ImportBatch).where(
            ImportBatch.owner_id == owner_id, ImportBatch.idempotency_key == idempotency_key))
        if previous and previous.content_hash == digest:
            return ImportResult(batch_id=previous.id, row_count=previous.row_count, preview=False, replayed=True)
        raise HTTPException(409, "Import conflicted; no rows imported") from None
    return ImportResult(batch_id=batch.id, row_count=len(rows), preview=False)
