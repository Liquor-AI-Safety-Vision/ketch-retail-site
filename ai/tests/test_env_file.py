"""ai/.env is the AI service's own file; <repo>/.env is only a fallback."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ai.src.common import env_file  # noqa: E402
from ai.src.common.paths import ai_root  # noqa: E402


def test_ai_env_is_read_first_then_repo_root():
    assert env_file.env_files() == [ai_root() / ".env", ai_root().parent / ".env"]


def test_precedence_process_env_then_ai_then_root():
    with tempfile.TemporaryDirectory() as tmp:
        ai_env, root_env = Path(tmp) / "ai.env", Path(tmp) / "root.env"
        ai_env.write_text("# comment\nT_SHARED=from-ai\nT_AI_ONLY=a\n\nnot a pair\n")
        root_env.write_text("T_SHARED=from-root\nT_ROOT_ONLY=r\nT_SHELL=from-root\n")
        keys = ("T_SHARED", "T_AI_ONLY", "T_ROOT_ONLY", "T_SHELL")
        with mock.patch.dict(os.environ, {"T_SHELL": "from-shell"}), \
                mock.patch.object(env_file, "env_files", return_value=[ai_env, root_env]):
            for k in keys[:3]:
                os.environ.pop(k, None)
            env_file.load_ai_env()
            got = {k: os.environ.get(k) for k in keys}
        assert got == {"T_SHARED": "from-ai", "T_AI_ONLY": "a", "T_ROOT_ONLY": "r", "T_SHELL": "from-shell"}, got


def test_missing_files_are_ignored():
    with mock.patch.object(env_file, "env_files", return_value=[Path("/nonexistent/.env")]):
        env_file.load_ai_env()


def test_env_files_are_gitignored_and_example_lives_in_ai():
    repo = ai_root().parent
    assert (ai_root() / ".env.example").exists()
    assert not (repo / ".env.example").exists()
    ignore = (repo / ".gitignore").read_text().splitlines()
    assert ".env" in ignore and "!.env.example" in ignore


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("all passed")
