import csv
import io
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import Field, model_validator
from sqlalchemy import or_, select

from app.dependencies import CurrentUser, Session, require_writer
from app.import_rules import VersionRules, default_rules
from app.models import (AuditLog, Category, ImportBatch, ImportSchemaField,
                        ImportSchemaTemplate, ImportSchemaVersion, User)
from app.schemas import StrictModel

router = APIRouter(prefix="/api/v1/import-schemas", tags=["import schemas"])


class TemplateCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    visibility: Literal["private", "shared"] = "private"
    rules: VersionRules


class TemplatePatch(StrictModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    is_active: bool | None = None

    @model_validator(mode="after")
    def changed(self):
        if not self.model_fields_set or any(getattr(self, k) is None for k in self.model_fields_set):
            raise ValueError("Provide non-null changed fields")
        return self


class Activation(StrictModel):
    is_active: bool


def log(session, user, action, resource_id):
    session.add(AuditLog(actor_id=user.id, action=action, resource_id=resource_id))


async def get_template(session, template_id, user, write=False, lock=False):
    if lock:
        await session.scalar(select(User.id).where(User.id == user.id).with_for_update())
    query = select(ImportSchemaTemplate).where(ImportSchemaTemplate.id == template_id)
    if user.role != "admin":
        query = query.where(or_(ImportSchemaTemplate.visibility == "shared", ImportSchemaTemplate.owner_id == user.id))
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    template = await session.scalar(query)
    if template is None:
        raise HTTPException(404, "Schema template not found")
    if write:
        require_writer(user)
        if user.role != "admin" and (template.owner_id != user.id or template.visibility == "shared"):
            raise HTTPException(403, "Only an administrator may edit shared templates")
    return template


async def get_version(session, template_id, version_id, lock=False):
    query = select(ImportSchemaVersion).where(ImportSchemaVersion.id == version_id,
                                            ImportSchemaVersion.template_id == template_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    version = await session.scalar(query)
    if version is None:
        raise HTTPException(404, "Schema version not found")
    return version


async def version_rules(session, version):
    fields = (await session.scalars(select(ImportSchemaField).where(
        ImportSchemaField.version_id == version.id).order_by(ImportSchemaField.position))).all()
    return VersionRules(file_format=version.file_format, extra_policy=version.extra_policy,
        trim_whitespace=version.trim_whitespace, fields=[{
            key: getattr(field, key) for key in ("source_field", "target_field", "data_type",
                "allow_numeric_string", "max_length", "max_digits", "decimal_places")} for field in fields])


async def version_output(session, version):
    rules = await version_rules(session, version)
    return {"id": version.id, "template_id": version.template_id, "number": version.number,
        "status": version.status, "is_active": version.is_active,
        "rules_hash": version.rules_hash, "rules": rules.model_dump(mode="json")}


async def store_rules(session, version, rules):
    version.file_format, version.extra_policy = rules.file_format, rules.extra_policy
    version.trim_whitespace, version.rules_hash = rules.trim_whitespace, rules.digest()
    for field in (await session.scalars(select(ImportSchemaField).where(ImportSchemaField.version_id == version.id))).all():
        await session.delete(field)
    await session.flush()
    session.add_all([ImportSchemaField(version_id=version.id, position=i, **field.model_dump())
                     for i, field in enumerate(rules.fields)])
    await session.flush()


async def add_version(session, template, rules, status="draft"):
    previous = await session.scalar(select(ImportSchemaVersion.number).where(
        ImportSchemaVersion.template_id == template.id).order_by(ImportSchemaVersion.number.desc()).limit(1))
    version = ImportSchemaVersion(template_id=template.id, number=(previous or 0) + 1,
        file_format=rules.file_format, extra_policy=rules.extra_policy,
        trim_whitespace=rules.trim_whitespace, rules_hash=rules.digest(), status=status)
    session.add(version)
    await session.flush()
    await store_rules(session, version, rules)
    return version


async def seed_builtin_templates(session):
    for kind in ("csv", "json"):
        key = f"default-{kind}"
        if await session.scalar(select(ImportSchemaTemplate.id).where(ImportSchemaTemplate.builtin_key == key)) is None:
            template = ImportSchemaTemplate(owner_id=None, name=f"內建相容範本 {kind.upper()}",
                description="固定四欄位；未指定額外型別，套用正式資料驗證", visibility="shared", builtin_key=key)
            session.add(template)
            await session.flush()
            await add_version(session, template, default_rules(kind), status="published")


@router.get("")
async def list_templates(session: Session, user: CurrentUser, page: int = Query(1, ge=1),
                         page_size: int = Query(50, ge=1, le=100)):
    query = select(ImportSchemaTemplate)
    if user.role != "admin":
        query = query.where(or_(ImportSchemaTemplate.visibility == "shared", ImportSchemaTemplate.owner_id == user.id))
    templates = (await session.scalars(query.order_by(ImportSchemaTemplate.id)
        .offset((page - 1) * page_size).limit(page_size))).all()
    return [{"id": t.id, "owner_id": t.owner_id, "name": t.name, "description": t.description,
             "visibility": t.visibility, "is_active": t.is_active, "builtin_key": t.builtin_key} for t in templates]


@router.post("", status_code=201)
async def create_template(data: TemplateCreate, session: Session, user: CurrentUser):
    require_writer(user)
    if data.visibility == "shared" and user.role != "admin":
        raise HTTPException(403, "Only administrators may create shared templates")
    template = ImportSchemaTemplate(owner_id=user.id, name=data.name, description=data.description, visibility=data.visibility)
    session.add(template)
    await session.flush()
    version = await add_version(session, template, data.rules)
    log(session, user, "schema.create", template.id)
    await session.commit()
    return {"id": template.id, "version": await version_output(session, version)}


@router.patch("/{template_id}")
async def patch_template(template_id: int, data: TemplatePatch, session: Session, user: CurrentUser):
    template = await get_template(session, template_id, user, write=True, lock=True)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(template, key, value)
    log(session, user, "schema.update", template.id)
    await session.commit()
    return {"id": template.id, "is_active": template.is_active}


@router.get("/{template_id}/versions")
async def list_versions(template_id: int, session: Session, user: CurrentUser,
                        page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100)):
    await get_template(session, template_id, user)
    versions = (await session.scalars(select(ImportSchemaVersion).where(ImportSchemaVersion.template_id == template_id)
        .order_by(ImportSchemaVersion.number.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    return [await version_output(session, version) for version in versions]


@router.post("/{template_id}/versions", status_code=201)
async def create_version(template_id: int, data: VersionRules, session: Session, user: CurrentUser):
    template = await get_template(session, template_id, user, write=True, lock=True)
    if template.builtin_key:
        raise HTTPException(409, "Clone builtin templates before changing rules")
    version = await add_version(session, template, data)
    log(session, user, "schema.version.create", version.id)
    await session.commit()
    return await version_output(session, version)


@router.get("/{template_id}/versions/{version_id}")
async def read_version(template_id: int, version_id: int, session: Session, user: CurrentUser):
    await get_template(session, template_id, user)
    return await version_output(session, await get_version(session, template_id, version_id))


@router.put("/{template_id}/versions/{version_id}")
async def edit_version(template_id: int, version_id: int, data: VersionRules, session: Session, user: CurrentUser):
    template = await get_template(session, template_id, user, write=True, lock=True)
    version = await get_version(session, template_id, version_id, lock=True)
    if version.status != "draft" or template.builtin_key:
        raise HTTPException(409, "Published rules are immutable; create a new version")
    await store_rules(session, version, data)
    log(session, user, "schema.version.update", version.id)
    await session.commit()
    return await version_output(session, version)


@router.post("/{template_id}/versions/{version_id}/publication")
async def publish_version(template_id: int, version_id: int, session: Session, user: CurrentUser):
    template = await get_template(session, template_id, user, write=True, lock=True)
    version = await get_version(session, template_id, version_id, lock=True)
    if not template.is_active or not version.is_active:
        raise HTTPException(409, "Cannot publish an inactive template or version")
    rules = await version_rules(session, version)
    if rules.digest() != version.rules_hash:
        raise HTTPException(409, "Schema rule checksum mismatch")
    version.status = "published"
    log(session, user, "schema.version.publish", version.id)
    await session.commit()
    return await version_output(session, version)


@router.patch("/{template_id}/versions/{version_id}/activation")
async def activate_version(template_id: int, version_id: int, data: Activation, session: Session, user: CurrentUser):
    await get_template(session, template_id, user, write=True, lock=True)
    version = await get_version(session, template_id, version_id, lock=True)
    version.is_active = data.is_active
    log(session, user, "schema.version.activate", version.id)
    await session.commit()
    return await version_output(session, version)


@router.delete("/{template_id}/versions/{version_id}", status_code=204)
async def delete_draft(template_id: int, version_id: int, session: Session, user: CurrentUser):
    template = await get_template(session, template_id, user, write=True, lock=True)
    version = await get_version(session, template_id, version_id, lock=True)
    used = await session.scalar(select(ImportBatch.id).where(ImportBatch.schema_version_id == version.id).limit(1))
    if version.status != "draft" or template.builtin_key or used is not None:
        raise HTTPException(409, "Only unreferenced drafts may be deleted; deactivate published versions")
    for field in (await session.scalars(select(ImportSchemaField).where(ImportSchemaField.version_id == version.id))).all():
        await session.delete(field)
    await session.flush()
    await session.delete(version)
    log(session, user, "schema.version.delete", version_id)
    await session.commit()
    return Response(status_code=204)


@router.get("/{template_id}/versions/{version_id}/sample")
async def sample_file(template_id: int, version_id: int, session: Session, user: CurrentUser):
    template = await get_template(session, template_id, user)
    version = await get_version(session, template_id, version_id)
    if not template.is_active or not version.is_active or version.status != "published":
        raise HTTPException(409, "Select an active published version")
    category = await session.scalar(select(Category.id).order_by(Category.id).limit(1))
    if category is None:
        raise HTTPException(409, "Create a category before generating a sample")
    rules = await version_rules(session, version)
    values = {"title": "範例資料", "value": 1, "category_id": category,
              "timestamp": datetime.now(timezone.utc).isoformat()}
    row = {f.source_field: (values[f.target_field][:f.max_length] if f.target_field == "title" and f.max_length
                           else values[f.target_field]) for f in rules.fields}
    if rules.file_format == "csv":
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
        content = stream.getvalue().encode("utf-8-sig")
    else:
        content = json.dumps([row], ensure_ascii=False, indent=2).encode()
    return Response(content, media_type="text/csv" if rules.file_format == "csv" else "application/json",
        headers={"Content-Disposition": f'attachment; filename="schema-{version.id}-sample.{rules.file_format}"'})
