"""The answer to a signed payload: what makes the apps' pairing test report the receiver as confirmed.

The Life Dashboard integration for Home Assistant answers every payload it took with a signed
JSON body that names the request's X-Signature. The receiver answers the same way, without
the parts that offer measurements to the phone.
"""

import json
import re
import urllib.error
import urllib.request

import receiver
from app_answers import (
    CONFIRMED, DELIVERED_UNCONFIRMED, INCOMPATIBLE, REFUSED, android_receive_verdict,
    ios_ping_outcome, request_signature, response_signature,
)
from conftest import REPO_DIR, envelope, query

SECRET = "s3cret"


def ios_ping(app_version="1.6.0") -> bytes:
    """TestPing.body in the iOS app: JSONSerialization with sorted keys, no spaces."""
    return json.dumps({
        "app_version": app_version, "message": "Test ping from Life Dashboard Companion",
        "source": "healthkit_ios", "test": True, "timestamp": "2026-10-17T09:30:00Z",
    }, sort_keys=True, separators=(",", ":")).encode()


def android_ping() -> bytes:
    """TestPing.payload in the Android app, as kotlinx.serialization writes it."""
    return json.dumps({
        "test": True, "message": "Test ping from Life Dashboard Companion",
        "timestamp": "2026-10-17T09:30:00.123456Z", "app_version": "1.23.0", "source": "health_connect",
    }, separators=(",", ":")).encode()


def post(base, body: bytes, signature: str | None, path="/webhook/phone"):
    """One POST as the apps send it. Returns the status, the raw answer and its X-Signature."""
    req = urllib.request.Request(base + path, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if signature is not None:
        req.add_header("X-Signature", signature)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), r.headers.get("X-Signature")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("X-Signature")


# ------------------------------------------------------------------ the ports themselves

def test_the_ports_match_the_apps_own_vectors():
    """WebhookSignerTests and PairingApplyTests in the iOS app, against the ported code."""
    fox = b"The quick brown fox jumps over the lazy dog"
    assert request_signature(fox, "key") == \
        "sha256=f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8"
    assert response_signature(fox, "key") == \
        "sha256=1f5e6e7bf7761bb81dcbbb34f09b6ba176523cdeba9e80d9a28dce3f34791e9b"
    asked = "sha256=" + "1" * 64
    ha = (b'{"life_dashboard":{"version":"0.7.0","writeback":1},'
          b'"writeback":{"in_reply_to":"' + asked.encode() + b'","issued_at":"2026-09-30T10:00:00Z","configured":[]}}')
    assert ios_ping_outcome(200, ha, response_signature(ha, "key"), asked, "key") == CONFIRMED
    assert ios_ping_outcome(200, ha, None, asked, "key") == CONFIRMED
    assert ios_ping_outcome(200, ha, "sha256=" + "0" * 64, asked, "key") == DELIVERED_UNCONFIRMED
    assert ios_ping_outcome(200, ha, None, "sha256=other", "key") == DELIVERED_UNCONFIRMED
    assert ios_ping_outcome(200, b"", None, asked, "key") == DELIVERED_UNCONFIRMED
    assert ios_ping_outcome(200, b'{"ok":true}', None, asked, "key") == DELIVERED_UNCONFIRMED
    assert ios_ping_outcome(401, b"", None, asked, "key") == REFUSED
    # The integration's own answer is one Receive accepts, so the Android port tells them apart.
    assert android_receive_verdict(ha, response_signature(ha, "key"), "key", asked,
                                   now=receiver.parse_time("2026-09-30T10:01:00Z")) == "accepted"


# ------------------------------------------------------------------ the receiver's answer

def test_a_signed_ping_is_confirmed(http_server, db):
    base = http_server(SECRET)
    body = ios_ping()
    asked = request_signature(body, SECRET)
    status, answer, answer_signature = post(base, body, asked)

    assert status == 200
    assert answer_signature == response_signature(answer, SECRET)
    assert answer_signature != request_signature(answer, SECRET)  # the derived key, not the secret
    reply = json.loads(answer)
    assert reply["writeback"] == {"in_reply_to": asked}
    assert reply["life_dashboard"] == {"receiver": "life-dashboard-stack", "version": receiver.VERSION}
    assert reply["ping"] is True
    assert len(answer) < 400
    assert ios_ping_outcome(status, answer, answer_signature, asked, SECRET) == CONFIRMED
    # Through a relay that drops the header, as Home Assistant Cloud does, it still confirms.
    assert ios_ping_outcome(status, answer, None, asked, SECRET) == CONFIRMED
    assert query(db, "SELECT kind FROM payloads") == [("ping",)]


