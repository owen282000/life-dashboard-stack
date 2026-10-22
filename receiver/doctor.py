"""The doctor: checks a running stack from inside the receiver's container.

    docker compose exec receiver python doctor.py
    kubectl -n life-dashboard exec deploy/lifedash-life-dashboard-receiver -- python doctor.py

It reads the receiver's own settings from the environment and checks them, the database,
the migrations, the two read-only roles, the receiver's HTTP port, the API when it is on,
and when each phone last sent data. Every check prints one line, PASS, WARN, FAIL or INFO,
and every WARN and FAIL says what to do about it. The exit code is 1 when a check failed,
0 otherwise.

It changes nothing: it only reads, and it logs in as grafana_ro and dash_ro to prove their
passwords work. It never prints a secret or a password. install.sh doctor runs it after its
own checks of the containers and the ports, which only the host can see.

Whether a phone reaches the receiver is the one thing no check on the server can tell, so the
doctor ends with the test to do on the phone itself.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

import api
import pairing

HERE = Path(__file__).resolve().parent
MIGRATIONS_DIR = HERE / "migrations"
DEFAULT_DSN = "postgresql://lifedash:lifedash@db:5432/lifedash"
QUIET_HOURS = 24      # the overview colors the last sync red after 24 hours, too
HTTP_TIMEOUT = 4

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"


@dataclass
class Result:
    status: str
    name: str
    message: str
    fix: str | None = None

    def lines(self) -> list[str]:
        out = [f"{self.status}  {self.name}: {self.message}"]
        if self.fix:
            out.append(f"      fix: {self.fix}")
        return out


# ------------------------------------------------------------------ settings

# The read-only roles' passwords before 1.2; a new install no longer starts with them.
OLD_DEFAULT_PASSWORDS = {"GRAFANA_DB_PASSWORD": "lifedash-ro", "DASH_DB_PASSWORD": "lifedash-dash-ro"}


def check_settings(env) -> list[Result]:
    """The settings that need no database: the secret, the address, the time zone, tokens."""
    out = []
    secret = env.get("WEBHOOK_SECRET", "")
    problem = pairing.secret_problem(secret)
    if not secret:
        out.append(Result(FAIL, "WEBHOOK_SECRET", "empty: the receiver stores unsigned payloads from "
                          "anyone who can reach it, and there is no pairing code",
                          "set WEBHOOK_SECRET to the output of openssl rand -hex 32 and restart the receiver"))
    elif problem:
        out.append(Result(FAIL, "WEBHOOK_SECRET", problem,
                          "use a long random string without spaces, such as the output of openssl rand -hex 32"))
    elif len(secret) < 16:
        out.append(Result(WARN, "WEBHOOK_SECRET", f"set, but only {len(secret)} characters",
                          "use a longer one, such as the output of openssl rand -hex 32, and pair the phones again"))
    else:
        out.append(Result(PASS, "WEBHOOK_SECRET", f"set ({len(secret)} characters)"))

    out.append(check_public_url(env.get("PUBLIC_URL", "").strip()))

    tz = env.get("TZ", "")
    if not tz:
        out.append(Result(WARN, "TZ", "not set, so days and nights follow UTC",
                          "set TZ to your time zone, such as Europe/Amsterdam, and restart the receiver"))
    elif tz == "UTC":
        out.append(Result(PASS, "TZ", "UTC (the default: set TZ to your own zone if you don't live in UTC)"))
    else:
        out.append(Result(PASS, "TZ", tz))

    token = env.get("PAIRING_TOKEN", "").strip()
    if token and len(token) < pairing.MIN_TOKEN:
        out.append(Result(WARN, "PAIRING_TOKEN", f"shorter than {pairing.MIN_TOKEN} characters, so it is ignored",
                          "use a longer one, or leave it empty"))
    for name, default in OLD_DEFAULT_PASSWORDS.items():
        if name in env and (env[name] or default) == default:
            out.append(Result(WARN, name, f"the old default ({default}), the same in every stack that kept it",
                              f"set {name} in .env to the output of openssl rand -hex 16, then docker compose up -d "
                              "(the receiver sets the new password at start)"))
    return out


def check_public_url(base: str) -> Result:
    if not base:
        return Result(WARN, "PUBLIC_URL", "not set, so there is no pairing code in the log, and "
                      "python pairing.py has none either",
                      "set PUBLIC_URL to the address your phone reaches this receiver on, such as "
                      "http://192.168.1.10:8080, and restart the receiver")
    problem = pairing.base_problem(base)
    if problem:
        return Result(FAIL, "PUBLIC_URL", f"{base} does not work: {problem}",
                      "set it to an address such as http://192.168.1.10:8080")
    if is_loopback(base):
        return Result(FAIL, "PUBLIC_URL", f"{base} is this machine as seen from itself, which a phone cannot reach",
                      "use the address of this machine on your network, such as http://192.168.1.10:8080")
    if pairing.address_notes(base):
        return Result(WARN, "PUBLIC_URL", f"{base}: an iPhone refuses plain http:// to a host name",
                      "for an iPhone, use the IP address of this machine or an https:// address")
    return Result(PASS, "PUBLIC_URL", base)


def is_loopback(base: str) -> bool:
    """localhost or 127.0.0.1: this machine as seen from itself, never what a phone reaches."""
    return any(note.startswith("This address is this machine") for note in pairing.address_notes(base))


# ------------------------------------------------------------------ database

def describe_error(exc: Exception) -> str:
    text = str(exc).strip().splitlines()
    return text[0] if text else type(exc).__name__


def connect(dsn: str, **kwargs):
    """psycopg.connect, tried again for a moment when the container's DNS has a hiccup.

    Podman's DNS (aardvark) can briefly fail to resolve a service name right after a start,
    which is not what the doctor is meant to report."""
    for attempt in range(3):
        try:
            return psycopg.connect(dsn, connect_timeout=5, **kwargs)
        except psycopg.OperationalError as exc:
            if attempt == 2 or "resolve host" not in str(exc):
                raise
            time.sleep(2)


def check_database(dsn: str, env, quiet_hours: float) -> list[Result]:
    try:
        conn = connect(dsn, autocommit=True)
    except psycopg.Error as exc:
        return [Result(FAIL, "database", f"no connection: {describe_error(exc)}",
                       "check that the database runs (docker compose ps db) and that DATABASE_URL is right")]
    out = []
    with conn:
        version = conn.info.server_version
        out.append(Result(PASS, "database", f"connected to Postgres {version // 10000}.{version % 10000}"
                          f" as {conninfo_to_dict(dsn).get('user', '?')}"))
        out.append(check_migrations(conn))
        views = check_views(conn)
        out.append(views)
        tz = env.get("TZ", "") or "UTC"
        if conn.execute("SELECT 1 FROM pg_timezone_names WHERE name = %s", (tz,)).fetchone() is None:
            out.append(Result(FAIL, "TZ", f"{tz} is not a time zone name Postgres knows",
                              "use an IANA name such as Europe/Amsterdam or UTC"))
        size = conn.execute("SELECT pg_size_pretty(pg_database_size(current_database()))").fetchone()[0]
        out.append(Result(INFO, "database size", size))
        if views.status == PASS:
            out += check_phones(conn, quiet_hours)
    out.append(check_role(dsn, "grafana_ro", env, "GRAFANA_DB_PASSWORD", "lifedash-ro",
                          "dash.activity_days"))
    out.append(check_role(dsn, api.ROLE, env, "DASH_DB_PASSWORD", "lifedash-dash-ro", "dash.installs"))
    return out


def check_migrations(conn) -> Result:
    files = sorted(p.name for p in MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))
    if conn.execute("SELECT to_regclass('public.schema_migrations')").fetchone()[0] is None:
        return Result(FAIL, "migrations", "none applied: the receiver has not set up this database",
                      "start the receiver and look at its log (docker compose logs receiver)")
    done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    missing = [f for f in files if f not in done]
    unknown = sorted(done - set(files))
    if missing:
        return Result(FAIL, "migrations", f"not applied: {', '.join(missing)}",
                      "restart the receiver, which applies them at start, and look at its log")
    if unknown:
        return Result(WARN, "migrations", f"the database has {', '.join(unknown)}, which this receiver "
                      "doesn't know: it is older than the database",
                      "run the version that set up this database, or a newer one")
    return Result(PASS, "migrations", f"all {len(files)} applied, the latest {files[-1]}")


def check_views(conn) -> Result:
    count = conn.execute("SELECT count(*) FROM pg_views WHERE schemaname = 'dash'").fetchone()[0]
    if not count:
        return Result(FAIL, "dash views", "missing", "restart the receiver, which rebuilds them at start")
    return Result(PASS, "dash views", f"{count} views")


def check_role(owner_dsn: str, role: str, env, password_env: str, default: str, view: str) -> Result:
    """Log in as the read-only role with the password the stack gives it, and read one view."""
    if password_env not in env:
        # The receiver doesn't set this role's password here (CloudNativePG's managed roles do),
        # so there is no password to try. The role must exist with its grants.
        return check_role_grants(owner_dsn, role, view)
    password = env.get(password_env) or default
    dsn = make_conninfo(owner_dsn, user=role, password=password)
    try:
        with connect(dsn) as conn:
            conn.execute(f"SELECT 1 FROM {view} LIMIT 1").fetchall()
            read_only = conn.execute("SHOW default_transaction_read_only").fetchone()[0]
    except psycopg.Error as exc:
        reason = describe_error(exc)
        fix = (f"{password_env} must be the same for every service that reads it; after a change, "
               "restart the receiver first (docker compose up -d), which sets the role's password")
        if "permission denied" in reason:
            fix = "restart the receiver, which grants the role its reads at start"
        return Result(FAIL, role, f"cannot read {view}: {reason}", fix)
    if read_only != "on":
        return Result(WARN, role, "logs in and reads, but its sessions are not read-only",
                      f"ALTER ROLE {role} SET default_transaction_read_only = on")
    return Result(PASS, role, f"logs in with {password_env} and reads {view}, read-only")


def check_role_grants(owner_dsn: str, role: str, view: str) -> Result:
    try:
        with connect(owner_dsn) as conn:
            row = conn.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            if row is None:
                return Result(FAIL, role, "the role does not exist",
                              "create it as in docs/kubernetes.md, then restart the receiver")
            can_read = conn.execute("SELECT has_table_privilege(%s, %s, 'SELECT')", (role, view)).fetchone()[0]
    except psycopg.Error as exc:
        return Result(FAIL, role, f"cannot check the role: {describe_error(exc)}")
    if not row[0] or not can_read:
        return Result(FAIL, role, f"exists, but {'cannot log in' if not row[0] else f'cannot read {view}'}",
                      "restart the receiver, which grants it its reads at start")
    return Result(PASS, role, f"exists and may read {view} (its password is managed outside the receiver)")


def check_phones(conn, quiet_hours: float) -> list[Result]:
    """Each phone label: when it last sent data, and when it last made contact at all."""
    rows = conn.execute(
        """
        SELECT i.install,
               string_agg(DISTINCT i.platform, ', ' ORDER BY i.platform) AS platforms,
               max(i.last_received) AS last_data,
               (SELECT max(p.received_at) FROM payloads p WHERE p.install = i.install) AS last_contact,
               extract(epoch FROM now() - max(i.last_received))::float8 / 3600 AS data_hours,
               extract(epoch FROM now() - (SELECT max(p.received_at) FROM payloads p
                                           WHERE p.install = i.install))::float8 / 3600 AS contact_hours
        FROM dash.installs i
        GROUP BY i.install
        ORDER BY i.install
        """
    ).fetchall()
    if not rows:
        return [Result(WARN, "phones", "no phone has sent anything yet",
                       "pair a phone (python pairing.py <label>, or the /pair page) and tap Sync Now in the app")]
    out = []
    for label, platforms, last_data, last_contact, data_hours, contact_hours in rows:
        name = f"phone {label}"
        if last_data is None:
            out.append(Result(WARN, name, f"only test pings so far, the last {ago(contact_hours)} ago",
                              "tap Sync Now in the app, then Backfill"))
        elif data_hours > quiet_hours:
            contact = ""
            if contact_hours is not None and contact_hours < data_hours - 1:
                contact = f"; its last ping {ago(contact_hours)} ago"
            out.append(Result(WARN, name, f"quiet: last data {ago(data_hours)} ago ({platforms}){contact}",
                              "open the app on that phone: it shows the last sync and any error. "
                              "Check that the phone can reach PUBLIC_URL and that background sync is allowed"))
        else:
            out.append(Result(PASS, name, f"last data {ago(data_hours)} ago ({platforms})"))
    return out


def ago(hours: float | None) -> str:
    if hours is None:
        return "never"
    if hours < 1:
        return f"{max(int(hours * 60), 0)} min"
    if hours < 48:
        return f"{hours:.0f} h"
    return f"{hours / 24:.0f} days"


# ------------------------------------------------------------------ HTTP

def http_get(url: str, headers: dict | None = None) -> tuple[int | None, str]:
    """The status and the start of the body, or None and the reason it failed."""
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:  # noqa: S310
            return response.status, response.read(200).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return None, str(reason)


def check_http(env, port: int) -> list[Result]:
    out = []
    local = f"http://127.0.0.1:{port}"
    status, body = http_get(local + "/health")
    if status == 200:
        out.append(Result(PASS, "receiver /health", f"200 on port {port} inside the container"))
    else:
        out.append(Result(FAIL, "receiver /health", f"{status or body} on port {port}",
                          "look at the receiver's log (docker compose logs receiver)"))
        return out

    base = env.get("PUBLIC_URL", "").strip()
    if base and not pairing.base_problem(base) and not is_loopback(base):
        status, body = http_get(base.rstrip("/") + "/health")
        if status == 200:
            out.append(Result(PASS, "PUBLIC_URL from the container", f"{base}/health answers 200"))
        else:
            out.append(Result(WARN, "PUBLIC_URL from the container", f"{base}/health: {status or body}",
                              "not always a problem: many routers don't let a machine reach its own public "
                              "or LAN address from inside a container (no hairpin NAT). Run install.sh doctor "
                              "on the host, and test from the phone as below"))

    token, warning = api_token(env)
    if warning:
        out.append(Result(WARN, "API", warning, "use a token of at least 16 characters, or leave API_TOKEN empty"))
    elif not token:
        out.append(Result(INFO, "API", "off (API_TOKEN is empty)"))
    else:
        without, _ = http_get(local + "/api/phones")
        with_token, _ = http_get(local + "/api/phones", {"Authorization": f"Bearer {token}"})
        if without == 401 and with_token == 200:
            out.append(Result(PASS, "API", "on: /api/phones answers 200 with the token and 401 without"))
        else:
            out.append(Result(FAIL, "API", f"/api/phones answers {with_token} with the token and {without} without",
                              "check DASH_DB_PASSWORD and the receiver's log"))
    return out


def api_token(env) -> tuple[str, str | None]:
    token = env.get("API_TOKEN", "").strip()
    if token and len(token) < api.MIN_TOKEN_LENGTH:
        return "", f"API_TOKEN is shorter than {api.MIN_TOKEN_LENGTH} characters, so the API stays off"
    return token, None


def check_mcp(url: str) -> list[Result]:
    """The MCP server's /health and that it refuses a request without its token."""
    url = url.rstrip("/")
    status, body = http_get(url + "/health")
    if status != 200:
        return [Result(FAIL, "MCP server", f"{url}/health: {status or body}",
                       "look at its log (docker compose logs mcp)")]
    request = urllib.request.Request(url + "/mcp", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT):  # noqa: S310
            code = 200
    except urllib.error.HTTPError as exc:
        code = exc.code
    except (urllib.error.URLError, OSError) as exc:
        code = str(getattr(exc, "reason", exc))
    if code != 401:
        return [Result(FAIL, "MCP server", f"/mcp without a token answers {code}, not 401",
                       "set MCP_TOKEN and restart the MCP server")]
    return [Result(PASS, "MCP server", "/health answers, and /mcp refuses a request without the token")]


