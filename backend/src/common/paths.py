"""Where backend's own files live, and where the footage lives.

Two roots, deliberately separate, because they move independently:

    backend_root()  code, models, config, sql -- everything that ships.
                    Always derived from __file__, never from an env var and
                    never from the caller's cwd, so it is correct no matter
                    where backend was unpacked or how it was invoked.

    data_root()     videos and datasets -- 12 GB that is not part of the
                    shipping unit. Env-overridable because on a GPU box the
                    footage is usually on a different volume from the code.

Config files address them as ${BACKEND_ROOT} and ${DATA_ROOT}. ${REPO_ROOT}
is still accepted and maps to data_root(): every place it appeared in a
manifest was a video path, and the benchmark write-ups quote those manifests.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def backend_root() -> Path:
    """The backend/ directory itself. backend/src/common/paths.py -> backend/."""
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def data_root() -> Path:
    """Footage and datasets. Override with LIQUIR_DATA_ROOT or DATA_ROOT."""
    override = os.environ.get("LIQUIR_DATA_ROOT") or os.environ.get("DATA_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (backend_root().parent / "data").resolve()


def outputs_dir() -> Path:
    """Scratch space for run artefacts: bench logs, frame dumps, regression records.

    Never inside backend/. These are outputs of running the code, not part of
    it, and a deployment writes them wherever its operator says. The default
    points at the repo's dev/ area so a tool run from a checkout lands beside
    the other dev artefacts instead of inventing a new top-level directory.
    """
    override = os.environ.get("LIQUIR_OUTPUT_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (backend_root().parent / "dev" / "outputs").resolve()


def models_dir() -> Path:
    return backend_root() / "models"


def config_dir() -> Path:
    return backend_root() / "config"


def zones_dir() -> Path:
    return config_dir() / "zones"


def model_path(name: str) -> str:
    """Resolve a model by bare filename. Absolute paths pass through."""
    p = Path(name)
    return str(p if p.is_absolute() else models_dir() / p.name)


def resolve_repo_path(value: str | Path) -> str:
    """Expand config placeholders and make the result absolute.

    A bare relative path resolves against data_root(), not the cwd: the only
    relative paths left in config are footage, and resolving those against
    whatever directory the process happened to start in is how a benchmark
    silently reads the wrong file.
    """
    raw = str(value)
    raw = raw.replace("${BACKEND_ROOT}", str(backend_root()))
    raw = raw.replace("${DATA_ROOT}", str(data_root()))
    raw = raw.replace("${REPO_ROOT}", str(data_root()))
    path = Path(os.path.expandvars(raw)).expanduser()
    if not path.is_absolute():
        path = data_root() / path
    return str(path.resolve())


# Kept so callers that only ever wanted "the directory above backend" still
# have a name for it. New code should ask for backend_root() or data_root(),
# whichever it actually means -- those two used to be the same directory,
# which is precisely why model paths and video paths got tangled together.
def repo_root() -> Path:
    return backend_root().parent
