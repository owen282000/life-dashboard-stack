"""Pairing: the link both apps parse, the QR code, the /pair page's gate and the log switch."""

import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

import pairing
import receiver
from app_parsers import (INCOMPLETE, NOT_OURS, UNSUPPORTED_VERSION, Link, android_parse,
                         ios_parse)
from conftest import RECEIVER_DIR

SECRET = "b" * 64
BOTH = frozenset({"health_connect", "screen_time"})

# The known-good vector of the Android app's PairingLinkTest, copied literally. It checks the
# ports in app_parsers.py against what the app's own test expects of it.
ANDROID_KNOWN_GOOD = (
    "https://owen282000.github.io/life-dashboard-companion-app/pair"
    "#v=1"
    "&url=http%3A%2F%2F192.168.10.138%3A8123%2Fapi%2Fwebhook%2F" + "a" * 64
    + "&secret=" + "b" * 64
    + "&name=Home%20Assistant"
    + "&sources=health_connect%2Cscreen_time"
)

# The known-good vector of the Home Assistant integration's test_pairing.py: the stack builds
# links with the same rules, so it builds this exact string from the same input.
HA_KNOWN_GOOD = (
    "https://owen282000.github.io/life-dashboard-companion-app/pair"
    "#v=1"
    "&url=http%3A%2F%2F192.168.10.138%3A8123%2Fapi%2Fwebhook%2F" + "a" * 64
    + "&secret=" + "b" * 64
    + "&name=Home%20Assistant"
)


# ------------------------------------------------------------------ the link

def test_the_ports_read_the_apps_own_vector():
    webhook = "http://192.168.10.138:8123/api/webhook/" + "a" * 64
    assert android_parse(ANDROID_KNOWN_GOOD) == Link(webhook, SECRET, "Home Assistant", BOTH)
    assert ios_parse(ANDROID_KNOWN_GOOD) == Link(webhook, SECRET, "Home Assistant",
                                                 frozenset({"health_connect"}))
    # A few of the apps' own edge cases, so the ports are not only right on the happy path.
    assert android_parse("lifedashboard://pair#v=1&url=https%3A%2F%2Fa.b%2Fc&secret=x+y").secret == "x+y"
    assert ios_parse("lifedashboard://pair#v=1&url=https%3A%2F%2Fa.b%2Fc&secret=x+y").secret == "x+y"
    for parse in (android_parse, ios_parse):
        assert parse("lifedashboard://pair#v=2&url=https%3A%2F%2Fa.b%2Fc&secret=xyz") == UNSUPPORTED_VERSION
        assert parse("lifedashboard://pair#v=1&url=https%3A%2F%2Fa.b%2Fc%2Cd&secret=xyz") == INCOMPLETE
        assert parse("lifedashboard://pair#v=1&url=https%3A%2F%2Fa.b%2Fc&secret=") == INCOMPLETE
        assert parse("https://example.com/pair#v=1&url=https%3A%2F%2Fa.b%2Fc&secret=xyz") == NOT_OURS


def test_builds_the_integrations_vector():
    webhook = "http://192.168.10.138:8123/api/webhook/" + "a" * 64
    assert pairing.pairing_url(webhook, SECRET, name="Home Assistant") == HA_KNOWN_GOOD


@pytest.mark.parametrize("base", [
    "http://192.168.1.10:8080",
    "http://192.168.1.10:8080/",
    "https://lifedash.example.com",
    "https://example.com/lifedash",
    "http://[fd00::10]:8080",
    "http://100.101.102.103:28080",
    "http://nas.local:8080",
])
@pytest.mark.parametrize("secret", [
    SECRET,
    "x+y/z=&w%20",           # characters with a meaning in a URL or a fragment
    "s" * pairing.MAX_SECRET,
    "grüße-☃",  # not ASCII
])
@pytest.mark.parametrize("label", ["phone", "kitchen-tablet_2"])
def test_a_stack_code_round_trips_through_both_apps(base, secret, label):
    hook = pairing.webhook_url(base, label)
    assert pairing.base_problem(base) is None and pairing.secret_problem(secret) is None
    link = pairing.pairing_url(hook, secret)
    assert receiver.LABEL_RE.match(urlsplit(hook).path.removeprefix(urlsplit(base).path.rstrip("/")))
    assert android_parse(link) == Link(hook, secret, pairing.NAME, BOTH)
    assert ios_parse(link) == Link(hook, secret, pairing.NAME, frozenset({"health_connect"}))
    # The pair page's server never sees the secret: it is only in the fragment.
    parts = urlsplit(link)
    assert parts.query == "" and secret not in parts.path


