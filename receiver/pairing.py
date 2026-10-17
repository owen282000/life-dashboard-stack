"""Pairing: the QR code that the Life Dashboard Companion apps scan.

A pairing code is an https link with everything the phone needs in the fragment, after
the #, which a browser never sends to a server (RFC 3986, section 3.5):

    https://owen282000.github.io/life-dashboard-companion-app/pair#v=1&url=...&secret=...&name=...

It is the format the Home Assistant integration hands out (its pairing.py), and both apps
parse it (PairingLink.kt on Android, PairingLink.swift on iOS). It carries an address and
a signing secret and nothing else: which data the phone sends stays a choice made on the
phone. There is no sources field, which the apps read as "both Health and Screen Time",
and that is what this receiver takes.

The receiver shows the code in two places: a block in its log at startup, and the page
at /pair, which is only served to your own network (see page_allowed). The same block
comes on demand from python pairing.py [label], with PUBLIC_URL and WEBHOOK_SECRET from the
environment, for instance through kubectl exec with the log and the page both off.

Nothing here touches the database.
"""

from __future__ import annotations

import hmac
import ipaddress
import re
import secrets
from html import escape
from urllib.parse import quote, urlencode, urlsplit

import segno

PAIR_PAGE = "https://owen282000.github.io/life-dashboard-companion-app/pair"
PAIR_VERSION = "1"
NAME = "Life Dashboard Stack"
DEFAULT_LABEL = "phone"
LABEL_RE = re.compile(r"^[a-z0-9_-]{1,32}$")

# The limits both apps apply. A code past them is refused on the phone, so it is refused here
# first, with a reason the operator can act on.
MAX_URL = 2048
MAX_SECRET = 512
MIN_TOKEN = 16

# Headers a reverse proxy, a tunnel or a CDN adds. Their presence means the request did not
# come from the network the TCP address suggests, so the page is refused. Their content is
# never trusted, since anyone can send them.
FORWARD_HEADERS = frozenset({
    "forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip", "cf-connecting-ip",
    "true-client-ip",
})

# Your own network: loopback, RFC 1918, link-local, the carrier-grade range that Tailscale
# hands out, and their IPv6 counterparts. The same list the iOS app uses to call an http://
# address a home network one.
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
    "100.64.0.0/10", "::1/128", "fc00::/7", "fe80::/10",
))

_HOST_HEADER_RE = re.compile(r"^(?:[A-Za-z0-9.-]{1,253}|\[[0-9A-Fa-f:.]{2,45}\])(?::\d{1,5})?$")


def is_on(value: str | None, default: bool = True) -> bool:
    """An on/off setting from the environment. Empty means the default."""
    text = (value or "").strip().lower()
    if not text:
        return default
    return text not in {"0", "false", "off", "no"}


# ------------------------------------------------------------------ the link

def pairing_url(webhook_url: str, secret: str, *, name: str = NAME) -> str:
    """The link the QR code carries, built exactly as the Home Assistant integration does.

    Every value is fully percent-encoded, nothing left "safe", so the apps can split the
    fragment on & and = before decoding.
    """
    fragment = urlencode(
        {"v": PAIR_VERSION, "url": webhook_url, "secret": secret, "name": name},
        quote_via=quote,
        safe="",
    )
    return f"{PAIR_PAGE}#{fragment}"


def webhook_url(base: str, label: str) -> str:
    return base.rstrip("/") + "/webhook/" + label


def base_problem(base: str) -> str | None:
    """Why the apps would refuse a webhook URL built on this address, or None."""
    if not base:
        return "no address"
    if len(base) + 50 > MAX_URL:
        return "the address is too long"
    if any(c.isspace() for c in base) or "," in base:
        return "the address contains a space or a comma"
    try:
        parts = urlsplit(base)
        parts.port  # noqa: B018 - raises on a port that is not a number
    except ValueError:
        return "the address is not a valid URL"
    if parts.scheme.lower() not in ("http", "https"):
        return "the address has to start with http:// or https://"
    if not parts.hostname or not parts.hostname.isascii():
        return "the address has no host name, or one with characters other than ASCII"
    if parts.username is not None or parts.password is not None:
        return "the address must not contain a user name or password"
    if parts.query or parts.fragment or "?" in base or "#" in base:
        return "the address must not contain ? or #"
    return None


def secret_problem(secret: str) -> str | None:
    """Why the apps would refuse this signing secret in a code, or None."""
    if not secret:
        return "WEBHOOK_SECRET is empty, and the apps refuse a pairing code without a secret"
    if len(secret) > MAX_SECRET:
        return f"WEBHOOK_SECRET is longer than the {MAX_SECRET} characters the apps accept"
    if any(c.isspace() for c in secret):
        return "WEBHOOK_SECRET contains a space, which the apps refuse in a pairing code"
    return None


