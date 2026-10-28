import hashlib
import json
from datetime import timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Header, HTTPException, Query, Request, Response, UploadFile
from pydantic import Field, ValidationError, model_validator
from sqlalchemy import case, cast, func, select
from sqlalchemy.dialects.mysql import DECIMAL

from app.dataset_rules import DatasetDefinition, DatasetField, decode_payload, unique_value_hash, validate_rows
from app.dependencies import CurrentUser, Session, require_owner, require_writer
from app.import_rules import no_duplicates, parse_source
from app.models import (AuditLog, Dataset, DatasetImportBatch, DatasetRecord,
                        DatasetSchemaVersion, DatasetUniqueValue, DatasetMonitorAlert, User)
from app.schemas import StrictModel

router = APIRouter(prefix="/api/v1/datasets", tags=["datasets"])


class DatasetCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    fields: list[DatasetField] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def complete_definition(self):
        DatasetDefinition(fields=self.fields)
        return self


class AppendFields(StrictModel):
    schema_version: int = Field(ge=1)
    fields: list[DatasetField] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_fields(self):
        DatasetDefinition(fields=self.fields)
        return self


class ManualRecord(StrictModel):
    schema_version: int = Field(ge=1)
    data: dict[str, Any]


def audit(session, user, action, resource_id):
    session.add(AuditLog(actor_id=user.id, action=action, resource_id=resource_id))


async def find_dataset(session, dataset_id, user, write=False):
    if write:
        require_writer(user)
        await session.scalar(select(User.id).where(User.id == user.id).with_for_update())
    query = select(Dataset).where(Dataset.id == dataset_id)
    if write:
        query = query.with_for_update().execution_options(populate_existing=True)
    dataset = await session.scalar(query)
    if dataset is None:
        raise HTTPException(404, "Dataset not found")
    if write:
        require_owner(user, dataset.owner_id)
    return dataset


async def schema_at(session, dataset, number=None):
    version = await session.scalar(select(DatasetSchemaVersion).where(
        DatasetSchemaVersion.dataset_id == dataset.id,
        DatasetSchemaVersion.number == (number if number is not None else dataset.schema_version)))
    if version is None:
        raise HTTPException(404, "Dataset schema version not found")
    definition = DatasetDefinition.model_validate(version.definition)
    if definition.digest() != version.rules_hash:
        raise HTTPException(409, "Dataset schema checksum mismatch")
    return version, definition


async def dataset_output(session, dataset, user):
    version, definition = await schema_at(session, dataset)
    return {"id": dataset.id, "name": dataset.name, "description": dataset.description,
        "owner_id": dataset.owner_id, "schema_version": dataset.schema_version,
        "rules_hash": version.rules_hash, "fields": definition.model_dump()["fields"],
        "can_write": user.role in {"admin", "user"} and (user.role == "admin" or dataset.owner_id == user.id)}


@router.get("")
async def list_datasets(session: Session, user: CurrentUser,
    page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=100)):
    total = await session.scalar(select(func.count(Dataset.id)))
    rows = (await session.scalars(select(Dataset).order_by(Dataset.id)
        .offset((page - 1) * page_size).limit(page_size))).all()
    return {"items": [await dataset_output(session, row, user) for row in rows],
            "total": total, "page": page, "page_size": page_size}


@router.post("", status_code=201)
async def create_dataset(data: DatasetCreate, session: Session, user: CurrentUser):
    require_writer(user)
    definition = DatasetDefinition(fields=data.fields)
    dataset = Dataset(owner_id=user.id, name=data.name, description=data.description)
    session.add(dataset)
    await session.flush()
    version = DatasetSchemaVersion(dataset_id=dataset.id, number=1,
        definition=definition.model_dump(), rules_hash=definition.digest())
    session.add(version)
    audit(session, user, "dataset.create", dataset.id)
    await session.commit()
    return await dataset_output(session, dataset, user)


@router.get("/{dataset_id}")
async def read_dataset(dataset_id: int, session: Session, user: CurrentUser):
    return await dataset_output(session, await find_dataset(session, dataset_id, user), user)


@router.post("/{dataset_id}/fields", status_code=201)
async def append_fields(dataset_id: int, data: AppendFields, session: Session, user: CurrentUser):
    dataset = await find_dataset(session, dataset_id, user, write=True)
    if data.schema_version != dataset.schema_version:
        raise HTTPException(409, "Dataset schema changed; reload before adding fields")
    _, previous = await schema_at(session, dataset)
    # Only append: no replace, rename, reorder or delete endpoint exists.
    if {field.name for field in previous.fields} & {field.name for field in data.fields}:
        raise HTTPException(422, "Existing fields cannot be changed or repeated; only append new names")
    if len(previous.fields) + len(data.fields) > 100:
        raise HTTPException(422, "Dataset cannot exceed 100 fields")
    definition = DatasetDefinition(fields=[*previous.fields, *data.fields])
    dataset.schema_version += 1
    session.add(DatasetSchemaVersion(dataset_id=dataset.id, number=dataset.schema_version,
        definition=definition.model_dump(), rules_hash=definition.digest()))
    audit(session, user, "dataset.fields.append", dataset.id)
    await session.commit()
    return await dataset_output(session, dataset, user)