@pytest.mark.parametrize("base,an_app_refuses", [
    ("", True), ("ftp://192.168.1.10", True), ("192.168.1.10:8080", True), ("http://", True),
    ("http://a b.example", True), ("http://user:pw@192.168.1.10:8080", True),
    ("http://hé.example", True), ("http://192.168.1.10:port", True), ("http://a.b,c", True),
    # The apps would take these, but /webhook/<label> would land in the query or the fragment.
    ("http://192.168.1.10:8080/?x=1", False), ("http://192.168.1.10:8080/#x", False),
])
def test_an_address_that_cannot_work_gives_no_code(base, an_app_refuses):
    assert pairing.base_problem(base) is not None
    link = pairing.pairing_url(pairing.webhook_url(base, "phone"), SECRET)
    both_accept = isinstance(android_parse(link), Link) and isinstance(ios_parse(link), Link)
    assert both_accept is not an_app_refuses


@pytest.mark.parametrize("secret", ["", "has space", "tab\there", "s" * (pairing.MAX_SECRET + 1)])
def test_a_secret_the_apps_refuse_gives_no_code(secret):
    assert pairing.secret_problem(secret) is not None
    link = pairing.pairing_url("http://192.168.1.10:8080/webhook/phone", secret)
    assert android_parse(link) == INCOMPLETE
    assert ios_parse(link) == INCOMPLETE


def test_the_code_stays_small_enough_to_scan():
    # The integration's guard: up to 69 modules a phone reads through camera blur.
    for base in ("http://192.168.1.10:8080", "https://a-fairly-long-host-name.duckdns.org"):
        link = pairing.pairing_url(pairing.webhook_url(base, "kitchen-tablet"), "d" * 64)
        assert pairing.make_qr(link).symbol_size(scale=1, border=0)[0] <= 69


def test_the_terminal_code_is_the_symbol():
    link = pairing.pairing_url("http://192.168.1.10:8080/webhook/phone", SECRET)
    text = pairing.terminal_qr(link)
    lines = text.split("\n")
    # Back from blocks to modules: a block is light on top and bottom, a space is dark.
    top = {"█": 0, "▀": 0, "▄": 1, " ": 1}
    bottom = {"█": 0, "▀": 1, "▄": 0, " ": 1}
    rows = []
    for line in lines:
        rows.append([top[c] for c in line])
        rows.append([bottom[c] for c in line])
    expected = [list(r) for r in pairing.make_qr(link).matrix_iter(scale=1, border=2)]
    assert rows[: len(expected)] == expected
    assert all(len(line) + len("receiver-1  | ") <= 80 for line in lines)


def test_address_notes():
    assert pairing.address_notes("http://192.168.1.10:8080") == []
    assert pairing.address_notes("https://lifedash.example.com") == []
    assert pairing.address_notes("http://nas.local:8080") == []
    assert "cannot reach it" in pairing.address_notes("http://localhost:8080")[0]
    assert "cannot reach it" in pairing.address_notes("http://127.0.0.1:8080")[0]
    assert "iPhone refuses" in pairing.address_notes("http://nas.lan:8080")[0]


# ------------------------------------------------------------------ the gate

@pytest.mark.parametrize("address,private", [
    ("127.0.0.1", True), ("::1", True), ("10.1.2.3", True), ("172.16.0.1", True),
    ("172.31.255.255", True), ("192.168.1.10", True), ("169.254.1.1", True),
    ("100.101.102.103", True), ("fd12:3456::1", True), ("fe80::1%eth0", True),
    ("::ffff:192.168.1.10", True),
    ("8.8.8.8", False), ("172.32.0.1", False), ("100.128.0.1", False), ("203.0.113.9", False),
    ("2001:db8::1", False), ("::ffff:8.8.8.8", False), ("", False), ("not an ip", False),
])
def test_private_addresses(address, private):
    assert pairing.is_private_address(address) is private


def test_page_allowed():
    token = "t" * 24
    assert pairing.page_allowed("192.168.1.20", ["Host"], None, "")
    assert not pairing.page_allowed("8.8.8.8", ["Host"], None, "")
    # A proxy header is never believed: not to let a request in, and it keeps a private one out.
    assert not pairing.page_allowed("8.8.8.8", ["X-Forwarded-For"], None, "")
    for header in ("X-Forwarded-For", "x-real-ip", "Forwarded", "CF-Connecting-IP", "X-Forwarded-Host"):
        assert not pairing.page_allowed("172.18.0.5", ["Host", header], None, ""), header
    # The token opens it from anywhere, through a proxy too.
    assert pairing.page_allowed("8.8.8.8", ["X-Forwarded-For"], token, token)
    assert not pairing.page_allowed("8.8.8.8", [], "wrong", token)
    assert not pairing.page_allowed("8.8.8.8", [], "", token)
    # A token too short to resist guessing is ignored.
    assert not pairing.page_allowed("8.8.8.8", [], "short", "short")


def test_is_on():
    assert pairing.is_on(None) and pairing.is_on("") and pairing.is_on("1") and pairing.is_on("yes")
    for off in ("0", "false", "OFF", " no "):
        assert not pairing.is_on(off)


# ------------------------------------------------------------------ the page over HTTP

@pytest.fixture
def serve():
    servers = []

    def start(client_ip=None, **attrs):
        attrs = {"secret": SECRET, "public_url": "", "pairing_page": True, "pairing_token": "", **attrs}
        if client_ip is not None:
            attrs["client_ip"] = lambda self: client_ip
        handler = type("PairHandler", (receiver.Handler,), attrs)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    yield start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