def suggest_secret() -> str:
    return secrets.token_hex(32)


def address_notes(base: str) -> list[str]:
    """Things about a usable address that still stop a phone from reaching it."""
    notes = []
    parts = urlsplit(base)
    host = (parts.hostname or "").lower().rstrip(".")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if host == "localhost" or host.endswith(".localhost") or (ip is not None and ip.is_loopback):
        notes.append("This address is this machine as seen from itself. A phone cannot reach it: "
                     "use the address of this machine on your network, such as http://192.168.1.10:8080.")
    elif parts.scheme.lower() == "http" and ip is None and "." in host and not host.endswith(".local"):
        notes.append("An iPhone refuses plain http:// to a name like this one. For an iPhone, use "
                     "the IP address of this machine or an https:// address.")
    return notes


# ------------------------------------------------------------------ the QR code

def make_qr(link: str) -> segno.QRCode:
    # Error correction M, as the Home Assistant integration uses: a 64-character secret and a
    # LAN address give a symbol of 61 modules, which a phone reads through camera blur.
    return segno.make_qr(link, error="m")


def terminal_qr(link: str, border: int = 2) -> str:
    """The code in text, two modules per line, for a terminal with a dark background.

    Light modules are drawn as blocks and dark ones are left to the terminal's background,
    the way qrencode -t UTF8 does. No color codes, so it survives log viewers that show
    them as text. A quiet zone of 2 keeps a typical code, with Docker's log prefix, inside
    80 columns. On a light terminal the code shows inverted, which the Android app's
    scanner reads as well; the /pair page shows it the right way round everywhere.
    """
    rows = [list(row) for row in make_qr(link).matrix_iter(scale=1, border=border)]
    if len(rows) % 2:
        rows.append([1] * len(rows[0]))  # dark, as the terminal background below is
    lines = []
    for top, bottom in zip(rows[0::2], rows[1::2]):
        line = []
        for t, b in zip(top, bottom):
            light_top, light_bottom = not t, not b
            line.append("█" if light_top and light_bottom
                        else "▀" if light_top
                        else "▄" if light_bottom
                        else " ")
        lines.append("".join(line))
    return "\n".join(lines)


def svg_qr(link: str) -> str:
    return make_qr(link).svg_inline(scale=1, border=4, dark="#000", light="#fff", omitsize=True,
                                    svgclass="qr")


# ------------------------------------------------------------------ the log block

def page_url(base: str) -> str:
    return base.rstrip("/") + "/pair"


def another_phone(public_url: str, label: str, page: bool, token: bool) -> list[str]:
    """Where the code for another phone comes from: the /pair page when it is on, else pairing.py."""
    if not page:
        return [f"Phone label: {label}. For another phone, run python pairing.py <label> in the",
                "receiver's container: every phone needs its own label."]
    lines = [f"Phone label: {label}. For another phone, open {page_url(public_url)}?label=<label>",
             "from your own network: every phone needs its own label."]
    if token:
        lines.append("From outside it, add &token=<PAIRING_TOKEN>.")
    return lines


def log_block(public_url: str, secret: str, label: str = DEFAULT_LABEL, at_start: bool = True,
              *, page: bool = True, token: bool = False) -> str:
    """What the receiver prints at startup when PAIRING_LOG is on, or pairing.py on demand.

    page says whether /pair is on (PAIRING_PAGE), token whether PAIRING_TOKEN opens it from
    outside your own network, so the block points to the way that works.
    """
    rule = "=" * 72
    out = [rule, "Pair a phone with this receiver", ""]
    problem = secret_problem(secret)
    if problem:
        out += [
            f"No pairing code: {problem}.",
            "Set WEBHOOK_SECRET and restart the receiver, for example with this freshly",
            "generated value, which is not in use anywhere yet:",
            "",
            f"  WEBHOOK_SECRET={suggest_secret()}",
        ]
    elif not public_url and not page:
        out += [
            "No pairing code: PUBLIC_URL is not set, and from inside its container the receiver",
            "cannot tell which address your phone reaches it on. Set PUBLIC_URL to that address,",
            "such as http://192.168.1.10:8080, and restart the receiver; this block then shows",
            "the code.",
        ]
    elif not public_url:
        out += [
            "No pairing code: PUBLIC_URL is not set, and from inside its container the receiver",
            "cannot tell which address your phone reaches it on. Either:",
            "",
            "- set PUBLIC_URL to that address, such as http://192.168.1.10:8080, and",
            "  restart the receiver; this block then shows the code, or",
            "- open /pair on this receiver in a browser on your own network, such as",
            "  http://<address of this machine>:8080/pair. That page shows a code for the",
            "  address you opened it with.",
        ]
    elif base_problem(public_url):
        out += [f"No pairing code: PUBLIC_URL={public_url!r} does not work: {base_problem(public_url)}."]
    else:
        hook = webhook_url(public_url, label)
        link = pairing_url(hook, secret)
        out += [
            "Scan this code in Life Dashboard Companion (Webhook card, Scan a pairing code),",
            "or with the phone's camera, check the address it shows and tap Pair.",
            "",
            terminal_qr(link),
            "",
            *another_phone(public_url, label, page, token),
            "",
            "The same as a link (open it on the phone itself):",
            link,
            "",
            "Or type it in by hand:",
            f"  Webhook URL:  {hook}",
            f"  Secret:       {secret}",
        ]
        for note in address_notes(public_url):
            out += ["", note]
        out += ["", "Anyone with the secret can send data to this receiver and delete records in it."]
    if at_start:
        out += ["", "PAIRING_LOG=0 hides this block."]
    out.append(rule)
    return "\n".join(out)