def record_output(record, number):
    return {"id": record.id, "dataset_id": record.dataset_id, "schema_version": number,
        "owner_id": record.owner_id, "source": record.source, "import_batch_id": record.import_batch_id,
        "created_at": record.created_at.replace(tzinfo=timezone.utc).isoformat(),
        "data": decode_payload(record.payload),
        "value_types": {key: value["kind"] for key, value in record.payload.items()}}


async def check_unique_values(session, dataset_id, definition, payloads):
    claims = []
    seen = set()
    duplicate_rows = []
    for row_number, payload in enumerate(payloads, 1):
        for field in definition.fields:
            if not field.unique or field.name not in payload or payload[field.name]["kind"] == "null":
                continue
            value_hash = unique_value_hash(payload[field.name])
            key = (field.name, value_hash)
            if key in seen:
                duplicate_rows.append({"row": row_number, "field": field.name,
                                       "message": "Duplicate unique value in this request"})
            seen.add(key)
            claims.append((row_number, field.name, value_hash))
    if duplicate_rows:
        raise HTTPException(422, {"message": "No rows saved", "duplicates": duplicate_rows[:50]})
    for field_name in {name for _, name, _ in claims}:
        hashes = list({value_hash for _, name, value_hash in claims if name == field_name})
        for start in range(0, len(hashes), 500):
            existing = (await session.scalars(select(DatasetUniqueValue.value_hash).where(
                DatasetUniqueValue.dataset_id == dataset_id,
                DatasetUniqueValue.field_name == field_name,
                DatasetUniqueValue.value_hash.in_(hashes[start:start + 500])))).all()
            if existing:
                raise HTTPException(409, {"field": field_name,
                    "message": "Unique value already exists in this dataset"})
    return claims


@router.post("/{dataset_id}/records", status_code=201)
async def create_record(dataset_id: int, data: ManualRecord, request: Request, session: Session, user: CurrentUser):
    dataset = await find_dataset(session, dataset_id, user, write=True)
    if data.schema_version != dataset.schema_version:
        raise HTTPException(409, "Dataset schema changed; reload the form")
    version, definition = await schema_at(session, dataset)
    try:
        # Parse raw JSON again so dynamic decimal values never pass through float.
        raw = json.loads(await request.body(), parse_float=Decimal, object_pairs_hook=no_duplicates,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        parsed = ManualRecord.model_validate(raw)
    except (ValueError, ValidationError, InvalidOperation, RecursionError):
        raise HTTPException(422, "Invalid record JSON") from None
    payload = validate_rows([parsed.data], definition, "manual")[0]
    claims = await check_unique_values(session, dataset.id, definition, [payload])
    record = DatasetRecord(dataset_id=dataset.id, schema_version_id=version.id, owner_id=user.id,
                           payload=payload, source="manual")
    session.add(record)
    await session.flush()
    session.add_all([DatasetUniqueValue(dataset_id=dataset.id, record_id=record.id,
        field_name=name, value_hash=value_hash) for _, name, value_hash in claims])
    from app.monitors import record_alerts
    await record_alerts(session, dataset.id, [record])
    audit(session, user, "dataset.record.create", record.id)
    await session.commit()
    return record_output(record, version.number)


@router.get("/{dataset_id}/records")
async def list_records(dataset_id: int, session: Session, user: CurrentUser,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=20),
    sort_by: str | None = None, sort_dir: Literal["asc", "desc"] = "asc"):
    dataset = await find_dataset(session, dataset_id, user)
    _, definition = await schema_at(session, dataset)
    total = await session.scalar(select(func.count(DatasetRecord.id)).where(DatasetRecord.dataset_id == dataset_id))
    order = [DatasetRecord.id.asc()]
    if sort_by:
        order = []
        if sort_by == "__record_id":
            sort_column = DatasetRecord.id
        elif sort_by == "__schema_version":
            sort_column = DatasetSchemaVersion.number
        else:
            field = next((field for field in definition.fields if field.name == sort_by), None)
            if field is None:
                raise HTTPException(422, "Unknown dataset sort field")
            path = '$.' + json.dumps(sort_by, ensure_ascii=False)
            kind = func.json_unquote(func.json_extract(DatasetRecord.payload, path + '."kind"'))
            value = func.json_unquote(func.json_extract(DatasetRecord.payload, path + '."value"'))
            null_last = case((kind.is_(None), 1), (kind == "null", 1), else_=0)
            order = [null_last.asc()]
            if field.data_type in {"integer", "decimal"}:
                sort_column = case((null_last == 1, None),
                                   else_=cast(value, DECIMAL(50, 12)))
            elif field.data_type is None:
                rank = case((kind.in_(["integer", "decimal"]), 0), (kind == "boolean", 1),
                            (kind == "datetime", 2), (kind == "string", 3), else_=4)
                order.append(rank.asc() if sort_dir == "asc" else rank.desc())
                numeric = case((null_last == 1, None),
                               else_=cast(value, DECIMAL(50, 12)))
                order.append(numeric.asc() if sort_dir == "asc" else numeric.desc())
                sort_column = case((null_last == 1, None), else_=value)
            else:
                sort_column = case((null_last == 1, None), else_=value)
        order.append(sort_column.asc() if sort_dir == "asc" else sort_column.desc())
        order.append(DatasetRecord.id.asc())
    rows = (await session.execute(select(DatasetRecord, DatasetSchemaVersion.number)
        .join(DatasetSchemaVersion, DatasetRecord.schema_version_id == DatasetSchemaVersion.id)
        .where(DatasetRecord.dataset_id == dataset_id).order_by(*order)
        .offset((page - 1) * page_size).limit(page_size))).all()
    return {"items": [record_output(record, number) for record, number in rows], "total": total,
        "page": page, "page_size": page_size, "dataset": await dataset_output(session, dataset, user)}


