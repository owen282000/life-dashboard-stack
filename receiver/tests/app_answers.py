"""How the apps read the receiver's answer, ported line by line, so a test can feed them ours.

ios_ping_outcome follows PairingPingOutcome.from in the iOS app
(LifeDashboardCompanion/Models/PairingApply.swift), with WebhookSigner.responseSignature
(LifeDashboardCompanion/Managers/WebhookSigner.swift): what the pairing sheet shows after its
test ping. android_receive_verdict follows WriteBackPayload.verify in the Android app
(app/src/main/java/com/owen282000/lifedashboard/WriteBackPayload.kt), with
WebhookSupport.responseSignature: how Receive reads the answer of the one URL it asks for
measurements. Android's pairing ping itself only looks at the status (TestPing.send).

The signatures are computed here on their own, not with the receiver's code, so a receiver
that signs the wrong way fails these checks.
"""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

RESPONSE_KEY_LABEL = b"life-dashboard-response-v1"

CONFIRMED = "confirmed"
DELIVERED_UNCONFIRMED = "deliveredUnconfirmed"
REFUSED = "refused"
FAILED = "failed"

ACCEPTED = "accepted"
INCOMPATIBLE = "incompatible"

# WriteBackPayload's limits.
MAX_BODY_BYTES = 262_144
PROTOCOL = 1
MAX_CLOCK_SKEW = timedelta(minutes=10)


def request_signature(body: bytes, secret: str) -> str:
    """WebhookSigner.signatureHeader and WebhookSupport.signature: what the apps put on a request."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def response_signature(body: bytes, secret: str) -> str:
    """WebhookSigner.responseSignature and WebhookSupport.responseSignature."""
    key = hmac.new(secret.encode("utf-8"), RESPONSE_KEY_LABEL, hashlib.sha256).digest()
    return "sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest()


def _equal(left: str, right: str) -> bool:
    # WebhookSigner.constantTimeEqual and WebhookSupport.signaturesMatch: byte for byte.
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def ios_ping_outcome(status: int | None, body: bytes, answer_signature: str | None,
                     req_signature: str, secret: str) -> str:
    """PairingPingOutcome.from: confirmed only when the answer names this request."""
    if status is None:
        return FAILED
    if status == 401:
        return REFUSED
    if not 200 <= status <= 299:
        return FAILED
    try:
        obj = json.loads(body)
    except ValueError:
        return DELIVERED_UNCONFIRMED
    if not isinstance(obj, dict) or not isinstance(obj.get("life_dashboard"), dict):
        return DELIVERED_UNCONFIRMED
    writeback = obj.get("writeback")
    if not isinstance(writeback, dict):
        return DELIVERED_UNCONFIRMED
    in_reply_to = writeback.get("in_reply_to")
    if not isinstance(in_reply_to, str) or not _equal(in_reply_to, req_signature):
        return DELIVERED_UNCONFIRMED
    if answer_signature is not None and not _equal(answer_signature, response_signature(body, secret)):
        return DELIVERED_UNCONFIRMED
    return CONFIRMED


def android_receive_verdict(body: bytes, signature_header: str | None, secret: str,
                            req_signature: str, now: datetime | None = None) -> str:
    """WriteBackPayload.verify, up to its verdict: accepted, incompatible or rejected: <reason>.

    Incompatible is what an integration without the protocol gets: no measurements, and the
    Receive row says the webhook's answer carried no measurements protocol.
    """
    now = now or datetime.now(timezone.utc)
    if len(body) > MAX_BODY_BYTES:
        return "rejected: too large"
    text = body.decode("utf-8", errors="replace")
    if not signature_header or not signature_header.strip():
        return "rejected: missing signature" if _has_protocol_block(text) else INCOMPATIBLE
    if not _equal(signature_header, response_signature(body, secret)):
        return "rejected: bad signature"
    try:
        root = json.loads(text)
    except ValueError:
        return "rejected: malformed"
    if not isinstance(root, dict):
        return "rejected: malformed"
    announced = root.get("life_dashboard")
    announced = announced if isinstance(announced, dict) else None
    protocol = announced.get("writeback") if announced else None
    # JsonPrimitive.longOrNull: a whole number, or a string of one.
    if isinstance(protocol, bool) or not isinstance(protocol, (int, str)):
        return INCOMPATIBLE
    try:
        protocol = int(protocol)
    except ValueError:
        return INCOMPATIBLE
    if protocol != PROTOCOL:
        return "rejected: unsupported protocol"
    block = root.get("writeback")
    if not isinstance(block, dict):
        return "rejected: malformed"
    in_reply_to = block.get("in_reply_to")
    if not isinstance(in_reply_to, str) or not _equal(in_reply_to, req_signature):
        return "rejected: not in reply"
    try:
        issued = datetime.fromisoformat(str(block.get("issued_at")).replace("Z", "+00:00"))
    except ValueError:
        return "rejected: malformed"
    if abs(now - issued) > MAX_CLOCK_SKEW:
        return "rejected: stale"
    return ACCEPTED


def _has_protocol_block(text: str) -> bool:
    try:
        return isinstance(json.loads(text).get("life_dashboard"), dict)
    except (ValueError, AttributeError):
        return False
