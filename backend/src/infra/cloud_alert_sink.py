"""The cloud-db sink AlertOutbox plugs in via sink_factory (see
backend/src/runtime/alert_outbox.py and business_tier.py's --storage-mode
wiring). Same call surface as SqliteAlertStore/PostgresAlertStore --
start_run/finish_run/create_alert/add_alert_media/close -- so it is a drop-in
sink_factory target, nothing upstream of it changes.

The only thing this class adds on top of PostgresAlertStore is
add_alert_media: media_store.save_snapshot()/save_clip() already wrote the
file to local disk (fast, unaffected by network) before the pipeline ever
called add_alert_media with that local path -- this uploads that file to R2
and stores the R2 object key in Postgres instead of the local path, so a
frontend can later build a URL straight to R2 without going through this
service again.

Once R2 holds a verified copy and Postgres holds the row pointing at it, the
local file is deleted. Before that it was kept forever: a clip is 5.7-15.5 MB,
and a run that does not end fills the disk. The order is the whole guarantee:

    1. PUT with Content-MD5   R2 refuses the object unless the bytes it
                              received hash to what this machine sent
    2. HEAD, compare size     the object exists and is the whole file
    3. INSERT the row         Postgres now points at it
    4. delete the local file  only now is it the second copy, not the only one

A failure at any step raises before step 4, so the file is still on disk for
AlertOutbox's retry. The retry re-runs the steps; a key already verified in
this process skips 1-2 rather than uploading the same 15 MB again.

Directories are left in place, deliberately. The snapshot and the clip for one
alert share a directory and are written at different times by the pipeline
thread, while this runs on the writer thread; removing a directory that looks
empty here can race the pipeline's mkdir for the next file and make that
write fail on the camera thread.
"""
from __future__ import annotations

import base64
import hashlib
import logging
from pathlib import Path
from typing import Any

from backend.src.infra.postgres_alert_store import PostgresAlertStore

logger = logging.getLogger(__name__)

# S3 error codes that mean "try again", as opposed to a bad key, a missing
# bucket or a request that will be refused identically every time.
_TRANSIENT_S3_CODES = frozenset({
    "InternalError", "ServiceUnavailable", "SlowDown", "RequestTimeout",
    "RequestTimeTooSkewed", "Throttling", "BadDigest", "IncompleteBody",
    "500", "502", "503", "504",
})


class UploadVerificationError(RuntimeError):
    """R2 accepted the PUT but the stored object is not the file we sent."""


class CloudAlertSink:
    def __init__(self, dsn: str, r2_endpoint: str, r2_bucket: str,
                 r2_access_key_id: str, r2_secret_access_key: str):
        import boto3
        from botocore.config import Config

        self.store = PostgresAlertStore(dsn)
        self.bucket = r2_bucket
        self.s3 = boto3.client(
            "s3", endpoint_url=r2_endpoint, aws_access_key_id=r2_access_key_id,
            aws_secret_access_key=r2_secret_access_key, region_name="auto",
            # Bounded, so a stalled connection surfaces as an error the outbox
            # retries instead of the writer thread hanging on it. botocore's
            # own retries absorb a single dropped request; the outbox's backoff
            # covers an outage longer than that.
            config=Config(connect_timeout=10, read_timeout=60,
                          retries={"max_attempts": 3, "mode": "standard"}))
        # Keys already uploaded and verified whose DB row has not landed yet.
        self._verified: set[str] = set()

    def close(self):
        self.store.close()

    def warm(self) -> None:
        """Connect to Neon and R2 before the first alert needs them."""
        self.store._connection()
        self.s3.head_bucket(Bucket=self.bucket)

    def is_transient(self, exc: BaseException) -> bool:
        from botocore.exceptions import ClientError, ConnectionError, HTTPClientError

        if self.store.is_transient(exc):
            return True
        if isinstance(exc, (ConnectionError, HTTPClientError, UploadVerificationError)):
            return True
        if isinstance(exc, ClientError):
            error = exc.response.get("Error", {})
            status = str(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", ""))
            return str(error.get("Code", "")) in _TRANSIENT_S3_CODES or status.startswith("5")
        return False

    def start_run(self, *args, **kwargs) -> str:
        return self.store.start_run(*args, **kwargs)

    def finish_run(self, *args, **kwargs) -> None:
        self.store.finish_run(*args, **kwargs)

    def create_alert(self, **kwargs) -> str:
        return self.store.create_alert(**kwargs)

    def add_alert_media(self, *, alert_id: str, kind: str, path: str,
                        media_id: str | None = None, **kwargs: Any) -> str:
        local_path = Path(path)
        key = f"alerts/{alert_id}/{local_path.name}"
        if key not in self._verified:
            self._upload_verified(local_path, key)
            self._verified.add(key)
        media_id = self.store.add_alert_media(
            alert_id=alert_id, kind=kind, path=key, media_id=media_id, **kwargs)
        self._verified.discard(key)
        try:
            local_path.unlink()
        except OSError as exc:
            # The alert is safe in R2 and Postgres; a file that could not be
            # removed costs disk, not evidence. Not worth failing the write.
            logger.warning("uploaded %s but could not delete the local copy: %r", key, exc)
        return media_id

    def _upload_verified(self, local_path: Path, key: str) -> None:
        body = local_path.read_bytes()
        md5 = base64.b64encode(hashlib.md5(body).digest()).decode("ascii")
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=body, ContentMD5=md5)
        head = self.s3.head_object(Bucket=self.bucket, Key=key)
        stored = int(head.get("ContentLength", -1))
        if stored != len(body):
            raise UploadVerificationError(
                f"R2 holds {stored} bytes for {key}, the local file is {len(body)}")
