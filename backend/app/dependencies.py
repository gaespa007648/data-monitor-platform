from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import User
from app.security import decode_token

Session = Annotated[AsyncSession, Depends(get_session)]
bearer = HTTPBearer(auto_error=False)


async def current_user(session: Session,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> User:
    error = HTTPException(401, "Invalid or expired credentials", headers={"WWW-Authenticate": "Bearer"})
    if credentials is None:
        raise error
    try:
        user_id = int(decode_token(credentials.credentials)["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
        raise error from None
    user = await session.get(User, user_id)
    if user is None or not user.is_active or user.role not in {"admin", "user", "viewer"}:
        raise error
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_writer(user: User):
    if user.role not in {"admin", "user"}:
        raise HTTPException(403, "Write permission required")


def require_owner(user: User, owner_id: int):
    require_writer(user)
    if user.role != "admin" and user.id != owner_id:
        raise HTTPException(403, "Only the creator or an administrator may modify this record")


def require_admin(user: User):
    if user.role != "admin":
        raise HTTPException(403, "Administrator permission required")
