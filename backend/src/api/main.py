"""Owner-facing dashboard API: fastapi.

    uvicorn backend.src.api.main:app --reload

Reads DATABASE_URL/R2_* the same way run_split_pipeline.py's --storage-mode
cloud-db does (see that file's _load_dotenv), plus JWT_SECRET for token
signing. Every /v1 route requires a bearer access token and only ever
returns rows for stores the token's owner is linked to via owner_stores --
see reader.py for the join. This process never writes to alerts/alert_media;
the pipeline (AlertOutbox -> CloudAlertSink) is still the only writer.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware

from backend.src.api import security
from backend.src.api.reader import AlertReader
from backend.src.api.schemas import (
    AlertOut,
    CameraOut,
    LoginRequest,
    StoreOut,
    TokenResponse,
)

ROOT = Path(__file__).resolve().parents[3]


def _load_dotenv(path: Path) -> None:
    """Same loader as run_split_pipeline.py's _load_dotenv, duplicated rather
    than imported: importing that module pulls in onnxruntime/opencv, which
    this process -- a plain HTTP API -- has no other reason to load."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(ROOT / ".env")

_REQUIRED_ENV = ("DATABASE_URL", "R2_ENDPOINT", "R2_BUCKET",
                  "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "JWT_SECRET")
_missing = [k for k in _REQUIRED_ENV if not os.environ.get(k)]
if _missing:
    raise RuntimeError(
        f"dashboard API needs {_missing} in the environment or in {ROOT / '.env'}")


def _build_reader() -> AlertReader:
    import boto3

    s3 = boto3.client(
        "s3", endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    return AlertReader(
        dsn=os.environ["DATABASE_URL"], s3=s3, bucket=os.environ["R2_BUCKET"],
        alerts_cache_ttl_s=float(os.environ.get("ALERTS_CACHE_TTL_S", 8.0)),
        presigned_url_ttl_s=int(os.environ.get("PRESIGNED_URL_TTL_S", 3600)),
    )


reader = _build_reader()

app = FastAPI(title="Ketch Enterprise dashboard API")

# CORS_ALLOWED_ORIGINS: comma-separated, e.g. "https://ketchenterprise.net".
# No wildcard default -- credentials:"include" (cookie-based refresh) and
# Access-Control-Allow-Origin: * are mutually exclusive in every browser, so
# an unset value should fail loudly rather than silently allow nothing.
_origins = [o.strip() for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware, allow_origins=_origins, allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


@app.post("/auth/login", response_model=TokenResponse)
def login(body: LoginRequest, response: Response):
    owner = reader.get_owner_by_email(body.email)
    if owner is None or not security.verify_password(body.password, owner["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
    owner_id = owner["owner_id"]
    response.set_cookie(
        security.REFRESH_COOKIE_NAME, security.create_refresh_token(owner_id),
        httponly=True, secure=True, samesite="lax",
        max_age=security.REFRESH_TOKEN_TTL_S, path="/auth",
    )
    return TokenResponse(access_token=security.create_access_token(owner_id))


@app.post("/auth/refresh", response_model=TokenResponse)
def refresh(refresh_token: Optional[str] = Cookie(default=None, alias=security.REFRESH_COOKIE_NAME)):
    if refresh_token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no refresh cookie")
    try:
        owner_id = security.decode_refresh_token(refresh_token)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired refresh token")
    return TokenResponse(access_token=security.create_access_token(owner_id))


@app.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(security.REFRESH_COOKIE_NAME, path="/auth")
    return {"ok": True}


@app.get("/api/v1/stores", response_model=list[StoreOut])
def get_stores(owner_id: str = Depends(security.get_current_owner_id)):
    return reader.list_stores(owner_id)


@app.get("/api/v1/cameras", response_model=list[CameraOut])
def get_cameras(store_id: Optional[str] = None,
                owner_id: str = Depends(security.get_current_owner_id)):
    return reader.list_cameras(owner_id, store_id=store_id)


@app.get("/api/v1/alerts", response_model=list[AlertOut])
def get_alerts(store_id: Optional[str] = None, status_: Optional[str] = None, limit: int = 100,
               owner_id: str = Depends(security.get_current_owner_id)):
    return reader.list_alerts(owner_id, store_id=store_id, status=status_, limit=limit)
