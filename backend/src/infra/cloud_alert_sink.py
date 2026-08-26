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
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.src.infra.postgres_alert_store import PostgresAlertStore


class CloudAlertSink:
    def __init__(self, dsn: str, r2_endpoint: str, r2_bucket: str,
                 r2_access_key_id: str, r2_secret_access_key: str):
        import boto3

        self.store = PostgresAlertStore(dsn)
        self.bucket = r2_bucket
        self.s3 = boto3.client(
            "s3", endpoint_url=r2_endpoint, aws_access_key_id=r2_access_key_id,
            aws_secret_access_key=r2_secret_access_key, region_name="auto")

    def close(self):
        self.store.close()

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
        self.s3.upload_file(str(local_path), self.bucket, key)
        return self.store.add_alert_media(
            alert_id=alert_id, kind=kind, path=key, media_id=media_id, **kwargs)