# ------------------------------------------------------------------ the page

def is_private_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in net for net in PRIVATE_NETWORKS)


def token_matches(given: str | None, token: str) -> bool:
    if len(token) < MIN_TOKEN or not given:
        return False
    return hmac.compare_digest(given.encode(), token.encode())


def page_allowed(client_ip: str, header_names, given_token: str | None, token: str) -> bool:
    """Whether /pair may be shown to this request.

    The page holds the signing secret, so it goes only to a request whose TCP peer is on your
    own network and that carries no proxy header, or to one with the right PAIRING_TOKEN.
    A forwarding header is never believed, only taken as a sign that a proxy sits in between.
    """
    if token_matches(given_token, token):
        return True
    if {h.lower() for h in header_names} & FORWARD_HEADERS:
        return False
    return is_private_address(client_ip)


def guess_base(host_header: str | None) -> str | None:
    """The address this page was opened with, as the guess when PUBLIC_URL is not set."""
    host = (host_header or "").strip()
    if not _HOST_HEADER_RE.match(host):
        return None
    return "http://" + host


_STYLE = """
:root { color-scheme: light dark; --bg: #f6f7f9; --fg: #1b1f24; --muted: #59616b;
  --card: #fff; --line: #d9dde3; --warn-bg: #fff4e5; --warn-fg: #6b3d00; --accent: #2f6fde; }
@media (prefers-color-scheme: dark) { :root { --bg: #111418; --fg: #e6e9ed; --muted: #9aa3ad;
  --card: #1a1e24; --line: #2c323a; --warn-bg: #3a2a12; --warn-fg: #ffd9a3; --accent: #7aa7ff; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 720px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 1.6rem; margin: 0 0 4px; }
h2 { font-size: 1.1rem; margin: 28px 0 8px; }
p, li { color: var(--fg); }
.muted { color: var(--muted); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px; }
.pair { display: flex; flex-wrap: wrap; gap: 20px; align-items: flex-start; }
.qr { width: 280px; max-width: 100%; height: auto; background: #fff; border-radius: 8px;
  display: block; }
.steps { flex: 1 1 260px; margin: 0; padding-left: 20px; }
.warn { background: var(--warn-bg); color: var(--warn-fg); border-radius: 12px; padding: 12px 16px;
  margin: 16px 0; }
.warn p { color: inherit; margin: 4px 0; }
code, pre { font: 14px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
pre { background: var(--bg); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px;
  overflow-wrap: anywhere; white-space: pre-wrap; margin: 4px 0 12px; }
a { color: var(--accent); overflow-wrap: anywhere; }
form { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
input[type=text] { font: inherit; padding: 6px 10px; border: 1px solid var(--line); border-radius: 8px;
  background: var(--card); color: var(--fg); width: 14em; max-width: 100%; }
button { font: inherit; padding: 6px 14px; border-radius: 8px; border: 1px solid var(--line);
  background: var(--card); color: var(--fg); cursor: pointer; }
"""