def phone_test(env) -> Result:
    base = env.get("PUBLIC_URL", "").strip().rstrip("/") or "http://<address of this machine>:8080"
    return Result(INFO, "phone", "no check on the server can tell whether your phone reaches it. On the "
                  f"phone, on the network it syncs from, open {base}/health in the browser: "
                  '{"status": "ok"} means it does. In the app, Test ping in the Webhook card does the same')


# ------------------------------------------------------------------ main

def run(env, dsn: str, port: int, quiet_hours: float = QUIET_HOURS, mcp_url: str | None = None,
        http: bool = True) -> list[Result]:
    results = check_settings(env)
    results += check_database(dsn, env, quiet_hours)
    if http:
        results += check_http(env, port)
    if mcp_url:
        results += check_mcp(mcp_url)
    results.append(phone_test(env))
    return results


def report(results: list[Result]) -> str:
    lines = [line for r in results for line in r.lines()]
    counts = {s: sum(r.status == s for r in results) for s in (PASS, WARN, FAIL)}
    lines.append(f"{counts[PASS]} passed, {counts[WARN]} warnings, {counts[FAIL]} failed")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python doctor.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--quiet-hours", type=float, default=QUIET_HOURS,
                        help="call a phone quiet after this many hours without data (default 24)")
    parser.add_argument("--mcp-url", help="also check the MCP server at this address, such as http://mcp:8090")
    parser.add_argument("--json", action="store_true", help="print the results as JSON")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    results = run(env, env.get("DATABASE_URL") or DEFAULT_DSN, int(env.get("PORT", "8080")),
                  args.quiet_hours, args.mcp_url)
    if args.json:
        print(json.dumps([r.__dict__ for r in results], indent=2))
    else:
        print(report(results))
    return 1 if any(r.status == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
