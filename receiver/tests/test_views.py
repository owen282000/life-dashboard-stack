"""The dash views: sleep rules, day placement in two time zones, measurements, workouts."""

import pytest

from conftest import envelope, post, query

ZONES = ["Europe/Amsterdam", "America/Los_Angeles"]


def stage(name, start, end, secs, source=None, uuid=None):
    s = {"stage": name, "start_time": start, "end_time": end, "duration_seconds": secs}
    if source:
        s["source"] = source
    if uuid:
        s["uuid"] = uuid
    return s


# ------------------------------------------------------------------ sleep

def ios_night():
    """An iPhone night as the sample generator builds it: Watch stages plus an iPhone in_bed."""
    stages = [
        stage("in_bed", "2026-10-03T21:00:00Z", "2026-10-04T05:30:00Z", 30600, "iPhone", "A"),
        stage("light", "2026-10-03T21:10:00Z", "2026-10-03T23:10:00Z", 7200, "Apple Watch", "B"),
        stage("deep", "2026-10-03T23:10:00Z", "2026-10-04T01:10:00Z", 7200, "Apple Watch", "C"),
        stage("awake", "2026-10-04T01:10:00Z", "2026-10-04T01:40:00Z", 1800, "Apple Watch", "D"),
        stage("rem", "2026-10-04T01:40:00Z", "2026-10-04T03:10:00Z", 5400, "Apple Watch", "E"),
        stage("sleeping", "2026-10-04T03:10:00Z", "2026-10-04T05:10:00Z", 7200, "Apple Watch", "F"),
        # An iPhone-recorded stage inside the night that must not count.
        stage("light", "2026-10-04T05:10:00Z", "2026-10-04T05:20:00Z", 600, "iPhone", "G"),
    ]
    return {"session_end_time": "2026-10-04T05:10:00Z", "duration_seconds": 28800,
            "stages": stages, "uuid": "B", "source": "Apple Watch"}


def test_ios_night_counts_watch_stages_only(db):
    post(db, envelope(None, source="healthkit_ios", sleep=[ios_night()]))
    row = query(db, "SELECT asleep_h, deep_h, light_h, rem_h, awake_h, in_bed_h, has_stages"
                    " FROM dash.sleep_nights")[0]
    assert row == (pytest.approx(7.5), pytest.approx(2.0), pytest.approx(2.0), pytest.approx(1.5),
                   pytest.approx(0.5), pytest.approx(8.0), True)
    stages = query(db, "SELECT stage, minutes FROM dash.sleep_stages ORDER BY time")
    assert [s for s, _ in stages] == ["light", "deep", "awake", "rem", "asleep"]


def test_night_without_stages_uses_duration(db):
    post(db, envelope(1, sleep=[{"session_end_time": "2026-10-04T06:00:00Z", "duration_seconds": 25200,
                                 "stages": [], "uuid": "s", "source": "x"}]))
    assert query(db, "SELECT asleep_h, deep_h, has_stages FROM dash.sleep_nights") == [(7.0, None, False)]


def test_two_sources_on_one_night_give_one_main_session(db):
    post(db, envelope(1, sleep=[
        {"session_end_time": "2026-10-04T06:00:00Z", "duration_seconds": 28800, "uuid": "a", "source": "watch",
         "stages": [stage("light", "2026-10-03T22:00:00Z", "2026-10-04T05:00:00Z", 25200)]},
        {"session_end_time": "2026-10-04T06:05:00Z", "duration_seconds": 24000, "uuid": "b", "source": "phone",
         "stages": []},
    ]))
    rows = query(db, "SELECT source_app, asleep_h FROM dash.sleep_nights")
    assert rows == [("watch", 7.0)]


def test_hr_low_and_avg_inside_the_main_session(db):
    hr = [{"bpm": 50 if 2 <= h <= 3 else 60, "time": f"2026-10-04T{h:02d}:{m:02d}:00Z",
           "uuid": f"r#{h}{m}", "source": "w"} for h in range(0, 9) for m in (0, 5)]
    post(db, envelope(1, heart_rate=hr, sleep=[
        {"session_end_time": "2026-10-04T06:00:00Z", "duration_seconds": 21600, "uuid": "s", "source": "w",
         "stages": []}]))
    low, avg = query(db, "SELECT hr_low, hr_avg FROM dash.sleep_nights")[0]
    assert low == 50.0
    assert avg == pytest.approx((4 * 50 + 8 * 60) / 12)  # samples 00:00 to 05:55


@pytest.mark.parametrize("tz", ZONES)
def test_wake_date_and_clock_minutes_in_both_zones(make_db, tz):
    db = make_db(tz)
    # 23:30 to 07:00 local, written in UTC for the zone in question.
    if tz == "Europe/Amsterdam":
        end, local_day = "2026-10-04T05:00:00Z", "2026-10-04"
    else:
        end, local_day = "2026-10-04T14:00:00Z", "2026-10-04"
    post(db, envelope(1, sleep=[{"session_end_time": end, "duration_seconds": 27000, "uuid": "s",
                                 "source": "w", "stages": []}]))
    rows = query(db, "SELECT day::text, bed_min, wake_min, time = dash.day_start(day) FROM dash.sleep_nights")
    assert rows == [(local_day, 330, 780, True)]


