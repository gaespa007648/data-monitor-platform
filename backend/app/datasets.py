import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Header, HTTPException, Query, Request, Response, UploadFile
from pydantic import Field, ValidationError, model_validator
from sqlalchemy import func, select

from app.dataset_rules import DatasetDefinition, DatasetField, decode_payload, validate_rows
from app.dependencies import CurrentUser, Session, require_owner, require_writer
from app.import_rules import no_duplicates, parse_source
from app.models import (AuditLog, Dataset, DatasetImportBatch, DatasetRecord, DatasetSchemaVersion, User)
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
    record = DatasetRecord(dataset_id=dataset.id, schema_version_id=version.id, owner_id=user.id,
                           payload=payload, source="manual")
    session.add(record)
    await session.flush()
    audit(session, user, "dataset.record.create", record.id)
    await session.commit()
    return record_output(record, version.number)


@router.get("/{dataset_id}/records")
async def list_records(dataset_id: int, session: Session, user: CurrentUser,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=20)):
    dataset = await find_dataset(session, dataset_id, user)
    total = await session.scalar(select(func.count(DatasetRecord.id)).where(DatasetRecord.dataset_id == dataset_id))
    rows = (await session.execute(select(DatasetRecord, DatasetSchemaVersion.number)
        .join(DatasetSchemaVersion, DatasetRecord.schema_version_id == DatasetSchemaVersion.id)
        .where(DatasetRecord.dataset_id == dataset_id).order_by(DatasetRecord.id)
        .offset((page - 1) * page_size).limit(page_size))).all()
    return {"items": [record_output(record, number) for record, number in rows], "total": total,
        "page": page, "page_size": page_size, "dataset": await dataset_output(session, dataset, user)}


@router.get("/{dataset_id}/sample")
async def sample(dataset_id: int, session: Session, user: CurrentUser,
                 file_format: Literal["csv", "json"] = "csv"):
    dataset = await find_dataset(session, dataset_id, user)
    version, definition = await schema_at(session, dataset)
    samples = {None: "sample", "string": "sample", "integer": 1, "decimal": 1,
               "datetime": datetime.now(timezone.utc).isoformat(), "boolean": True}
    row = {field.name: samples[field.data_type] for field in definition.fields}
    if file_format == "csv":
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow({k: str(v).lower() if type(v) is bool else v for k, v in row.items()})
        content = stream.getvalue().encode("utf-8-sig")
    else:
        content = json.dumps([row], ensure_ascii=False, indent=2).encode()
    return Response(content, media_type="text/csv" if file_format == "csv" else "application/json",
        headers={"Content-Disposition": f'attachment; filename="dataset-{dataset.id}-v{version.number}.{file_format}"'})


@router.post("/{dataset_id}/imports", status_code=201)
async def import_records(dataset_id: int, session: Session, user: CurrentUser,
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Header(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")],
    schema_version: int = Query(..., ge=1), preview: bool = False):
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
    rows = parse_source(content, file.filename or "", SimpleNamespace(file_format=kind))
    payloads = validate_rows(rows, definition, kind)
    if preview:
        return {"preview": True, "row_count": len(rows), "schema_version": version.number,
                "fingerprint": fingerprint, "normalized_sample": [decode_payload(p) for p in payloads[:5]]}
    batch = DatasetImportBatch(dataset_id=dataset.id, schema_version_id=version.id, owner_id=user.id,
        idempotency_key=idempotency_key, fingerprint=fingerprint, row_count=len(rows), schema_snapshot=version.definition)
    session.add(batch)
    await session.flush()
    session.add_all([DatasetRecord(dataset_id=dataset.id, schema_version_id=version.id, owner_id=user.id,
        import_batch_id=batch.id, payload=payload, source="import") for payload in payloads])
    audit(session, user, "dataset.import", batch.id)
    await session.commit()
    return {"preview": False, "batch_id": batch.id, "row_count": len(rows), "schema_version": version.number, "replayed": False}
