"""A year of 1-minute heart rate plus 60 days of everything: the views stay fast.

Every view is queried the way a dashboard does, over the last 30 days for one install,
and must answer in under 500 ms; heart_rate_hourly over 90 days in under 1 s.
"""

import importlib.util
import json
import time

import psycopg
import pytest

import receiver
from conftest import REPO_DIR

TIMED = {
    "activity_days": "time", "steps_hourly": "time", "steps_raw_days": "time", "heart_rate": "time",
    "heart_rate_hourly": "time", "heart_days": "time", "measurements": "time", "measurement_days": "time",
    "weight_days": "time", "sleep_nights": "time", "sleep_stages": "time", "workouts": "time",
    "screen_days": "time", "screen_apps": "time", "records_days": "time",
}
UNTIMED = ["installs", "type_summary"]


def load_sample_data():
    spec = importlib.util.spec_from_file_location("sample_data", REPO_DIR / "tools" / "sample_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def big_db(server_url, tmp_path_factory):
    import uuid as uuidlib
    from psycopg import sql

    name = f"perf_{uuidlib.uuid4().hex[:8]}"
    with psycopg.connect(server_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    dsn = server_url.rsplit("/", 1)[0] + "/" + name
    receiver.setup_database(dsn, "Europe/Amsterdam", "perf")
    sample = load_sample_data()
    for payload, _ in sample.run_android():
        status, _ = receiver.ingest(dsn, "phone", json.dumps(payload).encode())
        assert status == 200
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """
            INSERT INTO records (type, ts, uuid, source_app, payload_source, data, install, start_ts, seq, built_at)
            SELECT 'heart_rate', t, 'year#' || (extract(epoch FROM t) * 1000)::bigint, 'com.example.watch',
                   'health_connect',
                   jsonb_build_object('bpm', 55 + (random() * 50)::int, 'time', t),
                   'phone', t, 1, now()
            FROM generate_series(now() - interval '365 days', now(), interval '1 minute') AS t
            """
        )
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("VACUUM ANALYZE records")
        conn.execute("VACUUM ANALYZE payloads")
    yield dsn
    with psycopg.connect(server_url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def timed(dsn, statement, runs=3):
    with psycopg.connect(dsn) as conn:
        conn.execute(statement).fetchall()  # warm up
        best = None
        for _ in range(runs):
            started = time.perf_counter()
            rows = conn.execute(statement).fetchall()
            elapsed = time.perf_counter() - started
            best = elapsed if best is None else min(best, elapsed)
    print(f"\nTIMING {best * 1000:7.1f} ms  {statement[:70]}")
    return best, len(rows)


def test_volume(big_db):
    with psycopg.connect(big_db) as conn:
        n = conn.execute("SELECT count(*) FROM records WHERE type = 'heart_rate'").fetchone()[0]
    assert n > 525_000


@pytest.mark.parametrize("view", sorted(TIMED))
def test_view_over_30_days_under_500_ms(big_db, view):
    elapsed, rows = timed(big_db, f"SELECT * FROM dash.{view} WHERE install IN ('phone')"
                                  f" AND time >= now() - interval '30 days' AND time <= now()")
    assert rows > 0, view
    assert elapsed < 0.5, f"dash.{view}: {elapsed * 1000:.0f} ms"


@pytest.mark.parametrize("view", UNTIMED)
def test_untimed_view_under_500_ms(big_db, view):
    elapsed, rows = timed(big_db, f"SELECT * FROM dash.{view}")
    assert rows > 0
    assert elapsed < 0.5, f"dash.{view}: {elapsed * 1000:.0f} ms"


def test_heart_rate_hourly_over_90_days_under_1_s(big_db):
    elapsed, rows = timed(big_db, "SELECT * FROM dash.heart_rate_hourly WHERE install IN ('phone')"
                                  " AND time >= now() - interval '90 days' AND time <= now()")
    assert rows > 2000
    assert elapsed < 1.0, f"{elapsed * 1000:.0f} ms"


def test_install_variable_query_is_fast(big_db):
    elapsed, _ = timed(big_db, "SELECT install FROM dash.installs GROUP BY install"
                               " ORDER BY max(last_received) DESC NULLS LAST")
    assert elapsed < 0.2, f"{elapsed * 1000:.0f} ms"
