"""Read-only queries on the dash views, shared by the HTTP API (api.py) and the MCP server.

Every query reads the dash views and nothing else, through a connection as dash_ro: a
database user that can only SELECT from the dash schema. The connection is read-only on
three levels: the role's grants, the role's default_transaction_read_only, and the same
setting again in the connection options. Days are local days in the stack's TZ, and
timestamps come back in that zone.

The functions take plain values (dates, a phone label, a metric name), check them, and
return JSON-ready dicts: dates as YYYY-MM-DD, timestamps as ISO 8601 with their offset,
numbers rounded. A bad value raises BadRequest, an unknown phone NotFound.
"""

import re
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import psycopg
from psycopg.rows import dict_row

PHONE_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STATEMENT_TIMEOUT = "15s"


class BadRequest(ValueError):
    """A parameter is missing, malformed or out of range."""


class NotFound(LookupError):
    """The phone has never sent data."""


# ------------------------------------------------------------------ metrics

# A metric is one value per phone and local day. sql selects install, day and value from
# one view, with {where} for the day range and the phone. sum_metric: a day without a row
# counts as zero in a period's mean (no workout that day means 0 minutes of exercise).
_WHERE = "day BETWEEN %(start)s AND %(end)s AND (%(phone)s::text IS NULL OR install = %(phone)s)"


def _measure(measure: str) -> str:
    return ("SELECT install, day, avg(avg) AS value FROM dash.measurement_days"
            f" WHERE measure = '{measure}' AND {{where}} GROUP BY install, day")


METRICS: dict[str, dict] = {
    "steps": {"unit": "steps", "about": "Steps per day, counted once across phone and watch",
              "sql": "SELECT install, day, steps::float8 AS value FROM dash.activity_days WHERE {where}"},
    "distance_km": {"unit": "km", "about": "Distance per day",
                    "sql": "SELECT install, day, distance_m / 1000 AS value FROM dash.activity_days WHERE {where}"},
    "active_kcal": {"unit": "kcal", "about": "Active energy per day",
                    "sql": "SELECT install, day, active_kcal AS value FROM dash.activity_days WHERE {where}"},
    "total_kcal": {"unit": "kcal", "about": "Total energy per day",
                   "sql": "SELECT install, day, total_kcal AS value FROM dash.activity_days WHERE {where}"},
    "exercise_min": {"unit": "min", "about": "Workout minutes per day", "sum_metric": True,
                     "sql": "SELECT install, day, sum(minutes) AS value FROM dash.workouts"
                            " WHERE {where} GROUP BY install, day"},
    "sleep_h": {"unit": "h", "about": "Hours asleep per night, on the date you woke up",
                "sql": "SELECT install, day, asleep_h AS value FROM dash.sleep_main WHERE {where}"},
    "in_bed_h": {"unit": "h", "about": "Hours in bed per night",
                 "sql": "SELECT install, day, in_bed_h AS value FROM dash.sleep_main WHERE {where}"},
    "deep_sleep_h": {"unit": "h", "about": "Hours of deep sleep per night (nights with stages)",
                     "sql": "SELECT install, day, deep_h AS value FROM dash.sleep_main WHERE {where}"
                            " AND deep_h IS NOT NULL"},
    "rem_sleep_h": {"unit": "h", "about": "Hours of REM sleep per night (nights with stages)",
                    "sql": "SELECT install, day, rem_h AS value FROM dash.sleep_main WHERE {where}"
                           " AND rem_h IS NOT NULL"},
    "sleep_hr_low": {"unit": "bpm", "about": "Lowest 10-minute average heart rate during the night",
                     "sql": "SELECT install, day, hr_low AS value FROM dash.sleep_nights WHERE {where}"
                            " AND hr_low IS NOT NULL"},
    "heart_rate": {"unit": "bpm", "about": "Mean heart rate per day",
                   "sql": "SELECT install, day, bpm AS value FROM dash.heart_days WHERE {where}"},
    "resting_heart_rate": {"unit": "bpm", "about": "Resting heart rate as the watch reports it",
                           "sql": _measure("resting_heart_rate")},
    "hrv_rmssd": {"unit": "ms", "about": "Heart rate variability, RMSSD (Android)", "sql": _measure("rmssd")},
    "hrv_sdnn": {"unit": "ms", "about": "Heart rate variability, SDNN (iPhone)", "sql": _measure("sdnn")},
    "oxygen_saturation": {"unit": "%", "about": "SpO2", "sql": _measure("oxygen_saturation")},
    "respiratory_rate": {"unit": "/min", "about": "Breathing rate", "sql": _measure("respiratory_rate")},
    "vo2_max": {"unit": "ml/kg/min", "about": "VO2 max", "sql": _measure("vo2_max")},
    "weight": {"unit": "kg", "about": "First weighing of the day",
               "sql": "SELECT install, day, kg AS value FROM dash.weight_days WHERE {where}"},
    "body_fat": {"unit": "%", "about": "Body fat", "sql": _measure("body_fat")},
    "systolic": {"unit": "mmHg", "about": "Blood pressure, systolic", "sql": _measure("systolic")},
    "diastolic": {"unit": "mmHg", "about": "Blood pressure, diastolic", "sql": _measure("diastolic")},
    "blood_glucose": {"unit": "mmol/L", "about": "Blood glucose", "sql": _measure("blood_glucose")},
    "screen_min": {"unit": "min", "about": "Screen time per day, all apps (Android only)",
                   "sql": "SELECT install, day, sum(total_min)::float8 AS value FROM dash.screen_days"
                          " WHERE {where} GROUP BY install, day"},
}


