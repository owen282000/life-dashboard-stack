"""The read-only data API under /api/ on the receiver's port.

Off until API_TOKEN is set; while it is off every /api/ path answers 404. A request needs
"Authorization: Bearer <API_TOKEN>", compared in constant time. The API reads the dash
views through queries.py, as dash_ro: a database user the receiver sets up at every start
that can only SELECT from the dash schema. It never uses the receiver's own connection.
docs/api.md describes every endpoint.
"""

import hashlib
import hmac
import os
import sys
from urllib.parse import parse_qs, urlsplit

import psycopg
from psycopg.conninfo import make_conninfo

import queries
import roles

ROLE = "dash_ro"
MIN_TOKEN_LENGTH = 16
MAX_DAYS = 400       # the longest range one request may ask for
ROW_CAP = 5000       # rows per response; more sets "truncated": true
TOP_APPS = 50


def _token_from_env() -> tuple[str, str | None]:
    token = os.environ.get("API_TOKEN", "").strip()
    if token and len(token) < MIN_TOKEN_LENGTH:
        return "", (f"API_TOKEN is shorter than {MIN_TOKEN_LENGTH} characters, so the API under /api/ "
                    "stays off. Use a long random string, such as the output of openssl rand -hex 32.")
    return token, None


API_TOKEN, TOKEN_WARNING = _token_from_env()
DASH_DB_PASSWORD = os.environ.get("DASH_DB_PASSWORD", "") or "lifedash-dash-ro"
SETTINGS = {"statement_timeout": queries.STATEMENT_TIMEOUT, "default_transaction_read_only": "on"}


def dsn(owner_dsn: str, password: str = DASH_DB_PASSWORD) -> str:
    """The receiver's database URL with the dash_ro user in place of the owner."""
    return make_conninfo(owner_dsn, user=ROLE, password=password)


def setup_role(conn, password: str = DASH_DB_PASSWORD) -> None:
    """dash_ro: log in, SELECT on the dash views, nothing else. Runs after the views rebuild.

    Where the receiver's user may not manage roles, dash_ro must exist already (roles.py).
    """
    with conn.transaction():
        roles.ensure_login_role(conn, ROLE, password, SETTINGS, "DASH_DB_PASSWORD")
        conn.execute(f"REVOKE ALL ON records, buckets, payloads, schema_migrations, tombstones FROM {ROLE}")
        conn.execute(f"GRANT USAGE ON SCHEMA dash TO {ROLE}")
        conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA dash TO {ROLE}")


def status_line() -> str:
    if TOKEN_WARNING:
        return TOKEN_WARNING
    return f"read-only API under /api/: {'on' if API_TOKEN else 'off (API_TOKEN is empty)'}"


def _authorized(header: str | None, token: str) -> bool:
    scheme, _, given = (header or "").partition(" ")
    if scheme.lower() != "bearer":
        given = ""
    # Hash both sides first, so the comparison takes the same time for any length.
    return hmac.compare_digest(hashlib.sha256(given.strip().encode()).digest(),
                               hashlib.sha256(token.encode()).digest())


# ------------------------------------------------------------------ endpoints

def _range(reader, params, span=30):
    start, end = reader.default_range(params.get("from"), params.get("to"), span)
    queries.check_range(start, end, MAX_DAYS)
    return start, end


def _rows_body(reader, start, end, phone, rows, truncated, **extra):
    return {"timezone": reader.tz, "from": start.isoformat(), "to": end.isoformat(), "phone": phone,
            **extra, "rows": rows, "truncated": truncated}


def get_phones(reader, params):
    return {"timezone": reader.tz, "today": reader.today().isoformat(), "phones": reader.phones()}


def get_days(reader, params):
    phone = reader.require_phone(params.get("phone"))
    start, end = _range(reader, params)
    rows, truncated = reader.days(start, end, phone, ROW_CAP)
    return _rows_body(reader, start, end, phone, rows, truncated)


def get_sleep(reader, params):
    phone = reader.require_phone(params.get("phone"))
    start, end = _range(reader, params)
    rows, truncated = reader.sleep(start, end, phone, ROW_CAP)
    return _rows_body(reader, start, end, phone, rows, truncated)


def get_workouts(reader, params):
    phone = reader.require_phone(params.get("phone"))
    start, end = _range(reader, params)
    rows, truncated = reader.workouts(start, end, phone, ROW_CAP)
    return _rows_body(reader, start, end, phone, rows, truncated)


