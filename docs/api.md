# Read-only API

The receiver can answer questions as well as take payloads. With `API_TOKEN` set, it serves a small read-only JSON API under `/api/` on its own port: the phones, totals per day, nights, workouts, screen time, one metric as a series, a trend, what each phone has stored, and when each kind of data last arrived. It's meant for a script, a home automation, a spreadsheet or a widget of your own. The [MCP server](mcp.md) uses the same queries for a model.

## Contents

- [Switch it on](#switch-it-on)
- [How it reads](#how-it-reads)
- [Conventions](#conventions)
- [Endpoints](#endpoints): [`/api/phones`](#get-apiphones), [`/api/days`](#get-apidays), [`/api/sleep`](#get-apisleep), [`/api/workouts`](#get-apiworkouts), [`/api/screen-time`](#get-apiscreen-time), [`/api/series`](#get-apiseries), [`/api/trend`](#get-apitrend), [`/api/catalog`](#get-apicatalog), [`/api/sync`](#get-apisync)
- [Metrics](#metrics)
- [Errors](#errors)
- [Limits](#limits)
- [Security](#security)

## Switch it on

The API is off by default, and while it's off every path under `/api/` answers 404. Set a token in `.env` and restart the receiver:

```bash
echo "API_TOKEN=$(openssl rand -hex 32)" >> .env
docker compose up -d
```

A token shorter than 16 characters keeps the API off; the receiver says so in its log (`docker compose logs receiver`). Every request carries the token as a bearer token:

```bash
curl -H "Authorization: Bearer $API_TOKEN" http://localhost:8080/api/phones
```

With the Helm chart, `receiver.api.enabled: true` switches it on, and the token is `API_TOKEN` in the release's Secret; see [Kubernetes](kubernetes.md#the-api-and-the-mcp-server).

## How it reads

The API never uses the receiver's own database connection. It opens a connection of its own as `dash_ro`, a database user the receiver sets up at every start, right after it rebuilds the views:

- `dash_ro` can `SELECT` from the [`dash` views](views.md) and nothing else: not the `records` and `buckets` tables, not the payload ledger, and it can't create anything.
- Its sessions are read-only (`default_transaction_read_only`), set on the role and again by the API on every connection. A write fails on the read-only setting first and on the missing grants after that.
- A query stops after 15 seconds.

Its password is `DASH_DB_PASSWORD` in `.env`. The MCP server uses the same user. On CloudNativePG, the cluster's managed roles create `dash_ro` with its password instead, and the receiver only grants it its reads; see [Kubernetes](kubernetes.md#cnpg).

## Conventions

- **JSON** in and out, `GET` only. Responses carry `Cache-Control: no-store`.
- **Days** are local days in the stack's `TZ`, the same as in the dashboards: a night belongs to the date you woke up, and screen time uses the app's day (04:00 to 04:00 by default). Every response names the `timezone`.
- **Dates** are `YYYY-MM-DD`. `from` and `to` are both inclusive. Without `to` the range ends today; without `from` it covers the 30 days up to `to`.
- **Timestamps** are ISO 8601 in the stack's time zone, with their offset: `2026-10-15T06:51:00+02:00`.
- **`phone`** is the label from the webhook URL (`/webhook/<label>`). Without it, every phone is in the answer, one row per phone.
- **Missing data is `null`** or no row, never 0, as in the views.
- **`complete`** is false for today: a running total.
- Numbers are rounded to two decimals.

## Endpoints

### `GET /api/phones`

Every phone that has sent data, with the platforms it sent from, the screen time device, the app version, when it last sent data, and its first and last day with data.

```bash
curl -H "Authorization: Bearer $API_TOKEN" http://localhost:8080/api/phones
```

```json
{
  "timezone": "Europe/Amsterdam",
  "today": "2026-10-16",
  "phones": [
    {"phone": "demo-android", "platforms": ["Android", "Screen time"], "device": "Samsung SM-S918B",
     "app_version": "1.23.0", "last_received": "2026-10-16T16:26:22.434367+02:00", "hours_since": 0.01,
     "first_day": "2026-08-12", "last_day": "2026-10-16"},
    {"phone": "demo-iphone", "platforms": ["iPhone"], "device": null,
     "app_version": "1.6.0", "last_received": "2026-10-16T16:26:23.606810+02:00", "hours_since": 0.01,
     "first_day": "2026-08-18", "last_day": "2026-10-16"}
  ]
}
```

### `GET /api/days`

Parameters: `from`, `to`, `phone`. One row per phone and day, joined from several views:

| Field | Unit | From |
|---|---|---|
| `steps`, `distance_km`, `active_kcal`, `total_kcal` | steps, km, kcal | `dash.activity_days`: the phone's daily totals, counted once across phone and watch |
| `sleep_h`, `in_bed_h` | hours | `dash.sleep_main`: the night that ended that morning |
| `exercise_min`, `workouts` | minutes, count | `dash.workouts`, summed per day |
| `screen_min` | minutes | `dash.screen_days`, all apps (Android only) |
| `resting_hr` | bpm | `dash.measurement_days`, resting heart rate as the watch reports it |
| `weight_kg` | kg | `dash.weight_days`, the first weighing of the day |

```bash
curl -H "Authorization: Bearer $API_TOKEN" \
  "http://localhost:8080/api/days?from=2026-10-14&to=2026-10-15&phone=demo-android"
```

```json
{
  "timezone": "Europe/Amsterdam", "from": "2026-10-14", "to": "2026-10-15", "phone": "demo-android",
  "rows": [
    {"day": "2026-10-14", "phone": "demo-android", "steps": 8636, "distance_km": 6.39, "active_kcal": 409.2,
     "total_kcal": 2099.2, "sleep_h": 6.74, "in_bed_h": 6.74, "exercise_min": null, "workouts": null,
     "screen_min": 232, "resting_hr": 58.0, "weight_kg": 77.8, "complete": true},
    {"day": "2026-10-15", "phone": "demo-android", "steps": 9804, "distance_km": 7.25, "active_kcal": 469.9,
     "total_kcal": 2159.9, "sleep_h": 6.42, "in_bed_h": 6.55, "exercise_min": 58.0, "workouts": 1,
     "screen_min": 204, "resting_hr": 53.0, "weight_kg": null, "complete": true}
  ],
  "truncated": false
}
```

A day shows up when any of these views has a row for it. `exercise_min` is `null` on a day without a workout.

### `GET /api/sleep`

Parameters: `from`, `to`, `phone`, as wake dates. One night per phone from `dash.sleep_nights`: `bedtime` and `wake_time`, hours `asleep_h`, `in_bed_h`, `deep_h`, `light_h`, `rem_h` and `awake_h` (the stages are `null` for a night without them), `has_stages`, `hr_low` (the lowest 10-minute average heart rate in the night, not the resting heart rate), `hr_avg`, `source_app` and `complete`.

```bash
curl -H "Authorization: Bearer $API_TOKEN" \
  "http://localhost:8080/api/sleep?from=2026-10-15&to=2026-10-15&phone=demo-iphone"
```

```json
{
  "timezone": "Europe/Amsterdam", "from": "2026-10-15", "to": "2026-10-15", "phone": "demo-iphone",
  "rows": [
    {"day": "2026-10-15", "phone": "demo-iphone", "bedtime": "2026-10-15T00:08:14+02:00",
     "wake_time": "2026-10-15T06:51:00+02:00", "asleep_h": 6.51, "in_bed_h": 6.71, "deep_h": 1.62,
     "light_h": 4.06, "rem_h": 0.83, "awake_h": 0.2, "has_stages": true, "hr_low": 47.0, "hr_avg": 52.47,
     "source_app": "Apple Watch", "complete": true}
  ],
  "truncated": false
}
```

### `GET /api/workouts`

Parameters: `from`, `to`, `phone`. Every workout from `dash.workouts`, in order of its start: `day`, `start_time`, `end_time`, `activity`, `minutes` and `source_app`. A workout that a watch and a phone app both recorded is listed once.

```bash
curl -H "Authorization: Bearer $API_TOKEN" "http://localhost:8080/api/workouts?from=2026-10-15&to=2026-10-15"
```

```json
{
  "timezone": "Europe/Amsterdam", "from": "2026-10-15", "to": "2026-10-15", "phone": null,
  "rows": [
    {"day": "2026-10-15", "phone": "demo-iphone", "start_time": "2026-10-15T18:05:00+02:00",
     "end_time": "2026-10-15T18:57:00+02:00", "activity": "cycling", "minutes": 52.0, "source_app": "Apple Watch"},
    {"day": "2026-10-15", "phone": "demo-android", "start_time": "2026-10-15T18:17:00+02:00",
     "end_time": "2026-10-15T19:15:00+02:00", "activity": "biking", "minutes": 58.0,
     "source_app": "com.garmin.android.apps.connectmobile"}
  ],
  "truncated": false
}
```

### `GET /api/screen-time`

Parameters: `date` (default today), `phone`. Screen time on one day, one entry per phone and device, from `dash.screen_days` and `dash.screen_apps`: `total_min`, `filtered_min` and `app_filter` (when the phone filters apps), the number of `apps`, the `last_use`, and the 50 apps with the most minutes in `top_apps`. Screen time comes from Android phones only, so an iPhone has no entry.

```bash
curl -H "Authorization: Bearer $API_TOKEN" \
  "http://localhost:8080/api/screen-time?date=2026-10-15&phone=demo-android"
```

```json
{
  "timezone": "Europe/Amsterdam", "date": "2026-10-15", "phone": "demo-android",
  "phones": [
    {"phone": "demo-android", "device": "Samsung SM-S918B", "total_min": 204, "filtered_min": null,
     "app_filter": null, "apps": 7, "last_use": "2026-10-15T23:24:17+02:00", "complete": true,
     "top_apps": [
       {"app": "Reddit", "package": "com.reddit.frontpage", "minutes": 57, "last_used": "2026-10-15T15:52:36+02:00"},
       {"app": "WhatsApp", "package": "com.whatsapp", "minutes": 57, "last_used": "2026-10-15T23:24:17+02:00"},
       {"app": "Instagram", "package": "com.instagram.android", "minutes": 52, "last_used": "2026-10-15T21:54:37+02:00"}
     ]}
  ]
}
```

(The example shows three of the seven apps.)

### `GET /api/series`

Parameters: `metric` (required, see [Metrics](#metrics)), `from`, `to`, `phone`. One value per phone and day. Days without a value are left out.

```bash
curl -H "Authorization: Bearer $API_TOKEN" \
  "http://localhost:8080/api/series?metric=weight&from=2026-10-09&to=2026-10-15&phone=demo-android"
```

```json
{
  "timezone": "Europe/Amsterdam", "from": "2026-10-09", "to": "2026-10-15", "phone": "demo-android",
  "metric": "weight", "unit": "kg",
  "rows": [
    {"day": "2026-10-09", "phone": "demo-android", "value": 77.9},
    {"day": "2026-10-11", "phone": "demo-android", "value": 77.6},
    {"day": "2026-10-12", "phone": "demo-android", "value": 77.8},
    {"day": "2026-10-14", "phone": "demo-android", "value": 77.8}
  ],
  "truncated": false
}
```

### `GET /api/trend`

Parameters: `metric` (required), `days` (7 to 365, default 28), `phone`. The last `days` complete days, up to yesterday, against the same number of days before them. Per phone: the `period` with its mean, lowest and highest day and the days with data, the `previous` period's mean, and the `change` of the mean, absolute and in percent (`change_pct`). Today is a running total and stays out.

The mean is over the days with data, except for `exercise_min`: a day without a workout counts as 0 there, and the period also has a `total`.

```bash
curl -H "Authorization: Bearer $API_TOKEN" \
  "http://localhost:8080/api/trend?metric=sleep_h&days=28&phone=demo-iphone"
```

```json
{
  "timezone": "Europe/Amsterdam", "metric": "sleep_h", "phone": "demo-iphone",
  "phones": [
    {"phone": "demo-iphone", "metric": "sleep_h", "unit": "h",
     "period": {"from": "2026-09-18", "to": "2026-10-15", "days_with_data": 28, "mean": 7.01,
                "min": 5.91, "min_day": "2026-09-23", "max": 8.54, "max_day": "2026-10-03"},
     "previous": {"from": "2026-08-21", "to": "2026-09-17", "days_with_data": 28, "mean": 7.15},
     "change": -0.14, "change_pct": -1.96}
  ]
}
```

### `GET /api/catalog`

Parameters: `phone`. What each phone has stored, from [`dash.data_types`](views.md#dashdata_types), and what the other endpoints can answer from it. Per phone:

- `platforms`, as in `/api/phones`.
- `types`: one entry per type of data (the payload key) and payload source, with its `records`, its `windows` (series the phone sends per time window), the apps that wrote it (`sources`), its `first_day` and `last_day`, and `read`: false when no endpoint reads it. The dashboards still show it.
- `reads`: what has data. `days` lists the fields of `/api/days` that have a source; `sleep`, `workouts` and `screen_time` are the first and last day of `/api/sleep`, `/api/workouts` and `/api/screen-time`, or `null`; `metrics` gives every [metric](#metrics) with data and its first and last day.

A metric has data when the payload key it reads is stored as records. Windows count only for heart rate, the one series the views read per window. HRV counts only from the platform that measures it that way: `hrv_rmssd` from Android, `hrv_sdnn` from an iPhone. A metric that reads two keys (`sleep_hr_low`, sleep and heart rate) has the days both cover.

```bash
curl -H "Authorization: Bearer $API_TOKEN" "http://localhost:8080/api/catalog?phone=demo-android"
```

```json
{
  "timezone": "Europe/Amsterdam", "today": "2026-10-17", "phone": "demo-android",
  "phones": [
    {"phone": "demo-android", "platforms": ["Android", "Screen time"],
     "types": [
       {"type": "daily_totals", "payload_source": "health_connect", "records": 59, "windows": 0, "sources": [],
        "first_day": "2026-08-19", "last_day": "2026-10-16", "read": true},
       {"type": "heart_rate", "payload_source": "health_connect", "records": 17009, "windows": 0,
        "sources": ["com.garmin.android.apps.connectmobile"], "first_day": "2026-08-19", "last_day": "2026-10-17",
        "read": true},
       {"type": "skin_temperature", "payload_source": "health_connect", "records": 236, "windows": 0,
        "sources": ["com.garmin.android.apps.connectmobile"], "first_day": "2026-08-19", "last_day": "2026-10-16",
        "read": false}
     ],
     "reads": {
       "days": ["steps", "distance_km", "active_kcal", "total_kcal", "sleep_h", "in_bed_h", "exercise_min",
                "workouts", "screen_min", "resting_hr", "weight_kg"],
       "sleep": ["2026-08-19", "2026-10-16"], "workouts": ["2026-08-20", "2026-10-15"],
       "screen_time": ["2026-08-13", "2026-10-17"],
       "metrics": {"steps": ["2026-08-19", "2026-10-16"], "hrv_rmssd": ["2026-08-19", "2026-10-16"],
                   "vo2_max": ["2026-08-23", "2026-10-11"], "screen_min": ["2026-08-13", "2026-10-17"]}
     }}
  ],
  "truncated": false
}
```

(The example shows three of the 15 types and four of the 21 metrics.)

### `GET /api/sync`

Parameters: `phone`. When each phone last sent data and when each type of data last arrived, from `dash.installs`, [`dash.data_types`](views.md#dashdata_types) and [`dash.data_sources`](views.md#dashdata_sources). Per phone:

- `last_received` and `hours_since`: the newest payload with data, from any platform.
- `payload_sources`: per platform (`Android`, `iPhone`, `Screen time`) the `app_version`, `last_received`, `hours_since`, `payloads_24h` and `last_sequence`, as in `dash.installs`.
- `types`: per type, `last_time` (the newest record), `last_day`, `last_received` (when the receiver last stored any of it), `days_since`, `active_days`, `usual_gap_days` and `quiet`, as in `dash.data_types`.
- `sources`: per type and writing app (`source_app`), its `records`, `windows`, `last_time`, `last_received`, `days_since` and `quiet`.
- `quiet`: the types that went quiet.

A type is quiet when its newest record is more than twice its usual gap old: the usual gap is 28 days (fewer for a type that started later) divided by the days with data in the 28 days up to its newest record. A daily type is quiet after 2 days without a record, a weekly one after 14; `quiet` is `null` for a type with data on one day only. An app is quiet by the same rule, when it wrote the type in those 28 days: a watch that stopped sending heart rate while the phone still does.

```bash
curl -H "Authorization: Bearer $API_TOKEN" "http://localhost:8080/api/sync?phone=demo-android"
```

```json
{
  "timezone": "Europe/Amsterdam", "today": "2026-10-17", "phone": "demo-android",
  "phones": [
    {"phone": "demo-android", "last_received": "2026-10-17T01:23:20.402783+02:00", "hours_since": 0.01,
     "payload_sources": [
       {"payload_source": "health_connect", "platform": "Android", "app_version": "1.23.0",
        "last_received": "2026-10-17T01:23:19.305105+02:00", "hours_since": 0.01, "payloads_24h": 22,
        "last_sequence": 41022},
       {"payload_source": "screen_time", "platform": "Screen time", "app_version": "1.23.0",
        "last_received": "2026-10-17T01:23:20.402783+02:00", "hours_since": 0.01, "payloads_24h": 60,
        "last_sequence": 41082}
     ],
     "quiet": [],
     "types": [
       {"type": "vo2_max", "payload_source": "health_connect", "last_day": "2026-10-11",
        "last_time": "2026-10-11T10:00:00+02:00", "last_received": "2026-10-17T01:23:19.132775+02:00",
        "days_since": 5.64, "active_days": 4, "usual_gap_days": 7.0, "quiet": false},
       {"type": "weight", "payload_source": "health_connect", "last_day": "2026-10-16",
        "last_time": "2026-10-16T07:55:00+02:00", "last_received": "2026-10-17T01:23:19.253695+02:00",
        "days_since": 0.73, "active_days": 16, "usual_gap_days": 1.75, "quiet": false}
     ],
     "sources": [
       {"type": "steps", "source_app": "com.garmin.android.apps.connectmobile", "records": 944, "windows": 0,
        "last_time": "2026-10-16T23:00:00+02:00", "last_received": "2026-10-17T01:23:19.253695+02:00",
        "days_since": 0.1, "quiet": false},
       {"type": "steps", "source_app": "com.google.android.apps.fitness", "records": 118, "windows": 0,
        "last_time": "2026-10-16T18:00:00+02:00", "last_received": "2026-10-17T01:23:19.253695+02:00",
        "days_since": 0.31, "quiet": false}
     ]}
  ],
  "truncated": false
}
```

(The example shows two of the 15 types and two of the 16 pairs of type and app.)

## Metrics

`/api/series` and `/api/trend` take one of these. Each is one value per phone and local day.

| `metric` | Unit | What it is | View |
|---|---|---|---|
| `steps` | steps | Steps, counted once across phone and watch | `activity_days` |
| `distance_km` | km | Distance | `activity_days` |
| `active_kcal` | kcal | Active energy | `activity_days` |
| `total_kcal` | kcal | Total energy | `activity_days` |
| `exercise_min` | min | Workout minutes | `workouts` |
| `sleep_h` | h | Hours asleep, on the wake date | `sleep_main` |
| `in_bed_h` | h | Hours in bed | `sleep_main` |
| `deep_sleep_h` | h | Deep sleep, nights with stages | `sleep_main` |
| `rem_sleep_h` | h | REM sleep, nights with stages | `sleep_main` |
| `sleep_hr_low` | bpm | Lowest 10-minute average heart rate in the night | `sleep_nights` |
| `heart_rate` | bpm | Mean heart rate of the day | `heart_days` |
| `resting_heart_rate` | bpm | Resting heart rate as the watch reports it | `measurement_days` |
| `hrv_rmssd` | ms | HRV as RMSSD (Android) | `measurement_days` |
| `hrv_sdnn` | ms | HRV as SDNN (iPhone) | `measurement_days` |
| `oxygen_saturation` | % | SpO2 | `measurement_days` |
| `respiratory_rate` | /min | Breathing rate | `measurement_days` |
| `vo2_max` | ml/kg/min | VO2 max | `measurement_days` |
| `weight` | kg | First weighing of the day | `weight_days` |
| `body_fat` | % | Body fat | `measurement_days` |
| `systolic` | mmHg | Blood pressure, systolic | `measurement_days` |
| `diastolic` | mmHg | Blood pressure, diastolic | `measurement_days` |
| `blood_glucose` | mmol/L | Blood glucose | `measurement_days` |
| `screen_min` | min | Screen time, all apps (Android only) | `screen_days` |

The measurements are the mean of the day's readings. RMSSD and SDNN are different measures of heart rate variability, so they're two metrics and never averaged together.

## Errors

Errors are JSON with one `error` field.

| Status | When |
|---|---|
| 400 | A date that isn't `YYYY-MM-DD` or doesn't exist, `from` after `to`, a range over 400 days, an unknown `metric`, a `days` outside 7 to 365, a `phone` that isn't a valid label, an unknown or repeated parameter |
| 401 | No `Authorization: Bearer` header, or the wrong token. Comes with `WWW-Authenticate: Bearer` and is checked before anything else |
| 404 | The API is off, the path isn't one of the endpoints, or the `phone` has never sent data |
| 503 | The receiver is still starting, the database can't be reached, or a query ran over 15 seconds |

```json
{"error": "the range is at most 400 days"}
```

## Limits

- A range is at most 400 days.
- A response has at most 5,000 rows. When there were more, `truncated` is `true`: ask for a shorter range or one phone. In `/api/catalog` and `/api/sync` the rows are the types, and the writing apps.
- `/api/screen-time` lists the top 50 apps per phone.
- A query stops after 15 seconds, set on the `dash_ro` role and on the connection.

## Security

- The API reads everything the dashboards show, so treat `API_TOKEN` like the Grafana admin password. Use a long random value and keep it out of URLs: the API only reads it from the `Authorization` header, compares it in constant time and never logs it.
- The API is on the receiver's port. If a reverse proxy makes the receiver reachable from the internet for the phones, the API is reachable there too, behind its token. Forward only `/webhook` in the proxy when you don't need the API from outside, or keep `API_TOKEN` empty.
- Over plain HTTP the token and the answers cross the network readable. Outside your own network, put TLS in front, as for the webhook.
- Leave `API_TOKEN` empty and the API is gone: no endpoint, no database connection as `dash_ro`. The `dash_ro` user still exists, for the MCP server.
