import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm.exc import StaleDataError
from starlette.concurrency import run_in_threadpool

from app.database import engine
from app.dependencies import CurrentUser, Session, require_admin
from app.models import AuditLog, User
from app.rate_limit import limit
from app.schemas import Registration
from app.security import create_token, dummy_hash, password_hash
from app.records import router as records_router
from app.import_templates import router as schemas_router
from app.datasets import router as datasets_router
from app.request_limits import RequestBodyLimit

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(title="Data Monitor Platform API", version="0.2.0", lifespan=lifespan)
app.add_middleware(RequestBodyLimit)
app.include_router(records_router)
app.include_router(schemas_router)
app.include_router(datasets_router)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
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


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Pydantic's default errors include raw input, which can contain passwords.
    errors = [{"loc": error["loc"], "type": error["type"], "msg": error["msg"]}
              for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.exception_handler(StaleDataError)
async def stale_error(request: Request, exc: StaleDataError):
    return JSONResponse(status_code=409, content={"detail": "Record changed; reload before retrying"})


@app.exception_handler(IntegrityError)
async def integrity_error(request: Request, exc: IntegrityError):
    return JSONResponse(status_code=409, content={"detail": "Conflicting or invalid related data"})


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError):
    logger.error("Database operation failed: %s", type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Database temporarily unavailable"})


@app.exception_handler(OperationalError)
async def operation_error(request: Request, exc: OperationalError):
    code = exc.orig.args[0] if getattr(exc.orig, "args", ()) else None
    logger.error("Database operational error code=%r", code)
    if code in {1020, 1205, 1213}:
        return JSONResponse(status_code=409, content={"detail": "Concurrent transaction conflict; reload and retry"})
    return await database_error(request, exc)


@app.get("/health/live", tags=["health"])
async def live():
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def ready(session: Session):
    from app.models import Dataset, Record
    await session.scalar(select(Record.id).limit(1))
    await session.scalar(select(Dataset.id).limit(1))
    return {"status": "ok", "database": "connected"}


@app.post("/api/v1/users", response_model=UserResponse, status_code=201, tags=["users"])
async def register(data: Registration, request: Request, session: Session):
    limit(request, "register", 10)
    if await session.scalar(select(User.id).where(User.username == data.username)) is not None:
        raise HTTPException(409, "Username already exists")
    hashed = await run_in_threadpool(password_hash.hash, data.password)
    user = User(username=data.username, password_hash=hashed, role="user", is_active=True)
    session.add(user)
    try:
        await session.flush()
        session.add(AuditLog(actor_id=user.id, action="user.register", resource_id=user.id))
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "Username already exists") from None
    return user


@app.post("/api/v1/auth/tokens", response_model=TokenResponse, tags=["auth"])
async def login(data: LoginRequest, request: Request, session: Session):
    limit(request, "login", 30)
    user = await session.scalar(select(User).where(User.username == data.username.lower()))
    valid = await run_in_threadpool(
        password_hash.verify, data.password, user.password_hash if user else dummy_hash
    )
    if not valid or user is None or not user.is_active:
        raise HTTPException(401, "Invalid username or password")
    return TokenResponse(access_token=create_token(user.id))


@app.get("/api/v1/users/me", response_model=UserResponse, tags=["users"])
async def me(user: CurrentUser):
    return user


@app.get("/api/v1/users", response_model=list[UserResponse], tags=["users"])
async def list_users(session: Session, user: CurrentUser, page: int = 1, page_size: int = 50):
    require_admin(user)
    if page < 1 or not 1 <= page_size <= 100:
        raise HTTPException(422, "Invalid page or page_size")
    return (await session.scalars(select(User).order_by(User.id)
        .offset((page - 1) * page_size).limit(page_size))).all()