def page_html(*, public_url: str, host_header: str | None, secret: str, label: str | None,
              token: str | None) -> str:
    """The /pair page: the code, the link, the steps and what the page gives away."""
    e = escape
    body: list[str] = ["<h1>Pair a phone</h1>",
                       '<p class="muted">Life Dashboard Stack: scan the code with Life Dashboard '
                       "Companion, and nobody has to type an address or a secret.</p>"]

    chosen = DEFAULT_LABEL if label is None or label == "" else label
    label_ok = bool(LABEL_RE.match(chosen))
    base = public_url or guess_base(host_header)
    problem = secret_problem(secret)

    if problem:
        body += [
            '<div class="warn"><p><strong>Set a signing secret first.</strong></p>',
            f"<p>{e(problem)}. Without a secret this receiver also stores unsigned data from "
            "anyone who can reach it.</p>",
            "<p>Set <code>WEBHOOK_SECRET</code> to a freshly generated value that is not in "
            "use anywhere yet, such as the one below, and restart the receiver. Then reload "
            "this page.</p>",
            f"<pre>WEBHOOK_SECRET={e(suggest_secret())}</pre></div>",
        ]
    elif not label_ok:
        body.append('<div class="warn"><p>A label is 1 to 32 lowercase letters, digits, '
                    "<code>-</code> and <code>_</code>. Pick another one below.</p></div>")
    elif base is None or base_problem(base):
        reason = base_problem(base) if base else "this page could not tell which address you used"
        body.append(f'<div class="warn"><p>No code: {e(reason)}. Set <code>PUBLIC_URL</code> '
                    "to the address your phone reaches this receiver on, such as "
                    "<code>http://192.168.1.10:8080</code>, and restart the receiver.</p></div>")
    else:
        hook = webhook_url(base, chosen)
        link = pairing_url(hook, secret)
        notes = address_notes(base)
        if not public_url:
            notes.insert(0, "This address is the one you opened this page with. If your phone "
                            "reaches this receiver on another one, set PUBLIC_URL and restart "
                            "the receiver.")
        body.append('<div class="card pair">')
        body.append(svg_qr(link))
        body += [
            '<ol class="steps">',
            "<li>In Life Dashboard Companion, open the <strong>Webhook</strong> card on the "
            "<strong>Health</strong> tab and tap <strong>Scan a pairing code</strong>. The "
            "phone's camera works too.</li>",
            f"<li>Check that the app shows <code>{e(urlsplit(hook).netloc)}</code> and tap "
            "<strong>Pair</strong>, on Android with Health and Screen Time both on. The app "
            "sends a test ping.</li>",
            "<li>Tap <strong>Sync Now</strong>. On Android, also on the "
            "<strong>Screen Time</strong> tab.</li>",
            "</ol></div>",
        ]
        for note in notes:
            body.append(f'<div class="warn"><p>{e(note)}</p></div>')
        body += [
            f'<p>Phone label: <code>{e(chosen)}</code>. It is how the dashboards tell phones '
            "apart, so every phone needs its own.</p>",
            "<h2>On the phone itself</h2>",
            '<p>Opened this page on the phone? Tap the link instead of scanning:</p>',
            f'<p><a href="{e(link)}" rel="noreferrer">{e(link)}</a></p>',
            "<h2>By hand</h2>",
            "<p>Paste both into the webhook settings on the <strong>Health</strong> tab, and on "
            "Android on the <strong>Screen Time</strong> tab as well.</p>",
            f"<p>Webhook URL</p><pre>{e(hook)}</pre>",
            f"<p>Signing secret</p><pre>{e(secret)}</pre>",
        ]

    body += [
        "<h2>Another phone</h2>",
        '<form method="get" action="/pair">',
        '<label for="label">Label</label>',
        f'<input type="text" id="label" name="label" value="{e(chosen)}" maxlength="32" '
        'pattern="[a-z0-9_\\-]{1,32}" autocomplete="off" spellcheck="false">',
    ]
    if token:
        body.append(f'<input type="hidden" name="token" value="{e(token)}">')
    body += [
        "<button type=\"submit\">Show its code</button></form>",
        '<p class="muted">Lowercase letters, digits, <code>-</code> and <code>_</code>, such as '
        "<code>pixel</code> or <code>iphone</code>.</p>",
        "<h2>Who can see this page</h2>",
        "<p>This page shows the signing secret. Anyone who has it can send data to this "
        "receiver and delete records in it, so treat it like a password: don't share a "
        "screenshot of this page or of the code.</p>",
        "<p>The receiver shows it only to a request from your own network (a private or "
        "loopback address) that did not come through a reverse proxy, or to one with the right "
        "<code>?token=</code> when <code>PAIRING_TOKEN</code> is set. Everything else gets a "
        "404. <code>PAIRING_PAGE=0</code> switches the page off.</p>",
    ]

    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        '<meta name="referrer" content="no-referrer">'
        "<title>Pair a phone</title>"
        f"<style>{_STYLE}</style></head><body><main>\n"
        + "\n".join(body)
        + "\n</main></body></html>\n"
    )


PAGE_HEADERS = {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                               "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
}


def main(argv: list[str]) -> int:
    """python pairing.py [label]: the pairing block for one phone, on demand."""
    import os

    label = argv[0] if argv else DEFAULT_LABEL
    if not LABEL_RE.match(label):
        print("A label is 1 to 32 lowercase letters, digits, - and _.")
        return 2
    print(log_block(os.environ.get("PUBLIC_URL", "").strip(), os.environ.get("WEBHOOK_SECRET", ""),
                    label, at_start=False, page=is_on(os.environ.get("PAIRING_PAGE")),
                    token=len(os.environ.get("PAIRING_TOKEN", "").strip()) >= MIN_TOKEN))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
