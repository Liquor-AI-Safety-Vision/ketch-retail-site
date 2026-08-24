from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    override = os.environ.get("LIQUIR_STORE_ROOT") or os.environ.get("REPO_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[3]


def resolve_repo_path(value: str | Path) -> str:
    raw = str(value)
    root = repo_root()
    if "${REPO_ROOT}" in raw:
        raw = raw.replace("${REPO_ROOT}", str(root))
    expanded = os.path.expandvars(raw)
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        path = root / path
    return str(path.resolve())