# ------------------------------------------------------------------ input checks

def parse_day(value, name: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not DATE_RE.match(value):
        raise BadRequest(f"{name} must be a date as YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise BadRequest(f"{name} is not a valid date: {value}") from None


def check_phone(phone) -> str | None:
    if phone is None or phone == "":
        return None
    if not isinstance(phone, str) or not PHONE_RE.match(phone):
        raise BadRequest("phone must be a label: lowercase letters, digits, - and _, at most 32 characters")
    return phone


def check_range(start: date, end: date, max_days: int) -> None:
    if end < start:
        raise BadRequest("from must be on or before to")
    if (end - start).days + 1 > max_days:
        raise BadRequest(f"the range is at most {max_days} days")


def check_metric(metric) -> str:
    if metric not in METRICS:
        raise BadRequest("metric must be one of: " + ", ".join(METRICS))
    return metric


# ------------------------------------------------------------------ values

def _value(v):
    if isinstance(v, float):
        return round(v, 2)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return v


def _clean(row: dict) -> dict:
    return {k: _value(v) for k, v in row.items()}


def _capped(rows: list[dict], limit: int) -> tuple[list[dict], bool]:
    return [_clean(r) for r in rows[:limit]], len(rows) > limit


# ------------------------------------------------------------------ reader

class Reader:
    """Queries over one read-only connection."""

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self.tz = conn.execute("SELECT dash.tz() AS tz").fetchone()["tz"]
        # Timestamps come back in the stack's zone, like the days they belong to.
        conn.execute("SELECT set_config('TimeZone', %s, false)", (self.tz,))

    def _all(self, text: str, params=None) -> list[dict]:
        return self.conn.execute(text, params).fetchall()

    def today(self) -> date:
        return self.conn.execute("SELECT dash.today() AS d").fetchone()["d"]

    def default_range(self, start, end, span: int) -> tuple[date, date]:
        """from and to as dates; to defaults to today, from to span days up to to."""
        end = parse_day(end, "to") if end not in (None, "") else self.today()
        start = parse_day(start, "from") if start not in (None, "") else end - timedelta(days=span - 1)
        return start, end

    def require_phone(self, phone) -> str | None:
        phone = check_phone(phone)
        if phone is not None:
            found = self.conn.execute("SELECT 1 FROM dash.installs WHERE install = %s LIMIT 1", (phone,))
            if found.fetchone() is None:
                raise NotFound(f"no phone with the label {phone}")
        return phone

    # -------------------------------------------------------------- phones

    def phones(self) -> list[dict]:
        rows = self._all(
            """
            WITH i AS (
                SELECT install,
                       array_agg(DISTINCT platform ORDER BY platform) AS platforms,
                       max(device) AS device,
                       max(last_received) AS last_received,
                       (array_agg(app_version ORDER BY last_received DESC NULLS LAST)
                            FILTER (WHERE app_version IS NOT NULL))[1] AS app_version
                FROM dash.installs GROUP BY install
            ),
            d AS (
                SELECT install, min(day) AS first_day, max(day) AS last_day FROM (
                    SELECT install, day FROM dash.activity_days
                    UNION ALL SELECT install, day FROM dash.sleep_main
                    UNION ALL SELECT install, day FROM dash.screen_days
                ) x GROUP BY install
            )
            SELECT i.install AS phone, i.platforms, i.device, i.app_version, i.last_received,
                   extract(epoch FROM now() - i.last_received)::float8 / 3600 AS hours_since,
                   d.first_day, d.last_day
            FROM i LEFT JOIN d USING (install)
            ORDER BY i.install
            """
        )
        return [_clean(r) for r in rows]

    # -------------------------------------------------------------- days

    def days(self, start: date, end: date, phone: str | None, limit: int) -> tuple[list[dict], bool]:
        rows = self._all(
            f"""
            WITH a AS (SELECT install, day, steps, distance_m, active_kcal, total_kcal
                       FROM dash.activity_days WHERE {_WHERE}),
                 s AS (SELECT install, day, asleep_h, in_bed_h FROM dash.sleep_main WHERE {_WHERE}),
                 w AS (SELECT install, day, sum(minutes) AS exercise_min, count(*)::int AS workouts
                       FROM dash.workouts WHERE {_WHERE} GROUP BY install, day),
                 sc AS (SELECT install, day, sum(total_min)::int AS screen_min
                        FROM dash.screen_days WHERE {_WHERE} GROUP BY install, day),
                 rh AS (SELECT install, day, avg(avg) AS resting_hr FROM dash.measurement_days
                        WHERE measure = 'resting_heart_rate' AND {_WHERE} GROUP BY install, day),
                 wt AS (SELECT install, day, kg FROM dash.weight_days WHERE {_WHERE}),
                 k AS (SELECT install, day FROM a UNION SELECT install, day FROM s
                       UNION SELECT install, day FROM w UNION SELECT install, day FROM sc
                       UNION SELECT install, day FROM rh UNION SELECT install, day FROM wt)
            SELECT k.day, k.install AS phone, a.steps, a.distance_m / 1000 AS distance_km,
                   a.active_kcal, a.total_kcal, s.asleep_h AS sleep_h, s.in_bed_h,
                   w.exercise_min, w.workouts, sc.screen_min, rh.resting_hr, wt.kg AS weight_kg,
                   k.day < dash.today() AS complete
            FROM k
            LEFT JOIN a USING (install, day) LEFT JOIN s USING (install, day)
            LEFT JOIN w USING (install, day) LEFT JOIN sc USING (install, day)
            LEFT JOIN rh USING (install, day) LEFT JOIN wt USING (install, day)
            ORDER BY k.install, k.day
            LIMIT %(limit)s
            """,
            {"start": start, "end": end, "phone": phone, "limit": limit + 1},
        )
        return _capped(rows, limit)

    # -------------------------------------------------------------- sleep

    def sleep(self, start: date, end: date, phone: str | None, limit: int) -> tuple[list[dict], bool]:
        rows = self._all(
            f"""
            SELECT day, install AS phone, start_time AS bedtime, end_time AS wake_time,
                   asleep_h, in_bed_h, deep_h, light_h, rem_h, awake_h, has_stages,
                   hr_low, hr_avg, source_app, complete
            FROM dash.sleep_nights WHERE {_WHERE}
            ORDER BY install, day
            LIMIT %(limit)s
            """,
            {"start": start, "end": end, "phone": phone, "limit": limit + 1},
        )
        return _capped(rows, limit)

    # -------------------------------------------------------------- workouts

    def workouts(self, start: date, end: date, phone: str | None, limit: int) -> tuple[list[dict], bool]:
        rows = self._all(
            f"""
            SELECT day, install AS phone, "time" AS start_time, end_time, activity, minutes, source_app
            FROM dash.workouts WHERE {_WHERE}
            ORDER BY "time", install
            LIMIT %(limit)s
            """,
            {"start": start, "end": end, "phone": phone, "limit": limit + 1},
        )
        return _capped(rows, limit)

    # -------------------------------------------------------------- screen time

    def screen_time(self, day: date, phone: str | None, top: int) -> list[dict]:
        params = {"start": day, "end": day, "phone": phone, "top": top}
        phones = self._all(
            f"""
            SELECT install AS phone, device, total_min, filtered_min, app_filter, apps, last_use, complete
            FROM dash.screen_days WHERE {_WHERE}
            ORDER BY install, device
            """,
            params,
        )
        apps = self._all(
            f"""
            SELECT install, device, app, package, minutes, last_used FROM (
                SELECT install, device, app, package, minutes, last_used,
                       row_number() OVER (PARTITION BY install, device
                                          ORDER BY minutes DESC NULLS LAST, app) AS n
                FROM dash.screen_apps WHERE {_WHERE}
            ) x WHERE n <= %(top)s
            ORDER BY install, device, n
            """,
            params,
        )
        out = []
        for p in phones:
            entry = _clean(p)
            entry["top_apps"] = [
                _clean({k: a[k] for k in ("app", "package", "minutes", "last_used")})
                for a in apps if a["install"] == p["phone"] and a["device"] == p["device"]
            ]
            out.append(entry)
        return out

    # -------------------------------------------------------------- series and trend

    def series(self, metric: str, start: date, end: date, phone: str | None, limit: int) -> tuple[list[dict], bool]:
        spec = METRICS[check_metric(metric)]
        rows = self._all(
            f"SELECT day, install AS phone, value FROM ({spec['sql'].format(where=_WHERE)}) m"
            " WHERE value IS NOT NULL ORDER BY install, day LIMIT %(limit)s",
            {"start": start, "end": end, "phone": phone, "limit": limit + 1},
        )
        return _capped(rows, limit)

    def trend(self, metric: str, days: int, phone: str | None) -> list[dict]:
        """The last `days` complete days (up to yesterday) against the `days` before them."""
        spec = METRICS[check_metric(metric)]
        end = self.today() - timedelta(days=1)
        start = end - timedelta(days=days - 1)
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=days - 1)
        rows = self._all(
            f"SELECT day, install, value FROM ({spec['sql'].format(where=_WHERE)}) m"
            " WHERE value IS NOT NULL ORDER BY install, day",
            {"start": prev_start, "end": end, "phone": phone},
        )
        by_phone: dict[str, list[dict]] = {}
        for r in rows:
            by_phone.setdefault(r["install"], []).append(r)
        if phone is not None:
            by_phone.setdefault(phone, [])
        out = []
        for name, items in sorted(by_phone.items()):
            cur = [r for r in items if r["day"] >= start]
            prev = [r for r in items if r["day"] <= prev_end]
            out.append(_trend_entry(name, metric, spec, cur, prev, (start, end), (prev_start, prev_end), days))
        return out


def _stats(items: list[dict], days: int, sum_metric: bool) -> dict:
    values = [r["value"] for r in items]
    if sum_metric:
        mean = sum(values) / days if days else None
    else:
        mean = sum(values) / len(values) if values else None
    stats = {"days_with_data": len(values), "mean": mean}
    if values:
        lo = min(items, key=lambda r: r["value"])
        hi = max(items, key=lambda r: r["value"])
        stats.update(min=lo["value"], min_day=lo["day"], max=hi["value"], max_day=hi["day"])
        if sum_metric:
            stats["total"] = sum(values)
    return _clean(stats)


def _trend_entry(phone, metric, spec, cur, prev, period, prev_period, days) -> dict:
    sum_metric = spec.get("sum_metric", False)
    now = _stats(cur, days, sum_metric)
    before = _stats(prev, days, sum_metric)
    change = change_pct = None
    if cur and prev and now["mean"] is not None and before["mean"] is not None:
        change = now["mean"] - before["mean"]
        if before["mean"]:
            change_pct = change / before["mean"] * 100
    return {
        "phone": phone, "metric": metric, "unit": spec["unit"],
        "period": {"from": period[0].isoformat(), "to": period[1].isoformat(), **now},
        "previous": {"from": prev_period[0].isoformat(), "to": prev_period[1].isoformat(),
                     "days_with_data": before["days_with_data"], "mean": before["mean"]},
        "change": _value(change), "change_pct": _value(change_pct),
    }


@contextmanager
def open_reader(conninfo: str = ""):
    """A Reader on a new read-only connection. An empty conninfo uses the PG* variables."""
    conn = psycopg.connect(
        conninfo, autocommit=True, row_factory=dict_row, connect_timeout=5,
        options=f"-c default_transaction_read_only=on -c statement_timeout={STATEMENT_TIMEOUT}",
    )
    try:
        yield Reader(conn)
    finally:
        conn.close()