# ------------------------------------------------------------------ day placement

@pytest.mark.parametrize("tz", ZONES)
def test_day_record_lands_on_its_own_date(make_db, tz):
    db = make_db(tz)
    post(db, envelope(1, daily_totals=[{"date": "2026-10-04", "steps": 5000}]))
    post(db, {"timestamp": "2026-10-04T20:00:00Z", "app_version": "1.23.0", "device": "P",
              "source": "screen_time", "sequence": 2,
              "screen_time": [{"date": "2026-10-04", "total_screen_time_minutes": 90, "apps": []}]})
    assert query(db, "SELECT day::text FROM dash.activity_days") == [("2026-10-04",)]
    assert query(db, "SELECT day::text FROM dash.screen_days") == [("2026-10-04",)]
    assert query(db, "SELECT day::text, type FROM dash.records_days ORDER BY type") == [
        ("2026-10-04", "daily_totals"), ("2026-10-04", "screen_time")]
    midnight = query(db, "SELECT time, dash.day_start('2026-10-04') FROM dash.activity_days")[0]
    assert midnight[0] == midnight[1]


@pytest.mark.parametrize("tz,utc,local_day", [
    ("Europe/Amsterdam", "2026-10-04T21:30:00Z", "2026-10-04"),     # 23:30 CEST
    ("America/Los_Angeles", "2026-10-05T06:30:00Z", "2026-10-04"),  # 23:30 PDT
])
def test_heart_rate_at_2330_local_is_on_the_local_day(make_db, tz, utc, local_day):
    db = make_db(tz)
    post(db, envelope(1, heart_rate=[{"bpm": 70, "time": utc, "uuid": "r#1", "source": "w"}]))
    assert query(db, "SELECT day::text FROM dash.heart_days") == [(local_day,)]
    assert query(db, "SELECT extract(hour FROM time AT TIME ZONE dash.tz())::int"
                     " FROM dash.heart_rate_hourly") == [(23,)]
    assert query(db, "SELECT day::text FROM dash.records_days") == [(local_day,)]


def test_days_helper_is_a_dense_local_calendar(make_db):
    db = make_db("Europe/Amsterdam")
    rows = query(db, "SELECT day::text, time FROM dash.days('2026-10-24T12:00:00Z', '2026-10-27T12:00:00Z')")
    assert [d for d, _ in rows] == ["2026-10-24", "2026-10-25", "2026-10-26", "2026-10-27"]
    # The clocks go back on 25 October: that day is 25 hours long.
    assert (rows[2][1] - rows[1][1]).total_seconds() == 25 * 3600


def test_tz_and_today_helpers(make_db):
    db = make_db("America/Los_Angeles")
    assert query(db, "SELECT dash.tz(), dash.today() = (now() AT TIME ZONE 'America/Los_Angeles')::date") == [
        ("America/Los_Angeles", True)]


# ------------------------------------------------------------------ measurements and workouts

def test_hrv_keeps_rmssd_and_sdnn_apart(db):
    post(db, envelope(1, heart_rate_variability=[
        {"heart_rate_variability_millis": 40.0, "time": "2026-10-04T03:00:00Z", "uuid": "a"}]), install="phone")
    post(db, envelope(None, source="healthkit_ios", heart_rate_variability=[
        {"heart_rate_variability_millis": 55.0, "time": "2026-10-04T03:00:00Z", "uuid": "B"}]), install="phone")
    rows = query(db, "SELECT measure, unit, avg FROM dash.measurement_days ORDER BY measure")
    assert rows == [("rmssd", "ms", 40.0), ("sdnn", "ms", 55.0)]


def test_blood_pressure_gives_two_rows_and_skin_temperature_is_android_only(db):
    post(db, envelope(1, blood_pressure=[{"systolic": 120.0, "diastolic": 80.0,
                                          "time": "2026-10-04T07:00:00Z", "uuid": "bp"}],
                      skin_temperature=[{"delta_celsius": -0.3, "time": "2026-10-04T03:00:00Z", "uuid": "st#1"}]))
    post(db, envelope(None, source="healthkit_ios", skin_temperature=[
        {"delta_celsius": 34.1, "time": "2026-10-04T03:00:00Z", "uuid": "ST"}]))
    rows = query(db, "SELECT type, measure, value, unit FROM dash.measurements ORDER BY type, measure")
    assert rows == [("blood_pressure", "diastolic", 80.0, "mmHg"), ("blood_pressure", "systolic", 120.0, "mmHg"),
                    ("skin_temperature", "delta", -0.3, "°C")]


