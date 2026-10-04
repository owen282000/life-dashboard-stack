"""Webhook receiver for Life Dashboard Companion payloads.

POST /webhook/<label> (or /webhook for the label "default") takes one payload from the
Android or iPhone app. The receiver checks the size and the HMAC signature, writes the
payload to the ledger (a body it has seen before is answered as a duplicate), applies the
deletions it names and then upserts its records, all in one transaction. Records are keyed
on their uuid, day records on type, install, source, device and date, and bucketed
series per time window. A write that is
older than the stored row on both sequence and build time is skipped, so a payload that
arrives late cannot undo a newer one.

On start it runs the SQL migrations in migrations/, rebuilds the dash views from
views.sql, and sets up the read-only grafana_ro role. /health responds 503 until that is
done. Dependencies stay small on purpose: the standard library's http.server plus psycopg.
"""

import hashlib
import hmac
import json
import os
import re
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg
from psycopg import sql

HERE = Path(__file__).resolve().parent
MIGRATIONS_DIR = HERE / "migrations"
VIEWS_FILE = HERE / "views.sql"

DB_DSN = os.environ.get("DATABASE_URL", "postgresql://lifedash:lifedash@db:5432/lifedash")
SECRET = os.environ.get("WEBHOOK_SECRET", "")
PORT = int(os.environ.get("PORT", "8080"))
TZ = os.environ.get("TZ", "") or "UTC"
GRAFANA_DB_PASSWORD = os.environ.get("GRAFANA_DB_PASSWORD", "") or "lifedash-ro"

MAX_BODY = 32 * 1024 * 1024
LABEL_RE = re.compile(r"^/webhook(?:/([a-z0-9_-]{1,32}))?$")
MIGRATION_LOCK = 4_242_017  # pg_advisory_lock key, the same constant in every receiver

# Envelope keys that are never record arrays.
ENVELOPE_KEYS = {
    "timestamp", "app_version", "source", "sequence", "device", "app_filter", "test",
    "message", "backfill", "window_start", "window_end", "window_complete", "writeback",
    "deleted_records", "deletions_unavailable", "records_outside_window", "_diagnostics",
    "_resolutions",
}
DAY_TYPES = {"daily_totals", "screen_time"}
TIME_FIELDS = ("time", "end_time", "session_end_time")

READY = threading.Event()

# The ordering rule: an incoming write loses only when it is older on sequence (or has
# none) AND older on build time. It applies against the stored row and against the
# tombstone a deletion left.
UPSERT_SQL = """
INSERT INTO records AS r
    (type, ts, uuid, source_app, payload_source, data, install, seq, built_at, day, device, start_ts)
SELECT v.*
FROM (VALUES (%s::text, %s::timestamptz, %s::text, %s::text, %s::text, %s::jsonb, %s::text,
              %s::bigint, %s::timestamptz, %s::date, %s::text, %s::timestamptz))
     AS v (type, ts, uuid, source_app, payload_source, data, install, seq, built_at, day, device, start_ts)
WHERE NOT EXISTS (
    SELECT 1 FROM tombstones t
    WHERE t.type = v.type AND t.uuid IN (v.uuid, split_part(v.uuid, '#', 1))
      AND coalesce(v.seq < t.seq, true) AND coalesce(v.built_at < t.built_at, false))
ON CONFLICT (uuid) DO UPDATE SET
    type = EXCLUDED.type,
    ts = EXCLUDED.ts,
    source_app = EXCLUDED.source_app,
    payload_source = EXCLUDED.payload_source,
    data = EXCLUDED.data,
    install = EXCLUDED.install,
    seq = EXCLUDED.seq,
    built_at = EXCLUDED.built_at,
    day = EXCLUDED.day,
    device = EXCLUDED.device,
    start_ts = EXCLUDED.start_ts,
    received_at = now()
WHERE NOT (coalesce(EXCLUDED.seq < r.seq, true) AND coalesce(EXCLUDED.built_at < r.built_at, false))
"""

