"""Remove one explicitly named UI fixture, never application users in general."""
import asyncio
import sys

from sqlalchemy import select

from app.database import SessionFactory, engine
from app.models import (AuditLog, ImportBatch, ImportSchemaField, ImportSchemaTemplate,
                        ImportSchemaVersion, Record, User)


async def cleanup(username):
    if not username.startswith(("schemaui_", "uitest_")):
        raise ValueError("Only explicitly named generated UI fixtures may be removed")
    try:
        async with SessionFactory.begin() as session:
            user = await session.scalar(select(User).where(User.username == username))
            if user is None:
                return
            templates = select(ImportSchemaTemplate.id).where(ImportSchemaTemplate.owner_id == user.id)
            versions = select(ImportSchemaVersion.id).where(ImportSchemaVersion.template_id.in_(templates))
            for model, condition in [
                (Record, Record.owner_id == user.id), (ImportBatch, ImportBatch.owner_id == user.id),
                (ImportSchemaField, ImportSchemaField.version_id.in_(versions)),
                (ImportSchemaVersion, ImportSchemaVersion.template_id.in_(templates)),
                (ImportSchemaTemplate, ImportSchemaTemplate.owner_id == user.id),
                (AuditLog, AuditLog.actor_id == user.id)]:
                for row in (await session.scalars(select(model).where(condition))).all():
                    await session.delete(row)
                await session.flush()
            await session.delete(user)
        print("UI fixture removed")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(cleanup(sys.argv[1]))
