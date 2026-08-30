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


def schema_path(name: str) -> Path:
    """A SQL schema shipped with backend, or a loud failure naming it.

    Four call sites used to locate this independently, each as
    `Path(__file__).parents[3] / "backend/sql/..."` -- walking up to the repo
    root and back down through a directory named `backend`. That is correct
    only while backend's parent is a checkout and backend keeps its name;
    rename the deploy directory and every one of them silently misses.

    Two of them then guarded the miss with `if schema_path.exists()` and
    returned quietly, which is the worse half: the database gets created with
    no tables at all, and the failure surfaces later as a foreign-key error in
    start_run() -- far from the cause, and reading like a data problem rather
    than a packaging one.
    """
    p = backend_root() / "sql" / name
    if not p.exists():
        raise FileNotFoundError(
            f"SQL schema missing: {p}. It ships inside backend/sql/, so this "
            "is a packaging fault, not a configuration one.")
    return p


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
