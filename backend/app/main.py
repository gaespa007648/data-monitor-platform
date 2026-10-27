import logging
from contextlib import asynccontextmanager
from typing import Annotated

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.database import engine, get_session
from app.models import User
from app.security import create_token, decode_token, dummy_hash, password_hash

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(title="Data Monitor Platform API", version="0.1.0", lifespan=lifespan)
Session = Annotated[AsyncSession, Depends(get_session)]
bearer = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 900


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    role: str
    is_active: bool


async def current_user(
    session: Session,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    error = HTTPException(401, "Invalid or expired credentials", headers={"WWW-Authenticate": "Bearer"})
    if credentials is None:
        raise error
    try:
        user_id = int(decode_token(credentials.credentials)["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
        raise error from None
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise error
    return user


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError):
    logger.error("Database operation failed: %s", type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Database temporarily unavailable"})


@app.get("/health/live", tags=["health"])
async def live():
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def ready(session: Session):
    # ORM query also verifies that the initial migration has been applied.
    await session.scalar(select(User.id).limit(1))
    return {"status": "ok", "database": "connected"}


@app.post("/api/v1/auth/tokens", response_model=TokenResponse, tags=["auth"])
async def login(data: LoginRequest, session: Session):
    user = await session.scalar(select(User).where(User.username == data.username))
    valid = await run_in_threadpool(
        password_hash.verify, data.password, user.password_hash if user else dummy_hash
    )
    if not valid or user is None or not user.is_active:
        raise HTTPException(401, "Invalid username or password")
    return TokenResponse(access_token=create_token(user.id))


@app.get("/api/v1/users/me", response_model=UserResponse, tags=["users"])
async def me(user: Annotated[User, Depends(current_user)]):
    return user


@app.get("/api/v1/users", response_model=list[UserResponse], tags=["users"])
async def list_users(session: Session, user: Annotated[User, Depends(current_user)]):
    if user.role != "admin":
        raise HTTPException(403, "Administrator permission required")
    return (await session.scalars(select(User).order_by(User.id).limit(100))).all()
