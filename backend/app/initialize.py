"""Migrate, bootstrap demo accounts, and synchronize their configured passwords."""
import asyncio
import logging

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionFactory, engine
from app.models import Category, User
from app.security import password_hash
from app.import_templates import seed_builtin_templates


async def seed_demo_users():
    settings = get_settings()
    try:
        if not settings.seed_demo_users:
            return
        accounts = [
            ("admin", "admin", settings.demo_admin_password.get_secret_value()),
            ("viewer", "viewer", settings.demo_viewer_password.get_secret_value()),
        ]
        if any(len(password) < 12 for _, _, password in accounts):
            raise ValueError("Demo passwords must contain at least 12 characters")
        async with SessionFactory.begin() as session:
            for username, role, password in accounts:
                existing = await session.scalar(select(User).where(User.username == username))
                if existing is None:
                    session.add(User(username=username, role=role,
                                     password_hash=password_hash.hash(password)))
                elif not password_hash.verify(password, existing.password_hash):
                    existing.password_hash = password_hash.hash(password)
                    logging.info("Password synchronized from configuration for account %s", username)
        logging.info("Demo account bootstrap completed; configured passwords synchronized")
    finally:
        await engine.dispose()


async def initialize():
    async with SessionFactory.begin() as session:
        for name in ["一般", "溫度", "濕度"]:
            if await session.scalar(select(Category.id).where(Category.name == name)) is None:
                session.add(Category(name=name))
        await seed_builtin_templates(session)
    await seed_demo_users()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    command.upgrade(Config("alembic.ini"), "head")
    asyncio.run(initialize())
