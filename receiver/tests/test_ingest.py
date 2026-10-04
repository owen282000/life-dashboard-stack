"""Ingest semantics: duplicates, the ordering rule, day records and deletions."""

import json
import time

import receiver
from conftest import envelope, post, query


def weight(uuid, kg, at="2026-10-04T07:00:00Z"):
    return {"kilograms": kg, "time": at, "uuid": uuid, "source": "scale"}


def stored_kg(db, uuid):
    rows = query(db, "SELECT (data->>'kilograms')::float FROM records WHERE uuid = %s", (uuid,))
    return rows[0][0] if rows else None


# ------------------------------------------------------------------ duplicates

def test_same_body_twice_is_applied_once(db):
    body = json.dumps(envelope(5, weight=[weight("w1", 80.0)])).encode()
    assert receiver.ingest(db, "phone", body) == (200, {"stored": 1, "buckets": 0, "deleted": 0, "skipped": 0})
    # Change the row by hand: a second apply of the same body would put 80.0 back.
    query(db, "UPDATE records SET data = jsonb_set(data, '{kilograms}', '1') WHERE uuid = 'w1' RETURNING 1")
    assert receiver.ingest(db, "phone", body) == (200, {"duplicate": True})
    assert stored_kg(db, "w1") == 1.0
    assert query(db, "SELECT count(*) FROM payloads") == [(1,)]


def test_same_sequence_other_body_is_applied_and_logged(db, capsys):
    post(db, envelope(5, weight=[weight("w1", 80.0)]))
    post(db, envelope(5, weight=[weight("w1", 81.0)]))
    assert stored_kg(db, "w1") == 81.0
    assert "sequence 5" in capsys.readouterr().err


# ------------------------------------------------------------------ ordering

def test_late_lower_payload_is_rejected(db):
    post(db, envelope(10, ts="2026-10-04T08:00:00Z", weight=[weight("w1", 80.0)]))
    post(db, envelope(9, ts="2026-10-04T07:00:00Z", weight=[weight("w1", 79.0)]))
    assert stored_kg(db, "w1") == 80.0


def test_reinstall_with_newer_build_time_is_applied(db):
    post(db, envelope(500, ts="2026-10-04T08:00:00Z", weight=[weight("w1", 80.0)]))
    post(db, envelope(1, ts="2026-10-05T08:00:00Z", weight=[weight("w1", 79.0)]))
    assert stored_kg(db, "w1") == 79.0


def test_clock_moved_back_with_higher_sequence_is_applied(db):
    post(db, envelope(10, ts="2026-10-04T08:00:00Z", weight=[weight("w1", 80.0)]))
    post(db, envelope(11, ts="2026-10-03T08:00:00Z", weight=[weight("w1", 79.0)]))
    assert stored_kg(db, "w1") == 79.0


def test_ios_without_sequence_follows_build_time(db):
    ios = dict(source="healthkit_ios")
    post(db, envelope(None, ts="2026-10-04T08:00:00Z", weight=[weight("W1", 80.0)], **ios))
    post(db, envelope(None, ts="2026-10-04T07:00:00Z", weight=[weight("W1", 79.0)], **ios))
    assert stored_kg(db, "W1") == 80.0
    post(db, envelope(None, ts="2026-10-04T09:00:00Z", weight=[weight("W1", 78.0)], **ios))
    assert stored_kg(db, "W1") == 78.0


def test_migrated_row_without_sequence_or_build_time_is_replaced(db):
    query(db, "INSERT INTO records (type, ts, uuid, source_app, payload_source, data)"
              " VALUES ('weight', '2026-10-04T07:00:00Z', 'w1', 'scale', 'health_connect',"
              " '{\"kilograms\": 90.0}') RETURNING 1")
    post(db, envelope(1, ts="2020-01-01T00:00:00Z", weight=[weight("w1", 80.0)]))
    assert stored_kg(db, "w1") == 80.0