@router.delete("/{dataset_id}/records/{record_id}", status_code=204)
async def delete_record(dataset_id: int, record_id: int, session: Session, user: CurrentUser):
    dataset = await find_dataset(session, dataset_id, user, write=True)
    record = await session.scalar(select(DatasetRecord).where(
        DatasetRecord.id == record_id, DatasetRecord.dataset_id == dataset.id).with_for_update())
    if record is None:
        raise HTTPException(404, "Dataset record not found")
    require_owner(user, record.owner_id)
    unique_values = (await session.scalars(select(DatasetUniqueValue).where(
        DatasetUniqueValue.record_id == record_id))).all()
    for value in unique_values:
        await session.delete(value)
    for alert in (await session.scalars(select(DatasetMonitorAlert).where(
        DatasetMonitorAlert.record_id == record_id))).all():
        await session.delete(alert)
    await session.flush()
    await session.delete(record)
    audit(session, user, "dataset.record.delete", record_id)
    await session.commit()
    return Response(status_code=204)


@router.post("/{dataset_id}/imports", status_code=201)
async def import_records(dataset_id: int, session: Session, user: CurrentUser,
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Header(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")],
    schema_version: int = Query(..., ge=1), preview: bool = False,
    skip_first_row: bool = True):
    dataset = await find_dataset(session, dataset_id, user, write=True)
    version, definition = await schema_at(session, dataset, schema_version)
    try:
        content = await file.read(2 * 1024 * 1024 + 1)
    finally:
        await file.close()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(413, "File exceeds 2 MiB")
    kind = (file.filename or "").lower().rsplit(".", 1)[-1]
    if kind not in {"csv", "json"}:
        raise HTTPException(422, "Only CSV and JSON files are supported")
    fingerprint = hashlib.sha256(json.dumps([dataset.id, version.number, version.rules_hash, kind,
        skip_first_row if kind == "csv" else None,
        hashlib.sha256(content).hexdigest()], separators=(",", ":")).encode()).hexdigest()
    if not preview:
        previous = await session.scalar(select(DatasetImportBatch).where(
            DatasetImportBatch.owner_id == user.id, DatasetImportBatch.idempotency_key == idempotency_key).with_for_update())
        if previous:
            if previous.fingerprint != fingerprint:
                raise HTTPException(409, "Idempotency key already used for different dataset, schema or file")
            return {"batch_id": previous.id, "row_count": previous.row_count, "replayed": True, "preview": False}
    if schema_version != dataset.schema_version:
        raise HTTPException(409, "Dataset schema changed; select it again and repeat preview")
    rows = parse_source(content, file.filename or "", SimpleNamespace(file_format=kind),
        csv_fieldnames=[field.name for field in definition.fields],
        skip_first_row=skip_first_row if kind == "csv" else True)
    payloads = validate_rows(rows, definition, kind)
    claims = await check_unique_values(session, dataset.id, definition, payloads)
    if preview:
        return {"preview": True, "row_count": len(rows), "schema_version": version.number,
                "fingerprint": fingerprint, "normalized_sample": [decode_payload(p) for p in payloads[:5]]}
    batch = DatasetImportBatch(dataset_id=dataset.id, schema_version_id=version.id, owner_id=user.id,
        idempotency_key=idempotency_key, fingerprint=fingerprint, row_count=len(rows), schema_snapshot=version.definition)
    session.add(batch)
    await session.flush()
    records = [DatasetRecord(dataset_id=dataset.id, schema_version_id=version.id, owner_id=user.id,
        import_batch_id=batch.id, payload=payload, source="import") for payload in payloads]
    session.add_all(records)
    await session.flush()
    session.add_all([DatasetUniqueValue(dataset_id=dataset.id, record_id=records[index - 1].id,
        field_name=name, value_hash=value_hash) for index, name, value_hash in claims])
    from app.monitors import record_alerts
    await record_alerts(session, dataset.id, records)
    audit(session, user, "dataset.import", batch.id)
    await session.commit()
    return {"preview": False, "batch_id": batch.id, "row_count": len(rows), "schema_version": version.number, "replayed": False}