DELETE_SQL = """
DELETE FROM records r
WHERE r.type = %(type)s
  AND (r.uuid = %(uuid)s OR r.record_id = %(uuid)s)
  AND NOT (coalesce(%(seq)s::bigint < r.seq, true)
           AND coalesce(%(built_at)s::timestamptz < r.built_at, false))
"""

TOMBSTONE_SQL = """
INSERT INTO tombstones AS t (type, uuid, seq, built_at)
VALUES (%(type)s, %(uuid)s, %(seq)s, %(built_at)s)
ON CONFLICT (type, uuid) DO UPDATE SET
    seq = EXCLUDED.seq, built_at = EXCLUDED.built_at, deleted_at = now()
WHERE NOT (coalesce(EXCLUDED.seq < t.seq, true) AND coalesce(EXCLUDED.built_at < t.built_at, false))
"""


BUCKET_SQL = """
INSERT INTO buckets AS b
    (install, type, payload_source, bucket_start, bucket_end, sample_count, avg, min, max, total,
     complete, sources, seq, built_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (install, type, payload_source, bucket_start) DO UPDATE SET
    bucket_end = CASE WHEN EXCLUDED.complete THEN EXCLUDED.bucket_end
                      ELSE greatest(b.bucket_end, EXCLUDED.bucket_end) END,
    sample_count = CASE WHEN EXCLUDED.complete THEN EXCLUDED.sample_count
                        ELSE coalesce(b.sample_count, 0) + coalesce(EXCLUDED.sample_count, 0) END,
    avg = CASE WHEN EXCLUDED.complete THEN EXCLUDED.avg
               WHEN b.avg IS NULL THEN EXCLUDED.avg
               WHEN EXCLUDED.avg IS NULL THEN b.avg
               WHEN coalesce(b.sample_count, 0) + coalesce(EXCLUDED.sample_count, 0) = 0
                    THEN (b.avg + EXCLUDED.avg) / 2
               ELSE (b.avg * coalesce(b.sample_count, 0) + EXCLUDED.avg * coalesce(EXCLUDED.sample_count, 0))
                    / (coalesce(b.sample_count, 0) + coalesce(EXCLUDED.sample_count, 0)) END,
    min = CASE WHEN EXCLUDED.complete THEN EXCLUDED.min ELSE least(b.min, EXCLUDED.min) END,
    max = CASE WHEN EXCLUDED.complete THEN EXCLUDED.max ELSE greatest(b.max, EXCLUDED.max) END,
    total = CASE WHEN EXCLUDED.complete THEN EXCLUDED.total
                 WHEN b.total IS NULL AND EXCLUDED.total IS NULL THEN NULL
                 ELSE coalesce(b.total, 0) + coalesce(EXCLUDED.total, 0) END,
    complete = EXCLUDED.complete,
    sources = CASE WHEN EXCLUDED.complete THEN EXCLUDED.sources
                   ELSE ARRAY(SELECT DISTINCT unnest(coalesce(b.sources, '{}') || coalesce(EXCLUDED.sources, '{}'))
                              ORDER BY 1) END,
    seq = CASE WHEN EXCLUDED.complete THEN EXCLUDED.seq ELSE greatest(b.seq, EXCLUDED.seq) END,
    built_at = CASE WHEN EXCLUDED.complete THEN EXCLUDED.built_at ELSE greatest(b.built_at, EXCLUDED.built_at) END,
    received_at = now()
WHERE NOT EXCLUDED.complete
   OR NOT (coalesce(EXCLUDED.seq < b.seq, true) AND coalesce(EXCLUDED.built_at < b.built_at, false))
"""

def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


# ------------------------------------------------------------------ parsing

_FRACTION_RE = re.compile(r"\.(\d+)")


