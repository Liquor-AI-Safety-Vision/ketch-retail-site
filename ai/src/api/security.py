"""Password hashing and JWT issuance for the owner dashboard API.

Nothing in this repo touched owners/owner_stores before this API: the tables
existed in postgres_schema.sql (FK target for alerts.reviewed_by) but no
code ever wrote a password_hash or read one back. This is that first piece,
so there is no prior hashing scheme to stay compatible with.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

ACCESS_TOKEN_TTL_S = int(os.environ.get("ACCESS_TOKEN_TTL_S", 15 * 60))
REFRESH_TOKEN_TTL_S = int(os.environ.get("REFRESH_TOKEN_TTL_S", 30 * 24 * 3600))
REFRESH_COOKIE_NAME = "refresh_token"

_bearer = HTTPBearer(auto_error=False)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, password_hash: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))


def _jwt_secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError(
            "JWT_SECRET is not set; the dashboard API needs it in the "
            "environment or in .env, the same way DATABASE_URL/R2_* are loaded")
    return secret


def create_access_token(owner_id: str) -> str:
    now = int(time.time())
    payload = {
        "sub": owner_id,
        "typ": "access",
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL_S,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def decode_access_token(token: str) -> str:
    """Returns the owner_id, or raises jwt exceptions on an expired/bad token."""
    payload = jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    if payload.get("typ") != "access":
        raise jwt.InvalidTokenError("not an access token")
    return payload["sub"]


def create_refresh_token(owner_id: str) -> str:
    now = int(time.time())
    payload = {
        "sub": owner_id,
        "typ": "refresh",
        "iat": now,
        "exp": now + REFRESH_TOKEN_TTL_S,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def decode_refresh_token(token: str) -> str:
    payload = jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    if payload.get("typ") != "refresh":
        raise jwt.InvalidTokenError("not a refresh token")
    return payload["sub"]


def get_current_owner_id(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> str:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        return decode_access_token(credentials.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")