# ------------------------------------------------------------------ day records

def totals(day, steps):
    return {"date": day, "steps": steps, "distance_meters": steps * 0.7}


def test_todays_total_is_replaced_by_every_newer_sync(db):
    for seq, steps in ((1, 1000), (2, 2500), (3, 4200)):
        post(db, envelope(seq, daily_totals=[totals("2026-10-04", steps)]))
    assert query(db, "SELECT day::text, steps FROM dash.activity_days") == [("2026-10-04", 4200)]
    assert query(db, "SELECT count(*) FROM records WHERE type = 'daily_totals'") == [(1,)]


def screen(seq, ts, days, device="Pixel 8"):
    return {"timestamp": ts, "app_version": "1.23.0", "device": device, "source": "screen_time",
            "sequence": seq, "screen_time": [
                {"date": d, "total_screen_time_minutes": m, "apps": [
                    {"package": "com.whatsapp", "name": "WhatsApp", "minutes": m,
                     "last_used": f"{d}T20:00:00Z"}]} for d, m in days]}


def test_screen_time_late_week_still_fills_its_oldest_date(db):
    # Week ending on the 10th arrives first; the week ending on the 9th arrives late.
    newer = [(f"2026-10-{n:02d}", 100 + n) for n in range(4, 11)]
    older = [(f"2026-10-{n:02d}", 50 + n) for n in range(3, 10)]
    post(db, screen(20, "2026-10-10T20:00:00Z", newer))
    post(db, screen(19, "2026-10-09T20:00:00Z", older))
    rows = dict(query(db, "SELECT day::text, total_min FROM dash.screen_days"))
    assert rows["2026-10-03"] == 53          # only the late week had it
    assert rows["2026-10-04"] == 104         # the newer week wins
    assert rows["2026-10-10"] == 110
    assert len(rows) == 8


def test_two_installs_keep_separate_days(db):
    post(db, envelope(1, daily_totals=[totals("2026-10-04", 1000)]), install="phone-a")
    post(db, envelope(1, daily_totals=[totals("2026-10-04", 7000)]), install="phone-b")
    rows = query(db, "SELECT install, steps FROM dash.activity_days ORDER BY 1")
    assert rows == [("phone-a", 1000), ("phone-b", 7000)]


# ------------------------------------------------------------------ deletions

def test_delete_exact_uuid(db):
    post(db, envelope(1, weight=[weight("w1", 80.0), weight("w2", 81.0)]))
    post(db, envelope(2, deleted_records=[{"type": "weight", "uuid": "w1"}]))
    assert query(db, "SELECT uuid FROM records") == [("w2",)]
    assert query(db, "SELECT kind FROM payloads ORDER BY id") == [("sync",), ("deletion",)]


def test_delete_samples_by_record_id(db):
    hr = [{"bpm": 60 + i, "time": f"2026-10-04T06:0{i}:00Z", "uuid": f"rec#{1000 + i}", "source": "w"}
          for i in range(3)]
    hr.append({"bpm": 70, "time": "2026-10-04T07:00:00Z", "uuid": "other#1", "source": "w"})
    post(db, envelope(1, heart_rate=hr))
    post(db, envelope(2, deleted_records=[{"type": "heart_rate", "uuid": "rec"}]))
    assert query(db, "SELECT uuid FROM records") == [("other#1",)]


def test_delete_needs_matching_type(db):
    post(db, envelope(1, weight=[weight("w1", 80.0)]))
    post(db, envelope(2, deleted_records=[{"type": "body_fat", "uuid": "w1"}]))
    assert stored_kg(db, "w1") == 80.0


