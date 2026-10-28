import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, File, Header, HTTPException, Query, Response, UploadFile
from sqlalchemy import func, select

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
    """Compatibility helper retained for existing callers and validation tests."""
    from app.import_rules import default_rules, validate_source
    kind = filename.lower().rsplit(".", 1)[-1]
    if kind not in {"csv", "json"}:
        raise HTTPException(422, "Only CSV and JSON are supported")
    return validate_source(content, filename, default_rules(kind))[0]


@router.get("/record-imports/{batch_id}")
async def read_import(batch_id: int, session: Session, user: CurrentUser):
    batch = await session.get(ImportBatch, batch_id)
    if batch is None or (batch.owner_id != user.id and user.role != "admin"):
        raise HTTPException(404, "Import batch not found")
    return {"id": batch.id, "row_count": batch.row_count, "schema_version_id": batch.schema_version_id,
            "rules_hash": batch.rules_hash, "schema_snapshot": batch.schema_snapshot,
            "legacy": batch.schema_version_id is None}


@router.post("/record-imports", response_model=ImportResult, status_code=201)
async def import_records(session: Session, user: CurrentUser,
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Header(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")],
    preview: bool = False, schema_version_id: int | None = Query(None, gt=0)):
    from datetime import timezone
    from app.import_rules import validate_source
    from app.import_templates import get_template, get_version, version_rules
    from app.models import ImportSchemaTemplate, ImportSchemaVersion
    require_writer(user)
    try:
        content = await file.read(MAX_FILE_BYTES + 1)
    finally:
        await file.close()
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(413, "File exceeds 2 MiB")
    kind = (file.filename or "").lower().rsplit(".", 1)[-1]
    if kind not in {"csv", "json"}:
        raise HTTPException(422, "Only CSV and JSON are supported")
    # One lock ordering for all template writes/imports: user -> template -> version.
    # Locks stay held through validation/commit, so deactivation cannot race a new batch.
    if not preview:
        await session.scalar(select(User.id).where(User.id == user.id).with_for_update())
    implicit_version = schema_version_id is None
    if implicit_version:
        template_id = await session.scalar(select(ImportSchemaTemplate.id).where(
            ImportSchemaTemplate.builtin_key == f"default-{kind}"))
        selected_id = await session.scalar(select(ImportSchemaVersion.id).where(
            ImportSchemaVersion.template_id == template_id, ImportSchemaVersion.number == 1))
    else:
        selected_id = schema_version_id
        template_id = await session.scalar(select(ImportSchemaVersion.template_id).where(ImportSchemaVersion.id == selected_id))
    if template_id is None or selected_id is None:
        raise HTTPException(404, "Schema version not found")
    template = await get_template(session, template_id, user, lock=not preview)
    version = await get_version(session, template.id, selected_id, lock=not preview)
    rules = await version_rules(session, version)
    if rules.digest() != version.rules_hash:
        raise HTTPException(409, "Schema rule checksum mismatch")
    legacy_digest = hashlib.sha256(content + b"\0" + kind.encode()).hexdigest()
    fingerprint = hashlib.sha256(json.dumps([legacy_digest, version.id, version.rules_hash],
                                            separators=(",", ":")).encode()).hexdigest()
    if not preview:
        previous = await session.scalar(select(ImportBatch).where(
            ImportBatch.owner_id == user.id, ImportBatch.idempotency_key == idempotency_key).with_for_update())
        if previous:
            same = (previous.content_hash == fingerprint if previous.schema_version_id is not None
                    else implicit_version and previous.content_hash == legacy_digest)
            if not same:
                raise HTTPException(409, "Idempotency key already used for different content or schema version")
            return ImportResult(batch_id=previous.id, row_count=previous.row_count, preview=False, replayed=True,
                schema_version_id=previous.schema_version_id, rules_hash=previous.rules_hash,
                fingerprint=previous.content_hash)
    if version.status != "published" or not template.is_active or not version.is_active:
        raise HTTPException(409, "Select an active published schema version")
    rows, ignored = validate_source(content, file.filename or "", rules)
    known_categories = set((await session.scalars(select(Category.id))).all())
    category_rule = next(f for f in rules.fields if f.target_field == "category_id")
    missing = [{"row": i, "errors": [{"stage": "category", "source_field": category_rule.source_field,
                "target_field": "category_id", "message": "Category does not exist"}]}
               for i, row in enumerate(rows, 1) if row.category_id not in known_categories]
    if missing:
        raise HTTPException(422, {"message": "Unknown categories; no rows imported", "rows": missing[:50], "error_rows": len(missing)})
    if preview:
        samples = []
        for row in rows[:5]:
            sample = row.model_dump(mode="json")
            sample["timestamp"] = row.timestamp.replace(tzinfo=timezone.utc).isoformat()
            samples.append(sample)
        return ImportResult(row_count=len(rows), preview=True, schema_version_id=version.id,
            rules_hash=version.rules_hash, fingerprint=fingerprint, normalized_sample=samples, ignored_fields=ignored)
    batch = ImportBatch(owner_id=user.id, idempotency_key=idempotency_key,
        content_hash=fingerprint, row_count=len(rows), schema_version_id=version.id,
        rules_hash=version.rules_hash, schema_snapshot=rules.snapshot())
    session.add(batch)
    await session.flush()
    session.add_all([Record(**row.model_dump(), owner_id=user.id, source="import", import_batch_id=batch.id) for row in rows])
    audit(session, user, "record.import", batch.id, f"rows={len(rows)},schema_version={version.id}")
    await session.commit()
    return ImportResult(batch_id=batch.id, row_count=len(rows), preview=False,
        schema_version_id=version.id, rules_hash=version.rules_hash, fingerprint=fingerprint)