def get_screen_time(reader, params):
    phone = reader.require_phone(params.get("phone"))
    raw = params.get("date")
    day = queries.parse_day(raw, "date") if raw not in (None, "") else reader.today()
    return {"timezone": reader.tz, "date": day.isoformat(), "phone": phone,
            "phones": reader.screen_time(day, phone, TOP_APPS)}


def get_series(reader, params):
    metric = queries.check_metric(params.get("metric"))
    phone = reader.require_phone(params.get("phone"))
    start, end = _range(reader, params)
    rows, truncated = reader.series(metric, start, end, phone, ROW_CAP)
    spec = queries.METRICS[metric]
    return _rows_body(reader, start, end, phone, rows, truncated, metric=metric, unit=spec["unit"])


def get_trend(reader, params):
    metric = queries.check_metric(params.get("metric"))
    phone = reader.require_phone(params.get("phone"))
    raw = params.get("days") or "28"
    if not raw.isdigit() or not 7 <= int(raw) <= 365:
        raise queries.BadRequest("days must be a whole number from 7 to 365")
    return {"timezone": reader.tz, "metric": metric, "phone": phone,
            "phones": reader.trend(metric, int(raw), phone)}


def get_catalog(reader, params):
    phone = reader.require_phone(params.get("phone"))
    phones, truncated = reader.catalog(phone, ROW_CAP)
    return {"timezone": reader.tz, "today": reader.today().isoformat(), "phone": phone,
            "phones": phones, "truncated": truncated}


def get_sync(reader, params):
    phone = reader.require_phone(params.get("phone"))
    phones, truncated = reader.sync(phone, ROW_CAP)
    return {"timezone": reader.tz, "today": reader.today().isoformat(), "phone": phone,
            "phones": phones, "truncated": truncated}


ROUTES = {
    "/api/phones": (get_phones, set()),
    "/api/days": (get_days, {"from", "to", "phone"}),
    "/api/sleep": (get_sleep, {"from", "to", "phone"}),
    "/api/workouts": (get_workouts, {"from", "to", "phone"}),
    "/api/screen-time": (get_screen_time, {"date", "phone"}),
    "/api/series": (get_series, {"metric", "from", "to", "phone"}),
    "/api/trend": (get_trend, {"metric", "days", "phone"}),
    "/api/catalog": (get_catalog, {"phone"}),
    "/api/sync": (get_sync, {"phone"}),
}

UNAUTHORIZED_HEADERS = {"WWW-Authenticate": 'Bearer realm="life-dashboard"'}
JSON_HEADERS = {"Cache-Control": "no-store"}


def handle(target: str, authorization: str | None, token: str, conninfo: str) -> tuple[int, dict, dict]:
    """One GET request under /api/. Returns the status, the JSON body and extra headers."""
    if not token:
        return 404, {"error": "not found"}, {}
    if not _authorized(authorization, token):
        return 401, {"error": "missing or wrong bearer token"}, UNAUTHORIZED_HEADERS
    parts = urlsplit(target)
    route = ROUTES.get(parts.path.rstrip("/"))
    if route is None:
        return 404, {"error": "not found", "endpoints": sorted(ROUTES)}, JSON_HEADERS
    endpoint, allowed = route
    raw = parse_qs(parts.query, keep_blank_values=True)
    unknown = sorted(set(raw) - allowed)
    if unknown:
        names = ", ".join(sorted(allowed)) or "none"
        return 400, {"error": f"unknown parameter {unknown[0]}; allowed: {names}"}, JSON_HEADERS
    repeated = sorted(k for k, v in raw.items() if len(v) > 1)
    if repeated:
        return 400, {"error": f"parameter {repeated[0]} given more than once"}, JSON_HEADERS
    params = {k: v[0] for k, v in raw.items()}
    try:
        with queries.open_reader(conninfo) as reader:
            return 200, endpoint(reader, params), JSON_HEADERS
    except queries.BadRequest as exc:
        return 400, {"error": str(exc)}, JSON_HEADERS
    except queries.NotFound as exc:
        return 404, {"error": str(exc)}, JSON_HEADERS
    except psycopg.errors.QueryCanceled:
        return 503, {"error": "the query took too long; ask for a shorter range"}, JSON_HEADERS
    except psycopg.Error as exc:
        print(f"api: database error on {parts.path}: {type(exc).__name__}: {exc}".strip(),
              file=sys.stderr, flush=True)
        return 503, {"error": "database unavailable"}, JSON_HEADERS
