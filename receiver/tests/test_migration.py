"""Migration from a database written by the receiver on main (schema 001 only).

Before the day-key fix every sync added a new synthetic-<hash> row for the same day's
totals and screen time. After it, day rows were keyed <type>:<source>:<device>:<date>.
The migration keeps one row per date, the one received last, and loses nothing else.
"""

import json

import psycopg

import receiver
from conftest import RO_PASSWORD, query


def insert(conn, rtype, ts, uuid, payload_source, data, received_at, source_app=None):
    conn.execute(
        "INSERT INTO records (type, ts, uuid, source_app, payload_source, data, received_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (rtype, ts, uuid, source_app, payload_source, json.dumps(data), received_at),
    )


def main_era(dsn):
    with psycopg.connect(dsn) as conn:
        conn.execute((receiver.MIGRATIONS_DIR / "001_initial.sql").read_text())
        # Synthetic day rows from before the fix: three syncs for the 2nd, two for the 3rd.
        for n, steps in enumerate((1000, 4000, 8000)):
            insert(conn, "daily_totals", "2026-10-02T00:00:00Z", f"synthetic-dt2-{n}", "health_connect",
                   {"date": "2026-10-02", "steps": steps}, f"2026-10-02T{10 + n}:00:00Z")
        for n, steps in enumerate((2000, 6000)):
            insert(conn, "daily_totals", "2026-10-03T00:00:00Z", f"synthetic-dt3-{n}", "health_connect",
                   {"date": "2026-10-03", "steps": steps}, f"2026-10-03T{10 + n}:00:00Z")
        # A keyed row for the 3rd, written after the fix, and later than the synthetic ones.
        insert(conn, "daily_totals", "2026-10-03T00:00:00Z", "daily_totals:health_connect::2026-10-03",
               "health_connect", {"date": "2026-10-03", "steps": 9000}, "2026-10-03T20:00:00Z")
        # A keyed row for the 4th only.
        insert(conn, "daily_totals", "2026-10-04T00:00:00Z", "daily_totals:health_connect::2026-10-04",
               "health_connect", {"date": "2026-10-04", "steps": 3000}, "2026-10-04T09:00:00Z")
        # Screen time: synthetic rows (no device) and keyed rows of one device.
        for n, minutes in enumerate((60, 120)):
            insert(conn, "screen_time", "2026-10-02T00:00:00Z", f"synthetic-st2-{n}", "screen_time",
                   {"date": "2026-10-02", "total_screen_time_minutes": minutes, "apps": []},
                   f"2026-10-02T{18 + n}:00:00Z")
        insert(conn, "screen_time", "2026-10-03T00:00:00Z", "synthetic-st3-0", "screen_time",
               {"date": "2026-10-03", "total_screen_time_minutes": 50, "apps": []}, "2026-10-03T12:00:00Z")
        insert(conn, "screen_time", "2026-10-03T00:00:00Z", "screen_time:screen_time:Google Pixel 8:2026-10-03",
               "screen_time", {"date": "2026-10-03", "total_screen_time_minutes": 140, "apps": []},
               "2026-10-03T21:00:00Z")
        # Other records, which must all survive.
        for i in range(5):
            insert(conn, "heart_rate", f"2026-10-03T08:0{i}:00Z", f"rec#{i}", "health_connect",
                   {"bpm": 60 + i, "time": f"2026-10-03T08:0{i}:00Z"}, "2026-10-03T09:00:00Z", "watch")
        insert(conn, "sleep", "2026-10-03T06:00:00Z", "sleep-1", "health_connect",
               {"session_end_time": "2026-10-03T06:00:00Z", "duration_seconds": 28800, "stages": []},
               "2026-10-03T09:00:00Z", "watch")
        insert(conn, "steps", "2026-10-03T09:00:00Z", "steps-1", "health_connect",
               {"count": 500, "start_time": "2026-10-03T08:00:00Z", "end_time": "2026-10-03T09:00:00Z"},
               "2026-10-03T09:00:00Z", "watch")


def test_migration_collapses_day_rows_and_keeps_everything_else(make_db):
    dsn = make_db(setup=False)
    main_era(dsn)
    before = dict(query(dsn, "SELECT type, count(*) FROM records GROUP BY type"))
    assert before == {"daily_totals": 7, "screen_time": 4, "heart_rate": 5, "sleep": 1, "steps": 1}

    receiver.setup_database(dsn, "UTC", RO_PASSWORD)

    after = dict(query(dsn, "SELECT type, count(*) FROM records GROUP BY type"))
    assert after == {"daily_totals": 3, "screen_time": 2, "heart_rate": 5, "sleep": 1, "steps": 1}
    assert query(dsn, "SELECT day::text, steps FROM dash.activity_days ORDER BY day") == [
        ("2026-10-02", 8000), ("2026-10-03", 9000), ("2026-10-04", 3000)]
    assert query(dsn, "SELECT day::text, device, total_min FROM dash.screen_days ORDER BY day") == [
        ("2026-10-02", "Google Pixel 8", 120), ("2026-10-03", "Google Pixel 8", 140)]
    keys = [k for (k,) in query(dsn, "SELECT uuid FROM records WHERE day IS NOT NULL ORDER BY uuid")]
    assert keys == [
        "daily_totals:default:health_connect::2026-10-02",
        "daily_totals:default:health_connect::2026-10-03",
        "daily_totals:default:health_connect::2026-10-04",
        "screen_time:default:screen_time:Google Pixel 8:2026-10-02",
        "screen_time:default:screen_time:Google Pixel 8:2026-10-03",
    ]
    assert query(dsn, "SELECT count(*) FROM records WHERE install <> 'default' OR seq IS NOT NULL"
                      " OR built_at IS NOT NULL OR start_ts IS NULL") == [(0,)]
    assert query(dsn, "SELECT start_ts::text FROM records WHERE type IN ('sleep', 'steps') ORDER BY type") == [
        ("2026-10-02 22:00:00+00",), ("2026-10-03 08:00:00+00",)]
    # The old figures did not move their ts.
    assert query(dsn, "SELECT count(*) FROM records WHERE day IS NOT NULL AND ts <> day::timestamptz") == [(0,)]
    assert query(dsn, "SELECT install, payload_source FROM dash.installs ORDER BY 2") == [
        ("default", "health_connect"), ("default", "screen_time")]


def test_new_payloads_after_migration_replace_the_migrated_day(make_db):
    dsn = make_db(setup=False)
    main_era(dsn)
    receiver.setup_database(dsn, "UTC", RO_PASSWORD)
    status, _ = receiver.ingest(dsn, "default", json.dumps({
        "timestamp": "2026-10-04T12:00:00Z", "app_version": "1.23.0", "source": "health_connect",
        "sequence": 1, "daily_totals": [{"date": "2026-10-04", "steps": 5000}]}).encode())
    assert status == 200
    assert query(dsn, "SELECT steps FROM dash.activity_days WHERE day = '2026-10-04'") == [(5000,)]
    assert query(dsn, "SELECT count(*) FROM records WHERE type = 'daily_totals'") == [(3,)]


def test_setup_twice_is_a_no_op(make_db):
    dsn = make_db("UTC")
    receiver.setup_database(dsn, "UTC", RO_PASSWORD)
    versions = [v for (v,) in query(dsn, "SELECT version FROM schema_migrations ORDER BY 1")]
    assert versions == sorted(p.name for p in receiver.MIGRATIONS_DIR.glob("*.sql"))
