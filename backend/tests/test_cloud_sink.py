"""cloud-db writes that have to survive a run that does not end.

Two guarantees, both about what happens when the network misbehaves:

  * A local evidence file is deleted only after R2 has a verified copy AND
    Postgres has the row pointing at it. Fail anywhere before that and the
    file must still be on disk for the retry.
  * A lost Postgres connection is replaced, and a retried INSERT cannot turn
    into a primary-key violation that drops the write.

No network: S3 and psycopg2 are replaced by fakes. Whether R2 itself honours
Content-MD5 is R2's behaviour, not this code's, and is not asserted here.

    python backend/tools/run_tests_nopytest.py backend/tests/test_cloud_sink.py
"""
from __future__ import annotations

import base64
import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _FakeS3:
    def __init__(self, *, stored_size_delta=0, put_errors=()):
        self.objects: dict[str, bytes] = {}
        self.puts: list[dict] = []
        self.stored_size_delta = stored_size_delta
        self.put_errors = list(put_errors)

    def put_object(self, *, Bucket, Key, Body, ContentMD5):
        self.puts.append({"Key": Key, "ContentMD5": ContentMD5, "size": len(Body)})
        if self.put_errors:
            raise self.put_errors.pop(0)
        self.objects[Key] = Body

    def head_object(self, *, Bucket, Key):
        return {"ContentLength": len(self.objects[Key]) + self.stored_size_delta}


class _FakeStore:
    def __init__(self, fail_times=0, exc=None):
        self.rows: list[dict] = []
        self.fail_times = fail_times
        self.exc = exc or ConnectionError("neon went away")

    @staticmethod
    def is_transient(exc):
        return isinstance(exc, ConnectionError)

    def add_alert_media(self, **kwargs):
        if self.fail_times:
            self.fail_times -= 1
            raise self.exc
        self.rows.append(kwargs)
        return kwargs.get("media_id") or "m1"

    def close(self):
        pass


def _sink(s3, store):
    from backend.src.infra.cloud_alert_sink import CloudAlertSink

    # Bypass __init__: it builds a real boto3 client and a real store.
    sink = CloudAlertSink.__new__(CloudAlertSink)
    sink.s3 = s3
    sink.store = store
    sink.bucket = "test-bucket"
    sink._verified = set()
    return sink


