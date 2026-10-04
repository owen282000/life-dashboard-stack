"""Post 60 days of made-up data for two phones, signed like the apps sign it.

The installs are demo-android (Health Connect plus screen time) and demo-iphone (Apple
Health). Everything is generated: a watch and a phone that both count steps, heart rate
every 5 minutes, nights with sleep stages, weigh-ins, workouts, resting heart rate, HRV,
SpO2, breathing rate, VO2 max, blood pressure, and per-app screen time whose last use of
the evening follows bedtime.

Usage:
    URL=http://localhost:8080 SECRET=... TZ=Europe/Amsterdam python3 tools/sample_data.py
    python3 tools/sample_data.py android      only demo-android
    python3 tools/sample_data.py ios          only demo-iphone
    DRY=1 python3 tools/sample_data.py        write the JSON bodies to ./sample-out instead

Running it again is harmless: the records keep their IDs, so they replace themselves.
To remove the sample data afterward:
    docker compose exec db psql -U lifedash -c "DELETE FROM records WHERE install LIKE 'demo-%'; DELETE FROM payloads WHERE install LIKE 'demo-%'"
"""

import hashlib
import hmac
import json
import math
import os
import random
import sys
import urllib.error
import urllib.request
import uuid as uuidlib
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    LOCAL = ZoneInfo(os.environ.get("TZ") or "UTC")
except Exception:  # noqa: BLE001  an unknown zone falls back to UTC
    LOCAL = timezone.utc

BASE_URL = os.environ.get("URL", "http://localhost:8080").rstrip("/")
SECRET = os.environ.get("SECRET", "")
DRY = os.environ.get("DRY") == "1"
DAYS = int(os.environ.get("DAYS", "60"))
OUT = os.path.join(os.getcwd(), "sample-out")

NOW = datetime.now(timezone.utc).replace(microsecond=0)
TODAY = NOW.astimezone(LOCAL).date()
NAMESPACE = uuidlib.UUID("6f1c9e7a-3d2b-4c55-9a51-0d6c2b7e8f10")


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_dt(d, h, m=0):
    """A local wall time; hours past 23 roll into the next day."""
    return datetime(d.year, d.month, d.day, tzinfo=LOCAL) + timedelta(hours=h, minutes=m)


def stable_uuid(*parts, upper=False):
    """The same record gets the same uuid on every run, like a record in Health Connect."""
    u = str(uuidlib.uuid5(NAMESPACE, "|".join(str(p) for p in parts)))
    return u.upper() if upper else u


def rng(*parts):
    return random.Random("|".join(str(p) for p in parts))


