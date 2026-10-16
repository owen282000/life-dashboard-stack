"""The apps' pairing link parsers, ported line by line, so a test can feed them our codes.

android_parse follows PairingLinks.parse in the Android app
(app/src/main/java/com/owen282000/lifedashboard/PairingLink.kt), ios_parse follows
PairingLinks.parse in the iOS app (LifeDashboardCompanion/Models/PairingLink.swift). Both
are checked against the known-good vector from the apps' own tests in test_pairing.py, so a
port that drifts from the original fails there first.
"""

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

PAIR_HOST = "owen282000.github.io"
PAIR_PATH = "/life-dashboard-companion-app/pair"
SCHEME = "lifedashboard"
SCHEME_HOST = "pair"
VERSION = "1"
MAX_URL = 2048
MAX_SECRET = 512
MAX_NAME = 64
SOURCES = ("health_connect", "screen_time")

NOT_OURS = "not a pairing link"
UNSUPPORTED_VERSION = "unsupported version"
INCOMPLETE = "incomplete"
NO_USABLE_SOURCE = "no usable source"


@dataclass(frozen=True)
class Link:
    url: str
    secret: str
    name: str | None
    sources: frozenset


def _is_control(c: str) -> bool:
    # Kotlin's Char.isISOControl and Foundation's controlCharacters agree on these ranges.
    return ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F


def _split(text: str | None):
    trimmed = (text or "").strip()
    if not trimmed:
        return None
    hash_at = trimmed.find("#")
    if hash_at >= 0:
        return trimmed[:hash_at], trimmed[hash_at + 1:]
    return trimmed, None


def _fields(raw: str, decode) -> dict:
    fields = {}
    for part in raw.split("&"):
        index = part.find("=")
        if index <= 0:
            continue
        fields[decode(part[:index])] = decode(part[index + 1:])
    return fields


def _strict_unquote(value: str):
    """Percent-decoding that fails on a broken escape, as URLDecoder and Foundation do."""
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        return None
    try:
        return unquote(value, errors="strict")
    except UnicodeDecodeError:
        return None


# ------------------------------------------------------------------ Android

def _android_decode(value: str) -> str:
    # URLDecoder.decode(value.replace("+", "%2B")): a literal + stays a +.
    decoded = _strict_unquote(value.replace("+", "%2B"))
    return value if decoded is None else decoded


def _android_is_ours(address: str) -> bool:
    if any(c.isspace() for c in address):
        return False  # java.net.URI refuses it, and parse answers NotAPairingLink
    try:
        uri = urlsplit(address)
    except ValueError:
        return False
    scheme = uri.scheme.lower()
    if scheme == "https":
        return (uri.hostname or "").lower() == PAIR_HOST and \
            uri.path.rstrip("/").lower() == PAIR_PATH
    if scheme == SCHEME:
        target = uri.netloc or address.split(":", 1)[1].strip("/")
        return target.lower() == SCHEME_HOST
    return False


def _android_usable_url(url: str) -> bool:
    # SettingsRules.isValidUrl is OkHttp's toHttpUrlOrNull: http or https with a host.
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018
    except ValueError:
        return False
    return (len(url) <= MAX_URL and parts.scheme.lower() in ("http", "https")
            and bool(parts.hostname) and not any(c.isspace() for c in url) and "," not in url)


def android_parse(text: str | None):
    split = _split(text)
    if split is None:
        return NOT_OURS
    address, raw = split
    if not _android_is_ours(address):
        return NOT_OURS
    if raw is None:
        return INCOMPLETE
    fields = _fields(raw, _android_decode)
    if fields.get("v") != VERSION:
        return UNSUPPORTED_VERSION
    url = fields.get("url", "").strip()
    secret = fields.get("secret", "").strip()
    usable_secret = bool(secret.strip()) and len(secret) <= MAX_SECRET and \
        not any(c.isspace() for c in secret)
    if not _android_usable_url(url) or not usable_secret:
        return INCOMPLETE
    raw_sources = fields.get("sources")
    if raw_sources is None or not raw_sources.strip():
        sources = frozenset(SOURCES)
    else:
        sources = frozenset(s.strip() for s in raw_sources.split(",") if s.strip() in SOURCES)
    if not sources:
        return NO_USABLE_SOURCE
    name = fields.get("name")
    if name is not None:
        name = "".join(c for c in name if not _is_control(c)).strip()[:MAX_NAME] or None
    return Link(url, secret, name, sources)


# ------------------------------------------------------------------ iOS

def _ios_decode(value: str) -> str:
    decoded = _strict_unquote(value)
    return value if decoded is None else decoded


def _ios_is_ours(address: str) -> bool:
    if ":" not in address:
        return False
    scheme, rest = address.split(":", 1)
    scheme = scheme.lower()
    if scheme == "https":
        if not rest.startswith("//"):
            return False
        rest = rest[2:]
        slash = rest.find("/")
        authority = (rest if slash < 0 else rest[:slash]).lower()
        path = "" if slash < 0 else rest[slash:]
        return authority == PAIR_HOST and path.rstrip("/").lower() == PAIR_PATH
    if scheme == SCHEME:
        return rest.strip("/").lower() == SCHEME_HOST
    return False


def _ios_usable_url(url: str) -> bool:
    if len(url.encode("utf-16-le")) // 2 > MAX_URL or any(c.isspace() for c in url) \
            or any(_is_control(c) for c in url) or "," in url:
        return False
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - URLComponents refuses a port that is not a number
    except ValueError:
        return False
    return (parts.scheme.lower() in ("http", "https") and bool(parts.hostname)
            and parts.username is None and parts.password is None and parts.hostname.isascii())


def ios_parse(text: str | None):
    split = _split(text)
    if split is None:
        return NOT_OURS
    address, raw = split
    if not _ios_is_ours(address):
        return NOT_OURS
    if raw is None:
        return INCOMPLETE
    fields = _fields(raw, _ios_decode)
    if fields.get("v") != VERSION:
        return UNSUPPORTED_VERSION
    url = fields.get("url", "").strip()
    secret = fields.get("secret", "").strip()
    usable_secret = bool(secret) and len(secret.encode("utf-16-le")) // 2 <= MAX_SECRET and \
        not any(c.isspace() for c in secret)
    if not _ios_usable_url(url) or not usable_secret:
        return INCOMPLETE
    raw_sources = fields.get("sources")
    if raw_sources is not None and raw_sources.strip() and \
            "health_connect" not in [s.strip() for s in raw_sources.split(",")]:
        return NO_USABLE_SOURCE
    name = fields.get("name")
    if name is not None:
        name = "".join(c for c in name if not _is_control(c)).strip()[:MAX_NAME] or None
    # iOS has only the Health section.
    return Link(url, secret, name, frozenset({"health_connect"}))
