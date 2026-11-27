"""The AI service's credentials file: ai/.env (DATABASE_URL, R2_*).

Each service keeps its own .env beside its code -- ai/.env, backend/.env,
frontend/.env. Before the monorepo the AI service read <repo>/.env; that file
is still read as a fallback so a box deployed with the old layout keeps
working, but ai/.env wins on conflict, and the real process environment wins
over both (an operator's shell export always overrides a file).
"""
from __future__ import annotations

import os
from pathlib import Path

from ai.src.common.paths import ai_root


def env_files() -> list[Path]:
    """In precedence order."""
    return [ai_root() / ".env", ai_root().parent / ".env"]


def load_dotenv(path: Path) -> None:
    """No python-dotenv dependency for KEY=VALUE lines without quoting."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def load_ai_env() -> None:
    for path in env_files():
        load_dotenv(path)