def post(install, payload, name):
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if DRY:
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, f"{install}-{name}.json"), "wb") as f:
            f.write(body)
        return "written"
    req = urllib.request.Request(f"{BASE_URL}/webhook/{install}", data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    if SECRET:
        sig = hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
        req.add_header("X-Signature", "sha256=" + sig)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return f"{e.code} {e.read().decode(errors='replace')}"


# ------------------------------------------------------------------ one simulated person

class Person:
    def __init__(self, seed, ios):
        self.seed = seed
        self.ios = ios
        self.cache = {}

    def day(self, d):
        """Steps per hour, sleep and screen time for one local day."""
        if d in self.cache:
            return self.cache[d]
        r = rng(self.seed, "day", d)
        i = (d - date(2026, 1, 1)).days
        weekend = d.weekday() >= 5
        base = 8500 + 2500 * math.sin(i / 5.0) + (2000 if weekend else 0) + r.randint(-1500, 1500)
        weights = {}
        for h in range(7, 23):
            w = 1.0 + (2.5 if h in (8, 12, 17, 18) else 0) + (1.5 if weekend and 10 <= h <= 15 else 0)
            weights[h] = w
        total_w = sum(weights.values())
        hourly = {h: int(base * w / total_w) for h, w in weights.items()}
        # The night that ends this morning: later on weekends.
        wake = local_dt(d, 8, r.randint(15, 75)) if weekend else local_dt(d, 6, r.randint(40, 85))
        sleep_h = 7.1 + 0.7 * math.sin(i / 3.3) + r.uniform(-0.6, 0.6) + (0.4 if weekend else 0)
        screen = int(185 + 40 * math.sin(i / 4.0) + r.randint(-35, 35) + (50 if weekend else 0))
        workout = None
        if d.weekday() in (1, 3, 5) or (d.weekday() == 6 and r.random() < 0.5):
            kind = ["running", "walking", "biking", "strength"][(i + d.weekday()) % 4]
            start_h = 10 if weekend else 18
            workout = (kind, local_dt(d, start_h, r.randint(0, 40)), r.randint(28, 65))
        self.cache[d] = {
            "hourly": hourly, "wake": wake, "sleep_h": sleep_h,
            "bed": wake - timedelta(hours=sleep_h), "screen": screen, "workout": workout,
        }
        return self.cache[d]

    def asleep_at(self, t):
        d = t.astimezone(LOCAL).date()
        for night in (d, d + timedelta(days=1)):
            p = self.day(night)
            if p["bed"] <= t < p["wake"]:
                return True
        return False


# ------------------------------------------------------------------ Health records

HC_WORKOUT = {"running": "56", "walking": "79", "biking": "8", "strength": "70"}
IOS_WORKOUT = {"running": "running", "walking": "walking", "biking": "cycling",
               "strength": "traditional_strength_training"}


def health_records(person, days):
    ios = person.ios
    watch = "Apple Watch" if ios else "com.garmin.android.apps.connectmobile"
    phone = "iPhone" if ios else "com.google.android.apps.fitness"
    scale = "Withings" if ios else "com.withings.wiscale2"
    out = {k: [] for k in ("steps", "heart_rate", "sleep", "weight", "body_fat", "exercise",
                           "resting_heart_rate", "heart_rate_variability", "oxygen_saturation",
                           "respiratory_rate", "vo2_max", "blood_pressure", "skin_temperature")}
    totals = []
    for d in days:
        p = person.day(d)
        r = rng(person.seed, "records", d)
        counted = 0
        for h, c in p["hourly"].items():
            if local_dt(d, h + 1) > NOW:
                continue
            counted += c
            out["steps"].append({"count": c, "start_time": iso(local_dt(d, h)), "end_time": iso(local_dt(d, h + 1)),
                                 "uuid": stable_uuid(person.seed, "steps", d, h, upper=ios), "source": watch})
            # The phone counts the commute hours as well: the double count daily_totals removes.
            if h in (8, 17):
                out["steps"].append({"count": int(c * 0.9), "start_time": iso(local_dt(d, h)),
                                     "end_time": iso(local_dt(d, h + 1)),
                                     "uuid": stable_uuid(person.seed, "steps-phone", d, h, upper=ios),
                                     "source": phone})
        if counted:
            active = round(counted * 0.042 + r.uniform(20, 60), 1)
            hours_so_far = min(24.0, max(0.0, (min(NOW, local_dt(d, 24)) - local_dt(d, 0)).total_seconds() / 3600))
            totals.append({"date": d.isoformat(), "steps": counted, "distance_meters": round(counted * 0.74, 1),
                           "active_calories": active,
                           "total_calories": round(active + 1690 * hours_so_far / 24, 1)})

        # Heart rate every 5 minutes; one Health Connect record per day holds the samples.
        record = stable_uuid(person.seed, "hr", d)
        t = local_dt(d, 0)
        workout = p["workout"]
        while t < local_dt(d, 24) and t <= NOW:
            hour = t.astimezone(LOCAL).hour + t.astimezone(LOCAL).minute / 60
            if person.asleep_at(t):
                bpm = 52 + int(4 * math.sin(hour)) + r.randint(-3, 4)
            else:
                bpm = 68 + int(7 * math.sin(hour / 3)) + r.randint(-5, 7)
                if p["hourly"].get(int(hour), 0) > 900 and r.random() < 0.3:
                    bpm += r.randint(15, 35)
            if workout and workout[1] <= t < workout[1] + timedelta(minutes=workout[2]):
                bpm = (138 if workout[0] == "running" else 112) + r.randint(-10, 14)
            ms = int(t.timestamp() * 1000)
            out["heart_rate"].append({"bpm": bpm, "time": iso(t), "source": watch,
                                      "uuid": stable_uuid(person.seed, "hr", ms, upper=True) if ios else f"{record}#{ms}"})
            t += timedelta(minutes=5)

        # The night that ends this morning.
        start, end = p["bed"], p["wake"]
        if end <= NOW:
            dur = int((end - start).total_seconds())
            stages, cur, k = [], start, 0
            cycle = ["light", "deep", "light", "rem"]
            while cur < end:
                st = cycle[k % 4]
                ln = {"light": 35, "deep": 25, "rem": 22}[st] + r.randint(-8, 8)
                nxt = min(cur + timedelta(minutes=ln), end)
                stages.append({"stage": st, "start_time": iso(cur), "end_time": iso(nxt),
                               "duration_seconds": int((nxt - cur).total_seconds())})
                cur, k = nxt, k + 1
                if r.random() < 0.1 and cur < end:
                    nxt = min(cur + timedelta(minutes=r.randint(3, 9)), end)
                    stages.append({"stage": "awake", "start_time": iso(cur), "end_time": iso(nxt),
                                   "duration_seconds": int((nxt - cur).total_seconds())})
                    cur = nxt
            if ios:
                for n, st in enumerate(stages):
                    st["uuid"] = stable_uuid(person.seed, "stage", d, n, upper=True)
                    st["source"] = watch
                # The iPhone's own in-bed sample around the night: not a sleep stage.
                stages.insert(0, {"stage": "in_bed", "start_time": iso(start - timedelta(minutes=14)),
                                  "end_time": iso(end + timedelta(minutes=6)), "duration_seconds": dur + 20 * 60,
                                  "uuid": stable_uuid(person.seed, "inbed", d, upper=True), "source": phone})
            out["sleep"].append({"session_end_time": iso(end), "duration_seconds": dur, "stages": stages,
                                 "uuid": stable_uuid(person.seed, "sleep", d, upper=ios), "source": watch})
            # Overnight readings the watch takes.
            for n in range(4):
                at = start + timedelta(minutes=60 + n * 80)
                rmssd = 38 + 8 * math.sin((d - TODAY).days / 6) + r.uniform(-6, 6)
                out["heart_rate_variability"].append({
                    "heart_rate_variability_millis": round(rmssd * (1.35 if ios else 1.0), 1), "time": iso(at),
                    "uuid": stable_uuid(person.seed, "hrv", d, n, upper=ios), "source": watch})
                out["oxygen_saturation"].append({
                    "percentage": round(r.uniform(94.5, 98.5), 1), "time": iso(at + timedelta(minutes=7)),
                    "uuid": stable_uuid(person.seed, "spo2", d, n, upper=ios), "source": watch})
                if not ios:
                    record_st = stable_uuid(person.seed, "skin", d)
                    ms = int((at + timedelta(minutes=3)).timestamp() * 1000)
                    out["skin_temperature"].append({
                        "delta_celsius": round(r.uniform(-0.4, 0.4), 2), "baseline_celsius": 33.6,
                        "time": iso(at + timedelta(minutes=3)), "uuid": f"{record_st}#{ms}", "source": watch})
            out["respiratory_rate"].append({"rate": round(r.uniform(13.2, 15.8), 1), "time": iso(end - timedelta(minutes=30)),
                                            "uuid": stable_uuid(person.seed, "resp", d, upper=ios), "source": watch})
            out["resting_heart_rate"].append({"bpm": 54 + r.randint(-3, 4), "time": iso(end + timedelta(minutes=20)),
                                              "uuid": stable_uuid(person.seed, "rhr", d, upper=ios), "source": watch})

        if workout and workout[1] + timedelta(minutes=workout[2]) <= NOW:
            kind, w_start, minutes = workout
            out["exercise"].append({"type": (IOS_WORKOUT if ios else HC_WORKOUT)[kind],
                                    "start_time": iso(w_start), "end_time": iso(w_start + timedelta(minutes=minutes)),
                                    "duration_seconds": minutes * 60,
                                    "uuid": stable_uuid(person.seed, "exercise", d, upper=ios), "source": watch})

        # Weigh-ins four mornings a week, with body fat from the same scale.
        weigh = local_dt(d, 7, 55) if d.weekday() < 5 else local_dt(d, 9, 40)
        if d.weekday() in (0, 2, 4, 6) and weigh <= NOW:
            i = (d - TODAY).days
            kg = round((77.8 if not ios else 64.2) - 0.03 * i + 0.3 * math.sin(i / 2.7) + r.uniform(-0.15, 0.15), 1)
            out["weight"].append({"kilograms": kg, "time": iso(weigh),
                                  "uuid": stable_uuid(person.seed, "weight", d, upper=ios), "source": scale})
            out["body_fat"].append({"percentage": round(19.5 - 0.01 * i + r.uniform(-0.4, 0.4), 1), "time": iso(weigh),
                                    "uuid": stable_uuid(person.seed, "fat", d, upper=ios), "source": scale})
        if d.weekday() == 6 and local_dt(d, 10) <= NOW:
            out["vo2_max"].append({"vo2_ml_per_min_per_kg": round(44.0 + 0.02 * (d - TODAY).days + r.uniform(-0.6, 0.6), 1),
                                   "time": iso(local_dt(d, 10)),
                                   "uuid": stable_uuid(person.seed, "vo2", d, upper=ios), "source": watch})
        if d.weekday() == 2 and local_dt(d, 8, 10) <= NOW:
            out["blood_pressure"].append({"systolic": float(118 + r.randint(-6, 8)), "diastolic": float(77 + r.randint(-5, 6)),
                                          "time": iso(local_dt(d, 8, 10)),
                                          "uuid": stable_uuid(person.seed, "bp", d, upper=ios),
                                          "source": "Omron" if ios else "jp.co.omron.healthcare.omron_connect"})
    if ios:
        out.pop("skin_temperature")
    return out, totals


def health_payloads(person, first_day, seq):
    """A backfill in 3-day windows, as the apps send one."""
    ios = person.ios
    payloads = []
    w = local_dt(first_day, 0)
    while w < NOW:
        we = min(w + timedelta(hours=72), NOW)
        days = []
        d = w.astimezone(LOCAL).date()
        while d <= we.astimezone(LOCAL).date():
            days.append(d)
            d += timedelta(days=1)
        recs, totals = health_records(person, days)
        seq += 1
        p = {"timestamp": iso(NOW), "app_version": "1.6.0" if ios else "1.23.0",
             "source": "healthkit_ios" if ios else "health_connect", "sequence": seq,
             "backfill": True, "window_start": iso(w), "window_end": iso(we),
             "window_complete": True, "daily_totals": totals}
        for key, items in recs.items():
            field = "session_end_time" if key == "sleep" else ("start_time" if key in ("steps", "exercise") else "time")
            items = [x for x in items if iso(w) <= x[field] < iso(we)]
            if items:
                p[key] = items
        payloads.append((p, f"backfill-{w.astimezone(LOCAL).date()}"))
        w = we
    return payloads, seq


# ------------------------------------------------------------------ screen time

APPS = [("com.whatsapp", "WhatsApp"), ("com.google.android.youtube", "YouTube"),
        ("com.instagram.android", "Instagram"), ("org.mozilla.firefox", "Firefox"),
        ("com.spotify.music", "Spotify"), ("com.google.android.gm", "Gmail"),
        ("io.homeassistant.companion.android", "Home Assistant"), ("com.reddit.frontpage", "Reddit"),
        ("com.google.android.apps.maps", "Maps"), ("com.duolingo", "Duolingo")]


def screen_day(person, dd, sync_at):
    """One date of a Screen Time payload as known at sync_at."""
    r = rng(person.seed, "screen", dd)
    total = person.day(dd)["screen"]
    # The last use of the evening follows the bedtime of the night after it: mostly a
    # little before falling asleep, sometimes in bed after it, later on weekends.
    bed = person.day(dd + timedelta(days=1))["bed"]
    offset = r.gauss(-22, 24) + (15 if dd.weekday() >= 4 else 0)
    last = bed + timedelta(minutes=max(-150.0, min(25.0, offset)))
    day_end = local_dt(dd, 28)  # 04:00 the next morning, the default day boundary
    if sync_at < day_end:
        done = max(0.0, min(1.0, (sync_at - local_dt(dd, 4)).total_seconds() / (20 * 3600)))
        total = int(total * done)
        last = min(last, sync_at - timedelta(minutes=r.randint(2, 30)))
    weights = [r.random() ** 2 for _ in APPS]
    s = sum(weights)
    parts = [int(total * w / s) for w in weights]
    apps = []
    for n, ((pkg, name), minutes) in enumerate(zip(APPS, parts)):
        if minutes <= 1:
            continue
        earliest = local_dt(dd, 8)
        span = max(0.0, (last - earliest).total_seconds())
        lu = earliest + timedelta(seconds=r.uniform(0.3, 0.95) * span)
        apps.append({"package": pkg, "name": name, "minutes": minutes, "last_used": iso(lu)})
    if apps:
        apps.sort(key=lambda a: -a["minutes"])
        top = 0 if r.random() < 0.6 else min(2, len(apps) - 1)
        apps[top]["last_used"] = iso(last)
    return {"date": dd.isoformat(), "total_screen_time_minutes": total, "apps": apps}


def screen_payloads(person, first_day, seq):
    """One payload per evening sync, each with the last 7 days."""
    out = []
    d = first_day
    while d <= TODAY:
        sync_at = min(local_dt(d, 21, 15), NOW)
        days = [screen_day(person, d - timedelta(days=back), sync_at) for back in range(6, -1, -1)
                if d - timedelta(days=back) >= first_day - timedelta(days=6)]
        seq += 1
        out.append(({"timestamp": iso(sync_at), "app_version": "1.23.0", "device": "Samsung SM-S918B",
                     "source": "screen_time", "sequence": seq, "screen_time": days}, f"screen-{d}"))
        d += timedelta(days=1)
    return out, seq


# ------------------------------------------------------------------ main

def ping(source, app_version):
    return {"test": True, "message": "Test ping from Life Dashboard Companion", "timestamp": iso(NOW),
            "app_version": app_version, "source": source}


def run_android():
    person = Person("demo-android", ios=False)
    first = TODAY - timedelta(days=DAYS - 1)
    jobs = [(ping("health_connect", "1.23.0"), "ping")]
    health, seq = health_payloads(person, first, 41000)
    jobs += health
    # A weight typed in by mistake, then deleted on the phone: the deletion removes it.
    seq += 1
    typo = stable_uuid("demo-android", "typo-weight")
    jobs.append(({"timestamp": iso(NOW), "app_version": "1.23.0", "source": "health_connect", "sequence": seq,
                  "weight": [{"kilograms": 7.78, "time": iso(NOW - timedelta(hours=3)), "uuid": typo,
                              "source": "com.google.android.apps.fitness"}]}, "typo"))
    seq += 1
    jobs.append(({"timestamp": iso(NOW), "app_version": "1.23.0", "source": "health_connect", "sequence": seq,
                  "deleted_records": [{"type": "weight", "uuid": typo}]}, "deletion"))
    screen, seq = screen_payloads(person, first, seq)
    jobs += screen
    return jobs


def run_iphone():
    person = Person("demo-iphone", ios=True)
    first = TODAY - timedelta(days=DAYS - 1)
    jobs = [(ping("healthkit_ios", "1.6.0"), "ping")]
    health, _ = health_payloads(person, first, 900)
    return jobs + health


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    targets = []
    if which in ("all", "android"):
        targets.append(("demo-android", run_android()))
    if which in ("all", "ios"):
        targets.append(("demo-iphone", run_iphone()))
    failed = 0
    for install, jobs in targets:
        for payload, name in jobs:
            result = post(install, payload, name)
            if result not in (200, "written"):
                failed += 1
                print(f"{install} {name}: {result}", file=sys.stderr)
        print(f"{install}: {len(jobs)} payloads {'written' if DRY else 'posted'}", flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