def _evidence(tmp_path, name="evidence.mp4", size=4096) -> Path:
    path = tmp_path / "alert-1" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(range(256)) * (size // 256))
    return path


def test_the_local_file_is_deleted_only_after_upload_verify_and_row(tmp_path):
    path = _evidence(tmp_path)
    body = path.read_bytes()
    s3, store = _FakeS3(), _FakeStore()

    _sink(s3, store).add_alert_media(alert_id="alert-1", kind="clip", path=str(path),
                                     media_id="m1")

    assert s3.objects["alerts/alert-1/evidence.mp4"] == body
    # Content-MD5 is what makes R2 refuse bytes that were corrupted in transit.
    assert s3.puts[0]["ContentMD5"] == base64.b64encode(hashlib.md5(body).digest()).decode()
    assert store.rows[0]["path"] == "alerts/alert-1/evidence.mp4"
    assert not path.exists()
    # The directory stays: the pipeline may be about to write the other file
    # for this alert into it, on another thread.
    assert path.parent.exists()


def test_a_size_mismatch_after_upload_keeps_the_file_and_is_retried(tmp_path):
    from backend.src.infra.cloud_alert_sink import UploadVerificationError

    path = _evidence(tmp_path)
    s3, store = _FakeS3(stored_size_delta=-1), _FakeStore()
    sink = _sink(s3, store)

    with pytest.raises(UploadVerificationError):
        sink.add_alert_media(alert_id="alert-1", kind="clip", path=str(path))

    assert path.exists()
    assert store.rows == []
    assert sink.is_transient(UploadVerificationError("x"))


def test_a_failed_row_insert_keeps_the_file_and_the_retry_does_not_reupload(tmp_path):
    """Upload succeeded, Postgres did not. The file must survive for the retry,
    and the retry should not push the same 15 MB clip to R2 a second time."""
    path = _evidence(tmp_path)
    s3, store = _FakeS3(), _FakeStore(fail_times=1)
    sink = _sink(s3, store)

    with pytest.raises(ConnectionError):
        sink.add_alert_media(alert_id="alert-1", kind="clip", path=str(path), media_id="m1")
    assert path.exists()
    assert sink.is_transient(ConnectionError("x"))

    sink.add_alert_media(alert_id="alert-1", kind="clip", path=str(path), media_id="m1")
    assert len(s3.puts) == 1
    assert len(store.rows) == 1
    assert not path.exists()


def test_s3_errors_are_split_into_retry_and_give_up(tmp_path):
    from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError

    sink = _sink(_FakeS3(), _FakeStore())

    def client_error(code, status):
        return ClientError({"Error": {"Code": code},
                            "ResponseMetadata": {"HTTPStatusCode": status}}, "PutObject")

    assert sink.is_transient(EndpointConnectionError(endpoint_url="https://r2"))
    assert sink.is_transient(ReadTimeoutError(endpoint_url="https://r2"))
    assert sink.is_transient(client_error("SlowDown", 503))
    assert sink.is_transient(client_error("BadDigest", 400))
    assert not sink.is_transient(client_error("AccessDenied", 403))
    assert not sink.is_transient(client_error("NoSuchBucket", 404))
    assert not sink.is_transient(FileNotFoundError("gone"))


def test_a_refused_upload_keeps_the_file(tmp_path):
    from botocore.exceptions import ClientError

    path = _evidence(tmp_path)
    denied = ClientError({"Error": {"Code": "AccessDenied"},
                          "ResponseMetadata": {"HTTPStatusCode": 403}}, "PutObject")
    s3 = _FakeS3(put_errors=[denied])
    sink = _sink(s3, _FakeStore())

    with pytest.raises(ClientError):
        sink.add_alert_media(alert_id="alert-1", kind="clip", path=str(path))
    assert path.exists()


class _FakeConn:
    def __init__(self, log, fail_with=None):
        self.log = log
        self.fail_with = fail_with
        self.closed = 0

    def cursor(self):
        conn = self

        class _Cur:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params):
                if conn.fail_with is not None:
                    raise conn.fail_with
                conn.log.append(sql)

        return _Cur()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = 1


def test_a_lost_postgres_connection_is_replaced_on_the_next_write():
    import psycopg2

    from backend.src.infra.postgres_alert_store import PostgresAlertStore

    log: list[str] = []
    connections: list[_FakeConn] = []

    def fake_connect(dsn, **kwargs):
        # The first connection dies mid-statement; the next one is healthy.
        conn = _FakeConn(log, fail_with=psycopg2.OperationalError("server closed the connection")
                         if not connections else None)
        connections.append(conn)
        return conn

    real_connect = psycopg2.connect
    psycopg2.connect = fake_connect
    try:
        store = PostgresAlertStore("postgres://example")
        assert connections == []  # lazy: nothing opened until the first write

        with pytest.raises(psycopg2.OperationalError) as excinfo:
            store.start_run("counter_1", "src", run_id="run-1")
        assert store.is_transient(excinfo.value)
        assert connections[0].closed

        store.start_run("counter_1", "src", run_id="run-1")
    finally:
        psycopg2.connect = real_connect

    assert len(connections) == 2
    assert len(log) == 1


def test_every_insert_is_idempotent_on_its_client_generated_id():
    """A commit can land and its acknowledgement be lost with the connection.
    The retry must then be a no-op, not a duplicate-key error that drops it."""
    import psycopg2

    from backend.src.infra.postgres_alert_store import PostgresAlertStore

    log: list[str] = []
    real_connect = psycopg2.connect
    psycopg2.connect = lambda dsn, **kw: _FakeConn(log)
    try:
        store = PostgresAlertStore("postgres://example")
        store.start_run("counter_1", "src", run_id="r")
        store.create_alert(store_id="s", camera_id="c", run_id="r", pipeline_type="p",
                           event_type="e", severity="w", opened_at="t",
                           identity_person_id=None, identity_label=None,
                           identity_score=None, reason_codes=[], alert_id="a")
        store.add_alert_media(alert_id="a", kind="clip", path="k", media_id="m")
    finally:
        psycopg2.connect = real_connect

    assert "ON CONFLICT (run_id) DO NOTHING" in log[0]
    assert "ON CONFLICT (alert_id) DO NOTHING" in log[1]
    assert "ON CONFLICT (media_id) DO NOTHING" in log[2]