def test_delete_then_rewrite_under_same_uuid(db):
    post(db, envelope(1, sleep=[{"session_end_time": "2026-10-04T06:00:00Z", "duration_seconds": 28000,
                                 "stages": [], "uuid": "s1", "source": "fitbit"}]))
    post(db, envelope(2, deleted_records=[{"type": "sleep", "uuid": "s1"}]))
    assert query(db, "SELECT count(*) FROM records") == [(0,)]
    post(db, envelope(3, sleep=[{"session_end_time": "2026-10-04T06:30:00Z", "duration_seconds": 29800,
                                 "stages": [], "uuid": "s1", "source": "fitbit"}]))
    assert query(db, "SELECT (data->>'duration_seconds')::int FROM records") == [(29800,)]


def test_late_deletion_does_not_remove_a_newer_rewrite(db):
    post(db, envelope(3, ts="2026-10-04T09:00:00Z", weight=[weight("w1", 80.0)]))
    post(db, envelope(2, ts="2026-10-04T08:00:00Z", deleted_records=[{"type": "weight", "uuid": "w1"}]))
    assert stored_kg(db, "w1") == 80.0


def test_deletion_applies_before_records_of_the_same_payload(db):
    post(db, envelope(1, weight=[weight("old", 80.0)]))
    # Replace-by-delete: the old record is named deleted, a new one comes in the same payload.
    post(db, envelope(2, deleted_records=[{"type": "weight", "uuid": "old"}],
                      weight=[weight("new", 80.5)]))
    assert query(db, "SELECT uuid FROM records") == [("new",)]


def hr_bucket(start, end, n, avg, lo, hi, **extra):
    return {"bucket_start": start, "bucket_end": end, "sample_count": n, "avg": avg, "min": lo, "max": hi,
            "sources": ["watch"], **extra}


def test_buckets_without_complete_are_combined(db):
    body = post(db, envelope(1, heart_rate=[hr_bucket("2026-10-04T08:00:00Z", "2026-10-04T08:01:00Z",
                                                      30, 70.0, 65.0, 75.0)],
                             _resolutions={"heart_rate": "1m"}))
    assert body["stored"] == 0 and body["buckets"] == 1
    post(db, envelope(2, heart_rate=[hr_bucket("2026-10-04T08:00:00Z", "2026-10-04T08:01:00Z",
                                               10, 90.0, 80.0, 95.0)]))
    rows = query(db, "SELECT sample_count, avg, min, max FROM buckets")
    assert rows == [(40, 75.0, 65.0, 95.0)]
    assert query(db, "SELECT bpm, bpm_min, bpm_max, n FROM dash.heart_rate") == [(75.0, 65.0, 95.0, 40)]
    assert query(db, "SELECT bpm, n FROM dash.heart_rate_hourly") == [(75.0, 40)]
    assert query(db, "SELECT bpm, n, wear_h FROM dash.heart_days") == [(75.0, 40, 1 / 60)]


def test_a_repeated_bucket_payload_is_not_combined_twice(db):
    payload = envelope(1, heart_rate=[hr_bucket("2026-10-04T08:00:00Z", "2026-10-04T08:15:00Z", 30, 70.0, 65.0, 75.0)])
    post(db, payload)
    assert post(db, payload) == {"duplicate": True}
    assert query(db, "SELECT sample_count FROM buckets") == [(30,)]


def test_complete_buckets_replace_unless_older(db):
    steps = lambda seq, ts, total: envelope(seq, ts=ts, steps=[{
        "bucket_start": "2026-10-04T08:00:00Z", "bucket_end": "2026-10-04T09:00:00Z",
        "sample_count": 12, "total": total, "complete": True}])
    post(db, steps(1, "2026-10-04T09:05:00Z", 1840.0))
    post(db, steps(2, "2026-10-04T10:05:00Z", 1900.0))
    post(db, steps(1, "2026-10-04T09:00:00Z", 1500.0))  # an older copy arriving late
    assert query(db, "SELECT total, complete FROM buckets") == [(1900.0, True)]


