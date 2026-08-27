"""Webhook receiver for Life Dashboard Companion payloads.

Verifies the optional HMAC signature, upserts every record into Postgres keyed on the
record uuid (so re-sent edits update in place), and stays dependency-light on purpose:
stdlib http.server plus psycopg. See the repository README for the full quickstart.
"""

import hashlib
import hmac
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg

DB_DSN = os.environ.get("DATABASE_URL", "postgresql://lifedash:lifedash@db:5432/lifedash")
SECRET = os.environ.get("WEBHOOK_SECRET", "")
PORT = int(os.environ.get("PORT", "8080"))

# Payload keys that hold record arrays; everything else in the envelope is metadata.
SKIP_KEYS = {"timestamp", "app_version", "source", "_diagnostics", "device"}
TIME_FIELDS = ("time", "end_time", "session_end_time", "date")


def wait_for_db(retries: int = 30) -> None:
    for attempt in range(retries):
        try:
            with psycopg.connect(DB_DSN, connect_timeout=3):
                return
        except Exception:
            time.sleep(2)
    print("database unreachable, giving up", file=sys.stderr)
    sys.exit(1)


def record_time(record: dict) -> str | None:
    for field in TIME_FIELDS:
        if isinstance(record.get(field), str):
            return record[field]
    return None


def synthetic_uuid(rtype: str, record: dict) -> str:
    digest = hashlib.sha256(
        (rtype + json.dumps(record, sort_keys=True)).encode()
    ).hexdigest()
    return f"synthetic-{digest[:32]}"


def store(payload: dict) -> int:
    payload_source = payload.get("source", "unknown")
    inserted = 0
    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        for key, value in payload.items():
            if key in SKIP_KEYS or not isinstance(value, list):
                continue
            for record in value:
                if not isinstance(record, dict):
                    continue
                ts = record_time(record)
                if ts is None:
                    continue
                if len(ts) == 10:  # bare date (screen_time)
                    ts += "T00:00:00Z"
                cur.execute(
                    """
                    INSERT INTO records (type, ts, uuid, source_app, payload_source, data)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (uuid) DO UPDATE
                        SET ts = EXCLUDED.ts, data = EXCLUDED.data, received_at = now()
                    """,
                    (
                        key,
                        ts,
                        record.get("uuid") or synthetic_uuid(key, record),
                        record.get("source"),
                        payload_source,
                        json.dumps(record),
                    ),
                )
                inserted += 1
    return inserted


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._reply(200, {"status": "ok"})
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path != "/webhook":
            self._reply(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)

        if SECRET:
            expected = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
            provided = self.headers.get("X-Signature", "")
            if not hmac.compare_digest(expected, provided):
                self._reply(401, {"error": "invalid signature"})
                return

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._reply(400, {"error": "invalid json"})
            return

        try:
            count = store(payload)
        except Exception as exc:  # noqa: BLE001
            print(f"store failed: {exc}", file=sys.stderr)
            self._reply(500, {"error": "storage failure"})
            return

        self._reply(200, {"stored": count})

    def log_message(self, fmt, *args):  # quieter default logging
        print(f"{self.address_string()} {fmt % args}")


if __name__ == "__main__":
    wait_for_db()
    print(f"receiver listening on :{PORT} (signature check: {'on' if SECRET else 'off'})")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
