"""The read-only API under /api/: off by default, bearer token, ranges, and dash_ro only."""

import json
import threading
import urllib.error
import urllib.request
from datetime import date, timedelta
from http.server import ThreadingHTTPServer

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

import api
import queries
import receiver
from conftest import envelope, post

TOKEN = "test-api-token-0123456789abcdef"


def get(base, path, token=TOKEN, scheme="Bearer", method="GET"):
    req = urllib.request.Request(base + path, method=method)
    if token is not None:
        req.add_header("Authorization", f"{scheme} {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), dict(e.headers)


@pytest.fixture
def make_api():
    servers = []

    def start(dsn, token=TOKEN):
        handler = type("ApiHandler", (receiver.Handler,),
                       {"dsn": dsn, "secret": "", "api_token": token, "api_dsn": api.dsn(dsn)})
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        receiver.READY.set()
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    yield start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def seed(dsn, today=None):
    """Two days for "phone" and one for "other", in every view the days endpoint reads."""
    post(dsn, envelope(1, daily_totals=[
        {"date": "2026-10-03", "steps": 9000, "distance_meters": 6500.0, "active_calories": 400.0,
         "total_calories": 2200.0},
        {"date": "2026-10-04", "steps": 4000, "distance_meters": 3000.0},
    ], sleep=[{"session_end_time": "2026-10-04T05:30:00Z", "duration_seconds": 27000, "uuid": "n1",
               "source": "watch", "stages": []}],
        exercise=[{"type": 56, "start_time": "2026-10-03T16:00:00Z", "end_time": "2026-10-03T16:45:00Z",
                   "duration_seconds": 2700, "uuid": "x1", "source": "watch"}],
        weight=[{"kilograms": 80.0, "time": "2026-10-04T06:00:00Z", "uuid": "w1"}],
        resting_heart_rate=[{"bpm": 52, "time": "2026-10-04T06:00:00Z", "uuid": "r1"}]))
    post(dsn, {"timestamp": "2026-10-04T20:00:00Z", "source": "screen_time", "sequence": 2,
               "device": "Google Pixel 9", "screen_time": [
                   {"date": "2026-10-04", "total_screen_time_minutes": 150, "apps": [
                       {"package": "com.whatsapp", "name": "WhatsApp", "minutes": 60,
                        "last_used": "2026-10-04T19:00:00Z"},
                       {"package": "org.mozilla.firefox", "name": "Firefox", "minutes": 90,
                        "last_used": "2026-10-04T21:30:00Z"}]}]})
    post(dsn, envelope(1, daily_totals=[{"date": "2026-10-04", "steps": 100}]), install="other")


@pytest.fixture
def base(make_db, make_api):
    dsn = make_db("Europe/Amsterdam")
    seed(dsn)
    return make_api(dsn)


# ------------------------------------------------------------------ on, off and auth

def test_off_without_a_token(db, make_api):
    base = make_api(db, token="")
    assert get(base, "/api/phones")[0] == 404
    assert get(base, "/api/phones", token=None)[0] == 404


def test_short_token_keeps_the_api_off(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "short")
    token, warning = api._token_from_env()
    assert token == "" and "shorter than 16" in warning
    monkeypatch.setenv("API_TOKEN", TOKEN)
    assert api._token_from_env() == (TOKEN, None)


def test_bearer_token_is_required(base):
    status, body, headers = get(base, "/api/phones", token=None)
    assert status == 401 and headers["WWW-Authenticate"].startswith("Bearer")
    assert get(base, "/api/phones", token="wrong")[0] == 401
    assert get(base, "/api/phones", token=TOKEN + "x")[0] == 401
    assert get(base, "/api/phones", token=TOKEN, scheme="Basic")[0] == 401
    # A bad request without the token says nothing about the parameters.
    assert get(base, "/api/days?from=nonsense", token=None)[0] == 401
    status, body, headers = get(base, "/api/phones", scheme="bearer")
    assert status == 200 and headers["Cache-Control"] == "no-store"


def test_unknown_paths_and_methods(base):
    assert get(base, "/api/nothing")[0] == 404
    assert get(base, "/api")[0] == 404
    assert get(base, "/api/phones", method="POST")[0] == 404
    assert get(base, "/health", token=None)[0] == 200


def test_starting_receiver_answers_503(base):
    receiver.READY.clear()
    try:
        assert get(base, "/api/phones")[0] == 503
    finally:
        receiver.READY.set()


# ------------------------------------------------------------------ endpoints

def test_phones(base):
    status, body, _ = get(base, "/api/phones")
    assert status == 200 and body["timezone"] == "Europe/Amsterdam"
    phones = {p["phone"]: p for p in body["phones"]}
    assert set(phones) == {"phone", "other"}
    assert phones["phone"]["platforms"] == ["Android", "Screen time"]
    assert phones["phone"]["device"] == "Google Pixel 9"
    assert phones["phone"]["first_day"] == "2026-10-03" and phones["phone"]["last_day"] == "2026-10-04"


def test_days_joins_every_view(base):
    status, body, _ = get(base, "/api/days?from=2026-10-01&to=2026-10-05&phone=phone")
    assert status == 200 and body["truncated"] is False
    rows = {r["day"]: r for r in body["rows"]}
    assert set(rows) == {"2026-10-03", "2026-10-04"}
    d3, d4 = rows["2026-10-03"], rows["2026-10-04"]
    assert (d3["steps"], d3["distance_km"], d3["exercise_min"], d3["workouts"]) == (9000, 6.5, 45.0, 1)
    assert d3["sleep_h"] is None and d3["screen_min"] is None
    assert (d4["steps"], d4["sleep_h"], d4["screen_min"], d4["weight_kg"], d4["resting_hr"]) == \
        (4000, 7.5, 150, 80.0, 52.0)
    assert d4["active_kcal"] is None and d4["complete"] is True


def test_days_for_every_phone(base):
    status, body, _ = get(base, "/api/days?from=2026-10-04&to=2026-10-04")
    assert [(r["phone"], r["steps"]) for r in body["rows"]] == [("other", 100), ("phone", 4000)]


def test_sleep_workouts_in_local_time(base):
    _, body, _ = get(base, "/api/sleep?from=2026-10-04&to=2026-10-04")
    night = body["rows"][0]
    assert night["day"] == "2026-10-04" and night["asleep_h"] == 7.5
    assert night["bedtime"] == "2026-10-04T00:00:00+02:00"
    assert night["wake_time"] == "2026-10-04T07:30:00+02:00"
    _, body, _ = get(base, "/api/workouts?from=2026-10-03&to=2026-10-03&phone=phone")
    assert body["rows"] == [{"day": "2026-10-03", "phone": "phone", "start_time": "2026-10-03T18:00:00+02:00",
                             "end_time": "2026-10-03T18:45:00+02:00", "activity": "running",
                             "minutes": 45.0, "source_app": "watch"}]


def test_screen_time(base):
    status, body, _ = get(base, "/api/screen-time?date=2026-10-04")
    assert status == 200 and body["date"] == "2026-10-04"
    (entry,) = body["phones"]
    assert (entry["phone"], entry["total_min"], entry["apps"]) == ("phone", 150, 2)
    assert [a["app"] for a in entry["top_apps"]] == ["Firefox", "WhatsApp"]
    assert entry["last_use"] == "2026-10-04T23:30:00+02:00"
    assert get(base, "/api/screen-time?date=2026-10-05")[1]["phones"] == []


def test_series(base):
    status, body, _ = get(base, "/api/series?metric=steps&from=2026-10-01&to=2026-10-31&phone=phone")
    assert status == 200 and body["unit"] == "steps"
    assert body["rows"] == [{"day": "2026-10-03", "phone": "phone", "value": 9000.0},
                            {"day": "2026-10-04", "phone": "phone", "value": 4000.0}]
    _, body, _ = get(base, "/api/series?metric=weight&from=2026-10-01&to=2026-10-31")
    assert [r["value"] for r in body["rows"]] == [80.0]


def test_trend_compares_two_periods(make_db, make_api):
    dsn = make_db("UTC")
    today = date.today()
    totals = [{"date": (today - timedelta(days=n)).isoformat(), "steps": 1000 if n > 7 else 2000}
              for n in range(0, 15)]
    post(dsn, envelope(1, daily_totals=totals))
    base = make_api(dsn)
    status, body, _ = get(base, "/api/trend?metric=steps&days=7&phone=phone")
    assert status == 200
    (t,) = body["phones"]
    # Today is a running total and stays out; the 7 days before it against the 7 before those.
    assert t["period"]["to"] == (today - timedelta(days=1)).isoformat()
    assert t["period"]["mean"] == 2000.0 and t["previous"]["mean"] == 1000.0
    assert (t["change"], t["change_pct"]) == (1000.0, 100.0)


# ------------------------------------------------------------------ input checks

@pytest.mark.parametrize("path, status", [
    ("/api/days?from=2026-13-01", 400),
    ("/api/days?from=yesterday", 400),
    ("/api/days?from=2026-10-05&to=2026-10-01", 400),
    ("/api/days?from=2025-01-01&to=2026-10-04", 400),
    ("/api/days?from=2025-09-01&to=2026-10-04", 200),
    ("/api/days?start=2026-10-01", 400),
    ("/api/days?phone=phone&phone=other", 400),
    ("/api/days?phone=Bad%20Label", 400),
    ("/api/days?phone=nobody", 404),
    ("/api/sleep?phone=nobody", 404),
    ("/api/series?metric=cholesterol", 400),
    ("/api/series", 400),
    ("/api/trend?metric=steps&days=3", 400),
    ("/api/trend?metric=steps&days=400", 400),
    ("/api/trend?metric=steps&days=x", 400),
    ("/api/screen-time?date=2026-10", 400),
    ("/api/phones?x=1", 400),
])
def test_bad_input(base, path, status):
    got, body, _ = get(base, path)
    assert got == status, body
    if status != 200:
        assert isinstance(body["error"], str) and body["error"]


def test_range_limit_is_400_days(base):
    assert get(base, "/api/days?from=2025-09-04&to=2026-10-08")[0] == 200
    assert get(base, "/api/days?from=2025-09-03&to=2026-10-08")[0] == 400


def test_default_range_is_the_last_30_days(base):
    _, body, _ = get(base, "/api/days")
    assert (date.fromisoformat(body["to"]) - date.fromisoformat(body["from"])).days == 29


def test_row_cap_marks_truncated(base, monkeypatch):
    monkeypatch.setattr(api, "ROW_CAP", 2)
    _, body, _ = get(base, "/api/days?from=2026-10-01&to=2026-10-05")
    assert len(body["rows"]) == 2 and body["truncated"] is True


def test_database_down_is_503():
    status, body, _ = api.handle("/api/phones", f"Bearer {TOKEN}", TOKEN,
                                 "postgresql://dash_ro:x@127.0.0.1:1/none?connect_timeout=1")
    assert status == 503 and body == {"error": "database unavailable"}


# ------------------------------------------------------------------ read-only

def test_api_connects_as_dash_ro_not_the_owner(db):
    assert conninfo_to_dict(receiver.Handler.api_dsn)["user"] == "dash_ro"
    assert conninfo_to_dict(api.dsn(db))["user"] == "dash_ro"
    with queries.open_reader(api.dsn(db)) as reader:
        row = reader.conn.execute("SELECT current_user AS u, current_setting('transaction_read_only') AS ro,"
                                  " current_setting('statement_timeout') AS t").fetchone()
    assert row == {"u": "dash_ro", "ro": "on", "t": "15s"}


def test_dash_ro_role_attributes(db):
    with psycopg.connect(db) as conn:
        row = conn.execute("SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin, rolconfig"
                           " FROM pg_roles WHERE rolname = 'dash_ro'").fetchone()
    assert row[:4] == (False, False, False, True)
    assert "statement_timeout=15s" in row[4] and "default_transaction_read_only=on" in row[4]


def test_writes_through_the_api_connection_fail(db):
    seed(db)
    with queries.open_reader(api.dsn(db)) as reader:
        conn = reader.conn
        assert conn.execute("SELECT count(*) AS n FROM dash.activity_days").fetchone()["n"] == 3
        # Read-only is the first wall; the grants must hold without it.
        for statement in ["INSERT INTO records (type, ts, data) VALUES ('x', now(), '{}')",
                          "DELETE FROM records", "UPDATE buckets SET avg = 0", "DROP VIEW dash.workouts",
                          "CREATE TABLE public.mine (id int)", "CREATE VIEW dash.mine AS SELECT 1"]:
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                conn.execute(statement)
        conn.execute("SET default_transaction_read_only = off")
        for statement in ["INSERT INTO records (type, ts, data) VALUES ('x', now(), '{}')",
                          "DELETE FROM records", "UPDATE records SET type = 'x'",
                          "CREATE TABLE public.mine (id int)", "CREATE VIEW dash.mine AS SELECT 1",
                          "DROP VIEW dash.workouts", "SET ROLE lifedash", "SET ROLE grafana_ro"]:
            with pytest.raises(psycopg.Error):
                conn.execute(statement)
        # Only the views: not the raw tables, not the ledger.
        for table in ["records", "buckets", "payloads", "tombstones", "schema_migrations"]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(f"SELECT * FROM {table} LIMIT 1")
    with psycopg.connect(db) as owner:
        assert owner.execute("SELECT count(*) FROM records").fetchone()[0] > 0


def test_grants_survive_a_view_rebuild(db):
    receiver.setup_database(db, "UTC", "test-ro-password")
    with queries.open_reader(api.dsn(db)) as reader:
        assert reader.phones() == []
