"""The HTTP layer: paths, size cap, signature, pings, health and the empty-secret warning."""

import hashlib
import hmac
import http.client
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

import receiver
from conftest import RECEIVER_DIR, envelope, query


def send(base, path, body: bytes, signature=None, secret=None):
    req = urllib.request.Request(base + path, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    if secret is not None:
        signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if signature is not None:
        req.add_header("X-Signature", signature)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def sync_body(seq=1):
    return json.dumps(envelope(seq, heart_rate=[
        {"bpm": 61, "time": "2026-10-04T06:58:00Z", "uuid": f"hr-{seq}#1", "source": "watch"}])).encode()


def test_signature_ok_bad_absent(http_server, db):
    base = http_server("s3cret")
    assert send(base, "/webhook/phone", sync_body(1), secret="s3cret")[0] == 200
    assert send(base, "/webhook/phone", sync_body(2), secret="wrong")[0] == 401
    assert send(base, "/webhook/phone", sync_body(3))[0] == 401
    assert send(base, "/webhook/phone", sync_body(4), signature="sha256=zz")[0] == 401
    assert query(db, "SELECT uuid FROM records") == [("hr-1#1",)]
    assert query(db, "SELECT count(*) FROM payloads") == [(1,)]


def test_install_from_path(http_server, db):
    base = http_server("")
    assert send(base, "/webhook", sync_body(1))[0] == 200
    assert send(base, "/webhook/kitchen-tablet_2", sync_body(2))[0] == 200
    for bad in ("/webhook/Upper", "/webhook/a/b", "/webhook/" + "x" * 33, "/webhooks", "/webhook/"):
        assert send(base, bad, sync_body(9))[0] == 404, bad
    rows = query(db, "SELECT install, count(*) FROM records GROUP BY 1 ORDER BY 1")
    assert rows == [("default", 1), ("kitchen-tablet_2", 1)]


def test_body_over_32_mb_is_refused_before_reading(http_server, db):
    base = urlparse(http_server("s3cret"))
    conn = http.client.HTTPConnection(base.hostname, base.port, timeout=10)
    conn.putrequest("POST", "/webhook/phone")
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Content-Length", str(32 * 1024 * 1024 + 1))
    conn.endheaders()
    conn.send(b"{}")  # far less than announced: a server that reads the body would hang
    resp = conn.getresponse()
    assert resp.status == 413
    conn.close()
    assert query(db, "SELECT count(*) FROM payloads") == [(0,)]


def test_ping_writes_only_a_ledger_row(http_server, db):
    base = http_server("s3cret")
    ping = {"test": True, "message": "Test ping from Life Dashboard Companion",
            "timestamp": "2026-10-04T12:00:00Z", "app_version": "1.23.0", "source": "health_connect"}
    status, body = send(base, "/webhook/phone", json.dumps(ping).encode(), secret="s3cret")
    assert status == 200 and body.get("ping") is True
    assert query(db, "SELECT kind, install, record_count FROM payloads") == [("ping", "phone", 0)]
    assert query(db, "SELECT count(*) FROM records") == [(0,)]


def test_invalid_json_is_400(http_server):
    base = http_server("")
    assert send(base, "/webhook", b"{not json")[0] == 400
    assert send(base, "/webhook", b"[1, 2]")[0] == 400


def test_health_is_503_until_ready(http_server):
    base = http_server("")
    receiver.READY.clear()
    try:
        try:
            urllib.request.urlopen(base + "/health", timeout=5)
            raise AssertionError("expected 503")
        except urllib.error.HTTPError as e:
            assert e.code == 503
    finally:
        receiver.READY.set()
    with urllib.request.urlopen(base + "/health", timeout=5) as r:
        assert r.status == 200


def test_empty_secret_warning():
    assert receiver.secret_warning("x") is None
    text = receiver.secret_warning("")
    assert "WEBHOOK_SECRET is empty" in text


def test_empty_secret_warning_is_logged_at_start(server_url):
    # The receiver logs the warning before it even reaches the database.
    env = dict(os.environ, WEBHOOK_SECRET="", PORT="0",
               DATABASE_URL="postgresql://nobody:x@127.0.0.1:1/none", TZ="UTC")
    proc = subprocess.Popen([sys.executable, str(RECEIVER_DIR / "receiver.py")], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    assert "WEBHOOK_SECRET is empty" in err