def test_the_android_ping_is_confirmed_and_offers_no_measurements(http_server):
    base = http_server(SECRET)
    body = android_ping()
    asked = request_signature(body, SECRET)
    status, answer, answer_signature = post(base, body, asked)
    # Android's pairing ping counts any 2xx as delivered; its answer is the same contract.
    assert status == 200
    assert ios_ping_outcome(status, answer, answer_signature, asked, SECRET) == CONFIRMED
    # Receive reads it as an answer without the measurements protocol: nothing is written to
    # Health Connect and nothing is refused, as for a receiver that sends no answer at all.
    assert android_receive_verdict(answer, answer_signature, SECRET, asked) == INCOMPATIBLE
    assert android_receive_verdict(b"", None, SECRET, asked) == INCOMPATIBLE
    reply = json.loads(answer)
    assert "writeback" not in reply["life_dashboard"]
    assert set(reply["writeback"]) == {"in_reply_to"}


def test_the_answer_names_this_request_only(http_server):
    base = http_server(SECRET)
    first, second = ios_ping("1.6.0"), ios_ping("1.6.1")
    _, answer, signature = post(base, first, request_signature(first, SECRET))
    assert ios_ping_outcome(200, answer, signature, request_signature(second, SECRET), SECRET) \
        == DELIVERED_UNCONFIRMED
    # Another secret on the phone: the answer does not check out with it.
    assert ios_ping_outcome(200, answer, signature, request_signature(first, SECRET), "other") \
        == DELIVERED_UNCONFIRMED


def test_unsigned_without_a_secret_gets_no_confirmation(http_server, db):
    base = http_server("")
    body = ios_ping()
    status, answer, answer_signature = post(base, body, None)
    assert status == 200 and answer_signature is None
    reply = json.loads(answer)
    assert "life_dashboard" not in reply and "writeback" not in reply
    assert reply == {"stored": 0, "ping": True}
    # A phone that signs anyway, at a receiver without a secret, is not confirmed either.
    signed = ios_ping("1.6.1")
    status, answer, answer_signature = post(base, signed, request_signature(signed, "phone-secret"))
    assert status == 200 and answer_signature is None and "life_dashboard" not in json.loads(answer)
    assert ios_ping_outcome(status, answer, answer_signature, request_signature(signed, "phone-secret"),
                            "phone-secret") == DELIVERED_UNCONFIRMED


def test_a_bad_signature_is_401_with_nothing_more(http_server, db):
    base = http_server(SECRET)
    body = ios_ping()
    for signature in (request_signature(body, "wrong"), "sha256=zz", None):
        status, answer, answer_signature = post(base, body, signature)
        assert status == 401
        assert answer_signature is None
        assert json.loads(answer) == {"error": "invalid signature"}
        assert ios_ping_outcome(status, answer, answer_signature, signature or "", SECRET) == REFUSED
    assert query(db, "SELECT count(*) FROM payloads") == [(0,)]


def test_refusals_after_the_signature_get_no_confirmation(http_server):
    base = http_server(SECRET)
    for body in (b"{not json", b"[1, 2]"):
        status, answer, answer_signature = post(base, body, request_signature(body, SECRET))
        assert status == 400 and answer_signature is None
        assert "life_dashboard" not in json.loads(answer)


def test_a_sync_is_stored_as_before_and_confirmed(http_server, db):
    base = http_server(SECRET)
    body = json.dumps(envelope(1, heart_rate=[
        {"bpm": 61, "time": "2026-10-04T06:58:00Z", "uuid": "hr-1#1", "source": "watch"}])).encode()
    asked = request_signature(body, SECRET)
    status, answer, answer_signature = post(base, body, asked)
    assert status == 200
    reply = json.loads(answer)
    assert {k: reply[k] for k in ("stored", "buckets", "deleted", "skipped")} == \
        {"stored": 1, "buckets": 0, "deleted": 0, "skipped": 0}
    assert reply["writeback"] == {"in_reply_to": asked}
    assert answer_signature == response_signature(answer, SECRET)
    assert query(db, "SELECT uuid, install, (data->>'bpm')::int FROM records") == [("hr-1#1", "phone", 61)]
    assert query(db, "SELECT kind, record_count FROM payloads") == [("sync", 1)]

    # The same body again is a duplicate: applied once, and still answered for this request.
    status, answer, answer_signature = post(base, body, asked)
    reply = json.loads(answer)
    assert status == 200 and reply["duplicate"] is True and reply["writeback"] == {"in_reply_to": asked}
    assert answer_signature == response_signature(answer, SECRET)
    assert query(db, "SELECT count(*) FROM records") == [(1,)]
    assert query(db, "SELECT count(*) FROM payloads") == [(1,)]


def test_the_receiver_names_the_release_version():
    """scripts/prepare-release.sh sets it with the MCP server's and install.sh's."""
    mcp = re.search(r'^VERSION = "([^"]*)"', (REPO_DIR / "mcp" / "server.py").read_text(), re.M)
    install = re.search(r"^VERSION=(\S+)$", (REPO_DIR / "install.sh").read_text(), re.M)
    assert mcp and install and receiver.VERSION == mcp.group(1) == install.group(1)