def test_page_from_loopback(serve):
    base = serve(public_url="http://192.168.1.10:8080")
    status, headers, html = get(base + "/pair")
    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    # Plain HTML: no scripts and nothing loaded from elsewhere.
    assert "<svg" in html
    assert "<script" not in html and "<link" not in html and " src=" not in html and "url(" not in html
    link = pairing.pairing_url("http://192.168.1.10:8080/webhook/phone", SECRET)
    assert link.replace("&", "&amp;") in html
    assert SECRET in html


def test_page_label_and_guess_from_host(serve):
    base = serve()
    status, _, html = get(base + "/pair?label=pixel", {"Host": "192.168.1.10:28080"})
    assert status == 200
    assert "http://192.168.1.10:28080/webhook/pixel" in html
    assert "the one you opened this page with" in html
    status, _, html = get(base + "/pair?label=Bad%20Label", {"Host": "192.168.1.10:28080"})
    assert status == 200 and "<svg" not in html and SECRET not in html
    # Opened on the server itself, the guess is useless to a phone, and the page says so.
    _, _, html = get(base + "/pair")
    assert "cannot reach it" in html


def test_page_without_secret_recommends_one(serve):
    base = serve(secret="", public_url="http://192.168.1.10:8080")
    status, _, html = get(base + "/pair")
    assert status == 200
    assert "Set a signing secret first" in html and "WEBHOOK_SECRET=" in html and "<svg" not in html


def test_page_refused_from_outside_or_through_a_proxy(serve):
    assert get(serve(client_ip="203.0.113.9") + "/pair")[0] == 404
    assert get(serve(client_ip="2001:db8::1") + "/pair")[0] == 404
    local = serve()
    assert get(local + "/pair", {"X-Forwarded-For": "127.0.0.1"})[0] == 404
    assert get(local + "/pair", {"X-Real-IP": "192.168.1.2"})[0] == 404
    status, _, body = get(serve(pairing_page=False) + "/pair")
    assert status == 404 and body == '{"error": "not found"}'


def test_page_with_token(serve, capfd):
    token = "pairing-token-0123456789"
    base = serve(client_ip="203.0.113.9", pairing_token=token)
    assert get(base + "/pair?token=nope")[0] == 404
    status, _, html = get(base + f"/pair?token={token}&label=pixel", {"X-Forwarded-For": "203.0.113.9"})
    assert status == 200 and "/webhook/pixel" in html
    # The form for another phone carries it along.
    assert f'name="token" value="{token}"' in html
    time.sleep(0.2)
    out = capfd.readouterr()
    assert token not in out.out + out.err
    assert "token=<hidden>" in out.out


# ------------------------------------------------------------------ the log block

def test_log_block():
    text = pairing.log_block("http://192.168.1.10:8080", SECRET)
    link = pairing.pairing_url("http://192.168.1.10:8080/webhook/phone", SECRET)
    assert link in text and SECRET in text and "█" in text
    text = pairing.log_block("", SECRET)
    assert "PUBLIC_URL is not set" in text and SECRET not in text and "█" not in text
    text = pairing.log_block("http://192.168.1.10:8080", "")
    assert "WEBHOOK_SECRET is empty" in text and "WEBHOOK_SECRET=" in text and "█" not in text
    text = pairing.log_block("192.168.1.10:8080", SECRET)
    assert "does not work" in text and SECRET not in text


def run_receiver(dsn, until, **env):
    """Start receiver.py against a database, read its output up to a line, then stop it."""
    full = dict(os.environ, WEBHOOK_SECRET=SECRET, PORT="0", DATABASE_URL=dsn, TZ="UTC",
                PUBLIC_URL="http://192.168.1.10:8080", **env)
    for name in ("PAIRING_LOG", "PAIRING_PAGE", "PAIRING_TOKEN"):
        if name not in env:
            full.pop(name, None)
    proc = subprocess.Popen([sys.executable, str(RECEIVER_DIR / "receiver.py")], env=full,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = []
    timer = threading.Timer(60, proc.kill)
    timer.start()
    try:
        for line in proc.stdout:
            lines.append(line)
            if until in line:
                break
        time.sleep(0.5)  # anything printed right after the line
        proc.send_signal(signal.SIGTERM)
        rest, _ = proc.communicate(timeout=10)
        lines.append(rest)
    finally:
        timer.cancel()
        proc.kill()
    return "".join(lines)


def test_log_shows_the_code_at_start(db):
    out = run_receiver(db, "PAIRING_LOG=0 hides this block")
    assert "database ready" in out
    assert pairing.pairing_url("http://192.168.1.10:8080/webhook/phone", SECRET) in out
    assert SECRET in out and "█" in out


def test_log_switch_keeps_the_secret_out(db):
    out = run_receiver(db, "database ready", PAIRING_LOG="0")
    assert "database ready" in out
    assert SECRET not in out
    assert "Pair a phone" not in out and "█" not in out
