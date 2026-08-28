"""Camera sources are either a file on disk or a network URI, and the two
cannot be handled the same way.

Both functions here exist because of a specific failure mode rather than for
symmetry: one because path resolution silently corrupts a URI, the other
because a URI silently carries a password into the database.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit, urlunsplit

from backend.src.common.paths import resolve_repo_path

# A scheme is letters followed by letters/digits/+/-/. then "://". Windows
# drive letters ("C:\...") do not match, since they have no "//".
_URI_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")


def is_network_uri(value: str | None) -> bool:
    return bool(value) and _URI_SCHEME.match(str(value)) is not None


def resolve_source_uri(value: str | None) -> str | None:
    """Resolve a camera source that may be a path OR a URI.

    resolve_repo_path() treats its argument as a filesystem path, and doing
    that to a URI destroys it: Path() collapses "rtsp://host" to "rtsp:/host",
    the result is not absolute, so it gets prefixed with the repo root and
    comes out as "<repo>/rtsp:/host/path". That is not a reachable camera and
    not an obvious error either -- VideoCapture just fails to open it.

    Environment variables are still expanded for URIs, so a manifest can say

        rtsp://${CAM_USER}:${CAM_PASS}@192.168.11.51:554/cam/realmonitor

    and keep the credentials out of version control entirely.
    """
    if not value:
        return value
    raw = str(value)
    if is_network_uri(raw):
        return os.path.expandvars(raw)
    return resolve_repo_path(raw)


def redact_stream_credentials(value: str | None) -> str | None:
    """Strip user:password out of a URI before it is stored or logged.

    Camera URIs carry credentials inline, and the camera row writes
    source_uri to SQLite and Postgres (sqlite_alert_store.py,
    postgres_alert_store.py). Without this the camera password ends up in the
    database, in every backup taken of it, and in any log line or dashboard
    that renders a camera row.

    Non-URI values are returned unchanged -- a file path has nothing to hide,
    and so is anything malformed enough that urlsplit cannot parse it, which
    is deliberately treated as "leave it alone" rather than raising inside a
    persistence path.
    """
    if not value or not is_network_uri(value):
        return value
    try:
        parts = urlsplit(str(value))
    except ValueError:
        return value
    if "@" not in parts.netloc:
        return value
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, f"***@{host}", parts.path,
                       parts.query, parts.fragment))