def test_weight_days_counts_a_weighing_from_two_apps_once(db):
    post(db, envelope(1, weight=[
        {"kilograms": 80.04, "time": "2026-10-04T07:00:00Z", "uuid": "a", "source": "scale"},
        {"kilograms": 80.0, "time": "2026-10-04T07:00:00Z", "uuid": "b", "source": "fit"},
        {"kilograms": 81.0, "time": "2026-10-04T20:00:00Z", "uuid": "c", "source": "scale"},
        {"kilograms": 79.0, "time": "2026-10-01T07:00:00Z", "uuid": "d", "source": "scale"}]))
    rows = query(db, "SELECT day::text, kg, readings, round(kg_7d::numeric, 2)::float FROM dash.weight_days ORDER BY day")
    assert rows[0] == ("2026-10-01", 79.0, 1, 79.0)
    assert rows[1][0:3] == ("2026-10-04", pytest.approx(80.0, abs=0.05), 2)
    assert rows[1][3] == pytest.approx((79.0 + 80.0 + 81.0) / 3, abs=0.02)


@pytest.mark.parametrize("code,activity", [
    ("56", "running"), ("79", "walking"), ("8", "biking"), ("74", "swimming_pool"),
    ("36", "high_intensity_interval_training"), ("0", "other"), ("999", "other"),
])
def test_health_connect_codes_map_to_names(db, code, activity):
    post(db, envelope(1, exercise=[{"type": code, "start_time": "2026-10-04T07:00:00Z",
                                    "end_time": "2026-10-04T07:45:00Z", "duration_seconds": 2700,
                                    "uuid": "e", "source": "w"}]))
    assert query(db, "SELECT activity, type_code, minutes FROM dash.workouts") == [(activity, code, 45.0)]


def test_ios_workout_names_are_kept(db):
    post(db, envelope(None, source="healthkit_ios", exercise=[
        {"type": "downhill_skiing", "start_time": "2026-10-04T07:00:00Z", "end_time": "2026-10-04T08:00:00Z",
         "duration_seconds": 3600, "uuid": "E", "source": "Apple Watch"}]))
    assert query(db, "SELECT activity, minutes FROM dash.workouts") == [("downhill_skiing", 60.0)]


def test_steps_hourly_takes_the_max_across_sources(db):
    post(db, envelope(1, steps=[
        {"count": 1000, "start_time": "2026-10-04T08:00:00Z", "end_time": "2026-10-04T09:00:00Z", "uuid": "a", "source": "watch"},
        {"count": 900, "start_time": "2026-10-04T08:00:00Z", "end_time": "2026-10-04T09:00:00Z", "uuid": "b", "source": "phone"}]))
    assert query(db, "SELECT steps, sources FROM dash.steps_hourly") == [(1000, 2)]
    assert query(db, "SELECT source_app, steps FROM dash.steps_raw_days ORDER BY 1") == [("phone", 900), ("watch", 1000)]


def test_installs_lists_ledger_and_pre_ledger_rows(db):
    query(db, "INSERT INTO records (type, ts, uuid, payload_source, data) VALUES"
              " ('weight', now(), 'old', 'health_connect', '{}') RETURNING 1")
    post(db, {"timestamp": "2026-10-04T20:00:00Z", "app_version": "1.23.0", "device": "Pixel 8",
              "source": "screen_time", "sequence": 7,
              "screen_time": [{"date": "2026-10-04", "total_screen_time_minutes": 90, "apps": []}]}, install="p2")
    rows = query(db, "SELECT install, payload_source, platform, device, last_sequence, app_version"
                     " FROM dash.installs ORDER BY install")
    assert rows == [("default", "health_connect", "Android", None, None, None),
                    ("p2", "screen_time", "Screen time", "Pixel 8", 7, "1.23.0")]


def test_one_workout_recorded_by_two_sources_counts_once(db):
    def ex(uuid, source, start, end, secs):
        return {"type": "56", "start_time": start, "end_time": end, "duration_seconds": secs,
                "uuid": uuid, "source": source}
    post(db, envelope(1, exercise=[
        ex("w", "watch", "2026-10-04T07:00:00Z", "2026-10-04T07:50:00Z", 3000),
        ex("p", "phone", "2026-10-04T07:02:00Z", "2026-10-04T07:45:00Z", 2580),
        # A second, separate workout later that day from the phone stays.
        ex("p2", "phone", "2026-10-04T18:00:00Z", "2026-10-04T18:30:00Z", 1800),
    ]))
    rows = query(db, "SELECT source_app, minutes FROM dash.workouts ORDER BY time")
    assert rows == [("watch", 50.0), ("phone", 30.0)]


def test_overlapping_workouts_of_one_source_both_stay(db):
    post(db, envelope(1, exercise=[
        {"type": "56", "start_time": "2026-10-04T07:00:00Z", "end_time": "2026-10-04T07:50:00Z",
         "duration_seconds": 3000, "uuid": "a", "source": "watch"},
        {"type": "79", "start_time": "2026-10-04T07:10:00Z", "end_time": "2026-10-04T07:20:00Z",
         "duration_seconds": 600, "uuid": "b", "source": "watch"}]))
    assert query(db, "SELECT count(*) FROM dash.workouts") == [(2,)]
