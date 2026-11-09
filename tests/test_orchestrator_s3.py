import hashlib
import http.server
import threading
import urllib.parse

import requests

from orchestrator.s3_objects import S3ObjectStore


class _Store:
    def __init__(self):
        self.data = {}


def _fake_s3(objects):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _key(self):
            return urllib.parse.urlsplit(self.path).path.lstrip("/")

        def do_PUT(self):
            n = int(self.headers.get("content-length") or 0)
            objects[self._key()] = self.rfile.read(n)
            self.send_response(200); self.end_headers()

        def do_HEAD(self):
            body = objects.get(self._key())
            if body is None:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()

        def do_GET(self):
            body = objects.get(self._key())
            if body is None:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers(); self.wfile.write(body)

        def do_DELETE(self):
            objects.pop(self._key(), None)
            self.send_response(204); self.end_headers()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_s3_grant_put_head_hash_and_delete():
    objects = {}
    srv = _fake_s3(objects)
    try:
        store = S3ObjectStore(bucket="b", region="auto", access_key="ak",
                              secret_key="sk", endpoint=f"http://127.0.0.1:{srv.server_port}",
                              prefix="deltas")
        payload = b"delta-bytes"
        sha = hashlib.sha256(payload).hexdigest()
        grant = store.grant_put("r1/a.bin", sha, expires_s=60)
        assert "X-Amz-Signature=" in grant.url
        assert grant.key == "deltas/r1/a.bin"
        assert requests.put(grant.url, data=payload).ok
        assert store.head("r1/a.bin")["size"] == len(payload)
        assert store.verify_file("r1/a.bin", sha) is True
        assert store.verify_file("r1/a.bin", "0" * 64) is False
        assert store.delete("r1/a.bin") is True
        assert store.head("r1/a.bin") is None
    finally:
        srv.shutdown()


def test_s3_download_verifies_hash(tmp_path):
    objects = {}
    srv = _fake_s3(objects)
    try:
        store = S3ObjectStore(bucket="b", region="auto", access_key="ak",
                              secret_key="sk", endpoint=f"http://127.0.0.1:{srv.server_port}",
                              prefix="objects")
        payload = b"downloaded-delta"
        sha = hashlib.sha256(payload).hexdigest()
        objects["objects/r1/a.bin"] = payload
        dest = tmp_path / "a.bin"
        assert store.download("r1/a.bin", dest, expected_sha256=sha) == dest
        assert dest.read_bytes() == payload
        try:
            store.download("r1/a.bin", tmp_path / "b.bin", expected_sha256="0" * 64)
        except ValueError as e:
            assert "hash" in str(e)
        else:
            raise AssertionError("bad hash download accepted")
        assert not (tmp_path / "b.bin").exists()
    finally:
        srv.shutdown()


def test_s3_rejects_bad_key_and_hash():
    store = S3ObjectStore(bucket="b", region="auto", access_key="ak", secret_key="sk",
                          endpoint="http://127.0.0.1:9", prefix="deltas")
    for key in ("", "../escape"):
        try:
            store.grant_put(key, "a" * 64)
        except ValueError:
            pass
        else:
            raise AssertionError("bad key accepted")
    try:
        store.grant_put("ok", "short")
    except ValueError:
        pass
    else:
        raise AssertionError("bad hash accepted")


def test_s3_gc_and_outage_fail_closed(tmp_path):
    objects = {"deltas/live.bin": b"x"}
    srv = _fake_s3(objects)
    try:
        store = S3ObjectStore(bucket="b", region="auto", access_key="ak",
                              secret_key="sk", endpoint=f"http://127.0.0.1:{srv.server_port}",
                              prefix="deltas")
        # Absent keys are reported as already gone; present keys are deleted only
        # when the retention window is explicitly zero.
        assert store.gc(["missing.bin"], older_than_s=0) == ["missing.bin"]
        assert store.gc(["live.bin"], older_than_s=0) == ["live.bin"]
        assert store.head("live.bin") is None
    finally:
        srv.shutdown()
    down = S3ObjectStore(bucket="b", region="auto", access_key="ak", secret_key="sk",
                         endpoint="http://127.0.0.1:9", prefix="deltas", timeout=0.2)
    assert down.head("x") is None
    assert down.verify_file("x", "a" * 64) is False
    assert down.delete("x") is False
    try:
        down.download("x", tmp_path / "x")
    except (ValueError, requests.RequestException):
        pass
    else:
        raise AssertionError("outage accepted a download")
