import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import SecretStr

from app.initialize import seed_demo_users
from app.models import User
from app.security import password_hash


class SeedTests(unittest.IsolatedAsyncioTestCase):
    async def run_seed(self, admin, viewer, enabled=True):
        settings = SimpleNamespace(
            seed_demo_users=enabled,
            demo_admin_password=SecretStr("new-admin-password"),
            demo_viewer_password=SecretStr("new-viewer-password"),
        )
        session = MagicMock()
        session.scalar = AsyncMock(side_effect=[admin, viewer])
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=session)
        context.__aexit__ = AsyncMock(return_value=False)
        factory = MagicMock()
        factory.begin.return_value = context
        with patch("app.initialize.get_settings", return_value=settings), \
             patch("app.initialize.SessionFactory", factory), \
             patch("app.initialize.engine", SimpleNamespace(dispose=AsyncMock())):
            await seed_demo_users()
        return session, factory

    def user(self, username, password):
        return User(id=123, username=username, role="viewer", is_active=False,
                    password_hash=password_hash.hash(password))

    async def test_sync_both_passwords_and_preserve_other_fields(self):
        admin = self.user("admin", "old-admin-password")
        viewer = self.user("viewer", "old-viewer-password")
        session, _ = await self.run_seed(admin, viewer)
        self.assertTrue(password_hash.verify("new-admin-password", admin.password_hash))
        self.assertFalse(password_hash.verify("old-admin-password", admin.password_hash))
        self.assertEqual((admin.id, admin.role, admin.is_active), (123, "viewer", False))
        self.assertTrue(password_hash.verify("new-viewer-password", viewer.password_hash))
        self.assertFalse(password_hash.verify("old-viewer-password", viewer.password_hash))
        self.assertEqual((viewer.id, viewer.role, viewer.is_active), (123, "viewer", False))
        session.add.assert_not_called()

    async def test_unchanged_password_does_not_rehash(self):
        admin = self.user("admin", "new-admin-password")
        viewer = self.user("viewer", "new-viewer-password")
        originals = (admin.password_hash, viewer.password_hash)
        await self.run_seed(admin, viewer)
        self.assertEqual((admin.password_hash, viewer.password_hash), originals)

    async def test_create_missing_accounts(self):
        session, _ = await self.run_seed(None, None)
        users = [call.args[0] for call in session.add.call_args_list]
        self.assertEqual([user.username for user in users], ["admin", "viewer"])
        self.assertTrue(password_hash.verify("new-admin-password", users[0].password_hash))
        self.assertTrue(password_hash.verify("new-viewer-password", users[1].password_hash))

    async def test_disabled_seed_does_not_access_database(self):
        _, factory = await self.run_seed(None, None, enabled=False)
        factory.begin.assert_not_called()
