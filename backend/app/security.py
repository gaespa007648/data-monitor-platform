from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from app.config import get_settings

password_hash = PasswordHash.recommended()
# Equalize hashing work when an unknown account attempts to log in.
dummy_hash = password_hash.hash("unused-dummy-password")
ACCESS_TOKEN_EXPIRE_SECONDS = 60 * 60


def create_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": str(user_id), "iat": now, "exp": now + timedelta(seconds=ACCESS_TOKEN_EXPIRE_SECONDS),
         "iss": "data-monitor-platform", "aud": "data-monitor-api"},
        get_settings().jwt_secret.get_secret_value(), algorithm="HS256",
    )


def decode_token(token: str) -> dict:
    return jwt.decode(
        token, get_settings().jwt_secret.get_secret_value(), algorithms=["HS256"],
        issuer="data-monitor-platform", audience="data-monitor-api",
        options={"require": ["sub", "iat", "exp", "iss", "aud"]},
    )