def parse_time(value) -> datetime | None:
    """An ISO 8601 timestamp from the payload as an aware datetime, or None."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    # Python reads at most 6 fraction digits; the app sends up to 9.
    text = _FRACTION_RE.sub(lambda m: "." + m.group(1)[:6], text, count=1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def parse_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def as_number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def payload_kind(payload: dict, has_records: bool) -> str:
    if payload.get("test") is True:
        return "ping"
    if payload.get("source") == "screen_time":
        return "screen_time"
    if payload.get("backfill") is True:
        return "backfill"
    if has_records:
        return "sync"
    if any(k in payload for k in ("deleted_records", "deletions_unavailable", "records_outside_window")):
        return "deletion"
    return "ping"  # a heartbeat: writeback and nothing else


def build_rows(install: str, payload: dict, seq, built_at) -> tuple[list[tuple], list[tuple], int]:
    """The record rows and bucket rows of one payload, and how many objects were skipped."""
    if payload.get("test") is True:
        return [], [], 0
    payload_source = payload.get("source") if isinstance(payload.get("source"), str) else "unknown"
    device = payload.get("device") if isinstance(payload.get("device"), str) else ""
    app_filter = payload.get("app_filter") if isinstance(payload.get("app_filter"), str) else None
    rows, buckets, skipped = [], [], 0
    for rtype, value in payload.items():
        if rtype in ENVELOPE_KEYS or not isinstance(value, list):
            continue
        for record in value:
            if isinstance(record, dict) and "bucket_start" in record:
                row = bucket_row(rtype, install, payload_source, record, seq, built_at)
                target = buckets
            elif isinstance(record, dict):
                row = record_row(rtype, install, payload_source, device, app_filter, record, seq, built_at)
                target = rows
            else:
                row = None
            if row is None:
                skipped += 1
            else:
                target.append(row)
    return rows, buckets, skipped


def bucket_row(rtype, install, payload_source, bucket, seq, built_at):
    start, end = parse_time(bucket.get("bucket_start")), parse_time(bucket.get("bucket_end"))
    if start is None or end is None:
        return None
    count = bucket.get("sample_count")
    count = count if isinstance(count, int) and not isinstance(count, bool) else None
    sources = bucket.get("sources")
    sources = [x for x in sources if isinstance(x, str)] if isinstance(sources, list) else None
    return (
        install, rtype, payload_source, start, end, count,
        as_number(bucket.get("avg")), as_number(bucket.get("min")), as_number(bucket.get("max")),
        as_number(bucket.get("total")), bucket.get("complete") is True, sources, seq, built_at,
    )


def record_row(rtype, install, payload_source, device, app_filter, record, seq, built_at):
    day = None
    row_device = None
    if rtype in DAY_TYPES:
        day = parse_date(record.get("date"))
        if day is None:
            return None
        ts = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        dev = device if rtype == "screen_time" else ""
        uuid = f"{rtype}:{install}:{payload_source}:{dev}:{day.isoformat()}"
        if rtype == "screen_time":
            row_device = device
            if app_filter is not None:
                record = {**record, "_app_filter": app_filter}
    else:
        ts = None
        for field in TIME_FIELDS:
            ts = parse_time(record.get(field))
            if ts is not None:
                break
        if ts is None:
            return None
        uuid = record.get("uuid") if isinstance(record.get("uuid"), str) and record.get("uuid") else None
        if uuid is None:
            digest = hashlib.sha256(
                (install + "\0" + rtype + "\0" + json.dumps(record, sort_keys=True)).encode()
            ).hexdigest()
            uuid = f"synthetic-{digest[:32]}"
    start_ts = parse_time(record.get("start_time"))
    if start_ts is None:
        end = parse_time(record.get("session_end_time"))
        duration = as_number(record.get("duration_seconds"))
        if end is not None and duration is not None:
            start_ts = end - timedelta(seconds=duration)
    if start_ts is None:
        start_ts = ts
    source_app = record.get("source") if isinstance(record.get("source"), str) else None
    return (
        rtype, ts, uuid, source_app, payload_source,
        json.dumps(record, ensure_ascii=False).replace("\\u0000", ""),
        install, seq, built_at, day, row_device, start_ts,
    )


# ------------------------------------------------------------------ ingest

def ingest(dsn: str, install: str, body: bytes) -> tuple[int, dict]:
    """Store one payload in one transaction. Returns the HTTP status and response body."""
    try:
        payload = json.loads(body)
        text = body.decode("utf-8")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 400, {"error": "invalid json"}
    if not isinstance(payload, dict):
        return 400, {"error": "payload must be a JSON object"}

    sha = hashlib.sha256(body).hexdigest()
    source = payload.get("source") if isinstance(payload.get("source"), str) else None
    raw_seq = payload.get("sequence")
    seq = raw_seq if isinstance(raw_seq, int) and not isinstance(raw_seq, bool) else None
    built_at = parse_time(payload.get("timestamp"))
    app_version = payload.get("app_version") if isinstance(payload.get("app_version"), str) else None
    rows, buckets, skipped = build_rows(install, payload, seq, built_at)
    kind = payload_kind(payload, bool(rows) or bool(buckets) or skipped > 0)
    deletions = payload.get("deleted_records") if isinstance(payload.get("deleted_records"), list) else []
    deleted = 0

    with psycopg.connect(dsn) as conn:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO payloads (install, source, sequence, app_version, built_at, kind,
                                      record_count, body_sha256, body)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (body_sha256) DO NOTHING
                RETURNING id
                """,
                (install, source, seq, app_version, built_at, kind, len(rows) + len(buckets), sha,
                 text.replace("\\u0000", "")),
            )
            if cur.fetchone() is None:
                return 200, {"duplicate": True}
            if seq is not None:
                cur.execute(
                    "SELECT 1 FROM payloads WHERE install = %s AND source IS NOT DISTINCT FROM %s"
                    " AND sequence = %s AND body_sha256 <> %s LIMIT 1",
                    (install, source, seq, sha),
                )
                if cur.fetchone() is not None:
                    log(f"install {install}: sequence {seq} from {source} arrived before with"
                        " another body; applying this one as well")

            # Deletions first: the app never names a record as deleted and sends it in the
            # same payload, and a rewrite in a later payload must survive.
            for item in deletions:
                if not isinstance(item, dict):
                    continue
                dtype, duuid = item.get("type"), item.get("uuid")
                if not isinstance(dtype, str) or not isinstance(duuid, str) or not duuid:
                    continue
                params = {"type": dtype, "uuid": duuid, "seq": seq, "built_at": built_at}
                cur.execute(DELETE_SQL, params)
                deleted += cur.rowcount
                cur.execute(TOMBSTONE_SQL, params)

            if rows:
                cur.executemany(UPSERT_SQL, rows)
            if buckets:
                cur.executemany(BUCKET_SQL, buckets)
    if kind == "ping":
        return 200, {"stored": 0, "ping": True}
    return 200, {"stored": len(rows), "buckets": len(buckets), "deleted": deleted, "skipped": skipped}