def test_raw_samples_and_buckets_mix_in_the_hourly_view(db):
    post(db, envelope(1, heart_rate=[{"bpm": 60, "time": "2026-10-04T08:10:00Z", "uuid": "r#1", "source": "w"}]))
    post(db, envelope(2, heart_rate=[hr_bucket("2026-10-04T08:30:00Z", "2026-10-04T08:45:00Z", 3, 80.0, 70.0, 90.0)]))
    assert query(db, "SELECT bpm, bpm_min, bpm_max, n FROM dash.heart_rate_hourly") == [(75.0, 60.0, 90.0, 4)]
    assert query(db, "SELECT wear_h FROM dash.heart_days") == [(16 / 60,)]


def test_malformed_values_never_fail_an_insert(db):
    post(db, envelope(1, weight=[{"kilograms": "heavy", "time": "2026-10-04T07:00:00Z", "uuid": "w1"},
                                 {"kilograms": 80.0, "time": "not a time", "uuid": "w2"}],
                      heart_rate=[{"bpm": None, "time": "2026-10-04T07:00:00.123456789Z", "uuid": "h#1"}]))
    assert query(db, "SELECT uuid FROM records ORDER BY uuid") == [("h#1",), ("w1",)]
    assert query(db, "SELECT count(*) FROM dash.measurements") == [(0,)]
    assert query(db, "SELECT count(*) FROM dash.heart_rate") == [(0,)]


def test_a_1000_sample_payload_is_stored_in_under_2_seconds(db):
    hr = [{"bpm": 60 + i % 40, "time": f"2026-10-04T{i // 60 % 24:02d}:{i % 60:02d}:00Z",
           "uuid": f"rec#{i}", "source": "watch"} for i in range(1000)]
    body = json.dumps(envelope(1, heart_rate=hr)).encode()
    started = time.perf_counter()
    status, reply = receiver.ingest(db, "phone", body)
    elapsed = time.perf_counter() - started
    assert status == 200 and reply["stored"] == 1000
    assert elapsed < 2.0, elapsed


def test_eight_payloads_of_1000_samples_each_under_2_seconds(db):
    for n in range(8):
        hr = [{"bpm": 60 + i % 40, "time": f"2026-10-0{n + 1}T{i // 60 % 24:02d}:{i % 60:02d}:00Z",
               "uuid": f"rec{n}#{i}", "source": "watch"} for i in range(1000)]
        body = json.dumps(envelope(n + 1, heart_rate=hr)).encode()
        started = time.perf_counter()
        assert receiver.ingest(db, "phone", body)[0] == 200
        assert time.perf_counter() - started < 2.0
    assert query(db, "SELECT count(*) FROM records") == [(8000,)]


def test_late_payload_does_not_bring_back_a_deleted_record(db):
    post(db, envelope(1, ts="2026-10-04T08:00:00Z", weight=[weight("w1", 80.0)]))
    post(db, envelope(3, ts="2026-10-04T10:00:00Z", deleted_records=[{"type": "weight", "uuid": "w1"}]))
    post(db, envelope(2, ts="2026-10-04T09:00:00Z", weight=[weight("w1", 80.0)]))  # arrives late
    assert stored_kg(db, "w1") is None
    post(db, envelope(4, ts="2026-10-04T11:00:00Z", weight=[weight("w1", 81.0)]))  # rewritten later
    assert stored_kg(db, "w1") == 81.0


def test_late_samples_of_a_deleted_heart_rate_record_stay_deleted(db):
    post(db, envelope(5, ts="2026-10-04T10:00:00Z", deleted_records=[{"type": "heart_rate", "uuid": "rec"}]))
    post(db, envelope(4, ts="2026-10-04T09:00:00Z", heart_rate=[
        {"bpm": 60, "time": "2026-10-04T06:00:00Z", "uuid": "rec#1", "source": "w"},
        {"bpm": 61, "time": "2026-10-04T06:01:00Z", "uuid": "other#1", "source": "w"}]))
    assert query(db, "SELECT uuid FROM records") == [("other#1",)]
