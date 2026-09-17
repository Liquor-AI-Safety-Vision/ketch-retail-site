"""Request/response shapes for the owner-facing dashboard API.

Deliberately separate from backend/src/domain/models.py: those dataclasses
describe pipeline-internal state (frames, tracks) and are not JSON-safe or
owner-scoped. These are Pydantic models because FastAPI validates/serializes
against them directly, and nothing pipeline-side ever imports this module.

Uses typing.Optional/List rather than the `X | None` syntax the rest of the
backend uses under `from __future__ import annotations`: that syntax only
defers evaluation of the *outer* annotation, and Pydantic still evals it at
class-definition time to build the schema, which throws on Python 3.9 --
found by actually importing this module under 3.9 before deploy, since the
API server's host is not guaranteed to be the same Python version as the
GPU box the pipeline runs on.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class StoreOut(BaseModel):
    store_id: str
    store_code: Optional[str]
    display_name: str
    timezone: str


class CameraOut(BaseModel):
    camera_id: str
    store_id: str
    camera_code: Optional[str]
    display_name: str
    camera_type: str
    is_active: bool


class AlertMediaOut(BaseModel):
    media_id: str
    kind: str
    url: Optional[str]
    mime_type: Optional[str]
    started_at: Optional[str]
    ended_at: Optional[str]


class AlertOut(BaseModel):
    alert_id: str
    store_id: str
    camera_id: str
    event_type: str
    severity: str
    status: str
    opened_at: str
    closed_at: Optional[str]
    identity_label: Optional[str]
    reason_codes: List[str]
    media: List[AlertMediaOut]