# ------------------------------------------------------------------ setup

class SetupError(Exception):
    pass


def wait_for_db(dsn: str, retries: int = 60) -> None:
    for _ in range(retries):
        try:
            with psycopg.connect(dsn, connect_timeout=3):
                return
        except psycopg.OperationalError:
            time.sleep(2)
    raise SetupError("database unreachable, giving up")


def check_timezone(conn, tz: str) -> None:
    found = conn.execute("SELECT 1 FROM pg_timezone_names WHERE name = %s", (tz,)).fetchone()
    if found is None:
        raise SetupError(
            f"TZ={tz!r} is not a time zone name Postgres knows. Set TZ in .env to an IANA name "
            "such as Europe/Amsterdam, America/Los_Angeles or UTC."
        )


def migrate(conn) -> list[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    applied = []
    for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        if path.name in done:
            continue
        with conn.transaction():
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
        applied.append(path.name)
        log(f"applied migration {path.name}")
    return applied


def rebuild_views(conn, tz: str) -> None:
    text = VIEWS_FILE.read_text().replace("__TZ__", sql.Literal(tz).as_string(conn))
    with conn.transaction():
        conn.execute(text)


def setup_roles(conn, ro_password: str) -> None:
    with conn.transaction():
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'grafana_ro'").fetchone()
        if exists is None:
            conn.execute("CREATE ROLE grafana_ro")
        conn.execute(
            sql.SQL(
                "ALTER ROLE grafana_ro WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE"
                " NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(sql.Literal(ro_password))
        )
        conn.execute("ALTER ROLE grafana_ro SET statement_timeout = '30s'")
        conn.execute("ALTER ROLE grafana_ro SET default_transaction_read_only = on")
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        conn.execute("GRANT USAGE ON SCHEMA dash, public TO grafana_ro")
        conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA dash TO grafana_ro")
        conn.execute("GRANT SELECT ON records TO grafana_ro")
        conn.execute("GRANT SELECT ON buckets TO grafana_ro")
        conn.execute("REVOKE ALL ON payloads, schema_migrations, tombstones FROM grafana_ro")


def setup_database(dsn: str, tz: str, ro_password: str) -> None:
    """Migrations, views and roles, under an advisory lock so two receivers never race."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        check_timezone(conn, tz)
        conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK,))
        try:
            migrate(conn)
            rebuild_views(conn, tz)
            setup_roles(conn, ro_password)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK,))


def secret_warning(secret: str) -> str | None:
    if secret:
        return None
    line = "!" * 72
    return (
        f"{line}\n"
        "WARNING: WEBHOOK_SECRET is empty, so this receiver stores unsigned payloads from\n"
        "anyone who can reach it. Set WEBHOOK_SECRET in .env, enter the same secret in the\n"
        "app as the HMAC signing secret, and restart the stack.\n"
        f"{line}"
    )


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "LifeDashboardReceiver"
    dsn = DB_DSN
    secret = SECRET

    def _reply(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path.split("?", 1)[0] == "/health":
            if READY.is_set():
                self._reply(200, {"status": "ok"})
            else:
                self._reply(503, {"status": "starting"})
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        match = LABEL_RE.match(self.path.split("?", 1)[0])
        if match is None:
            self.close_connection = True
            self._reply(404, {"error": "not found"})
            return
        install = match.group(1) or "default"

        # The size comes first, from the header, before a byte of the body is read.
        raw_length = self.headers.get("Content-Length")
        if raw_length is None or not raw_length.strip().isdigit():
            self.close_connection = True
            self._reply(411, {"error": "Content-Length required"})
            return
        length = int(raw_length)
        if length > MAX_BODY:
            self.close_connection = True
            self._reply(413, {"error": f"payload over {MAX_BODY} bytes"})
            return
        body = self.rfile.read(length)

        if self.secret:
            expected = "sha256=" + hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
            provided = self.headers.get("X-Signature", "")
            if not hmac.compare_digest(expected.encode(), provided.encode()):
                self._reply(401, {"error": "invalid signature"})
                return

        if not READY.is_set():
            self._reply(503, {"error": "starting"})
            return

        try:
            code, reply = ingest(self.dsn, install, body)
        except Exception as exc:  # noqa: BLE001
            log(f"store failed for install {install}: {exc}")
            code, reply = 500, {"error": "storage failure"}
        self._reply(code, reply)

    def log_message(self, fmt, *args):
        if self.command == "GET" and self.path == "/health":
            return  # the healthcheck runs every few seconds
        print(f"{self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    # As PID 1 in its container, Python ignores SIGTERM unless it handles it, and docker
    # stop would wait for its timeout and then kill it.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    warning = secret_warning(SECRET)
    if warning:
        log(warning)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"receiver listening on :{PORT} (signature check: {'on' if SECRET else 'OFF'}, TZ {TZ})",
          flush=True)
    try:
        wait_for_db(DB_DSN)
        setup_database(DB_DSN, TZ, GRAFANA_DB_PASSWORD)
    except SetupError as exc:
        log(f"receiver cannot start: {exc}")
        sys.exit(2)
    READY.set()
    print("database ready: migrations applied, dash views rebuilt", flush=True)
    threading.Event().wait()


if __name__ == "__main__":
    main()
