# Views for your own queries

The dashboards read nothing but the views in the `dash` schema. You can query them too, from Grafana's **Explore**, from `psql`, or from a panel of your own. This page lists every view and column, the unit of each value, and the rules behind them.

## Contents

- [How it fits together](#how-it-fits-together)
- [Time and days](#time-and-days)
- [Helper functions](#helper-functions)
- [Installs](#installs)
- [Activity](#activity)
- [Heart rate](#heart-rate)
- [Measurements](#measurements)
- [Sleep](#sleep)
- [Workouts](#workouts)
- [Screen time](#screen-time)
- [Data and sync](#data-and-sync)
- [Storage underneath](#storage-underneath)

## How it fits together

The receiver stores every record of every payload in one table, `records`, with the record's JSON in `data`. On every start it drops the `dash` schema and builds it again from `receiver/views.sql`, so a change to that file takes effect on the next start. That also means anything you create in `dash` is gone after a restart. Put views of your own in the `public` schema, and have them read `records`: a view in `public` that reads a `dash` view is dropped with it on the next start.

Grafana connects as `grafana_ro`, a database user that can read the `dash` views, `records` and `buckets` and nothing else. Its password is `GRAFANA_DB_PASSWORD` from `.env`.

A few conventions hold for every view:

- `time` is a `timestamptz`, ready for Grafana's time filter (`WHERE $__timeFilter(time)`).
- Every view has an `install` column: the label from the webhook URL, `/webhook/<label>`. Payloads sent to plain `/webhook`, and everything stored before labels existed, have `default`.
- Missing data is no row, or NULL. A view never turns "nothing was measured" into 0.
- A value from the payload is only used when it is a JSON number. Anything else becomes NULL, so a malformed value never breaks a view.
- Types: text unless the table says otherwise. Numbers with a fraction are `double precision`.

## Time and days

`TZ` in `.env` sets the time zone every day, night and hour is counted in. The receiver checks it against Postgres's list of zones when it starts, and refuses to start with a clear message when it does not know it.

- Day views have `day` (a `date`), `time` set to local midnight of that day, and `complete`, which is true for every day before today. Today is a running total: leave it out of averages and baselines, and label it "so far".
- Records over a period (steps, workouts) count on the local day they started. Records at one moment count on the local day of that moment.
- Daily totals use the `date` the phone sent, which is the phone's own local day.
- A night of sleep belongs to the local date you woke up on.
- Screen time uses the app's own day, which runs from 04:00 to 04:00 by default (configurable on the phone under **Day Boundary**), so use after midnight counts toward the day before.

Avoid `current_date`, `$__timeGroup` for days, and the session time zone: they use the database's UTC, not `TZ`. Use the helper functions below instead.

## Helper functions

| Function | Returns | What it does |
|---|---|---|
| `dash.tz()` | text | The configured time zone, such as `Europe/Amsterdam` |
| `dash.local_day(timestamptz)` | date | The local date of a moment |
| `dash.day_start(date)` | timestamptz | Local midnight at the start of a date (a day around a clock change is 23 or 25 hours long) |
| `dash.hour_start(timestamptz)` | timestamptz | The start of the local hour of a moment |
| `dash.today()` | date | Today's local date |
| `dash.days(from, to)` | table of `day` date, `time` timestamptz | Every local date from `from` to `to`, for a calendar without gaps: `SELECT * FROM dash.days($__timeFrom(), $__timeTo())` |
| `dash.num(jsonb)` | double precision | A JSON number, or NULL for anything else |
| `dash.ts(text)` | timestamptz | An ISO 8601 timestamp, or NULL when the text is not one |

## Installs

### `dash.installs`

One row per install and payload source, from the payload ledger and from `records`, so installs that sent data before the ledger existed are listed too. Test pings do not count as data.

| Column | Type | Meaning |
|---|---|---|
| `install` | text | The label from the webhook URL |
| `payload_source` | text | `health_connect`, `healthkit_ios` or `screen_time` |
| `platform` | text | `Android`, `iPhone` or `Screen time` |
| `device` | text | The phone model, from screen time payloads; NULL for health payloads |
| `last_received` | timestamptz | When the receiver last stored a payload with data |
| `last_built` | timestamptz | When the phone built the newest payload (its `timestamp`) |
| `last_sequence` | bigint | `sequence` of the payload received last; NULL for iPhone payloads from app versions without one |
| `app_version` | text | App version of the payload received last |
| `payloads_24h` | integer | Payloads with data in the last 24 hours |
| `hours_since` | double precision | Hours since `last_received` |

## Activity

### `dash.activity_days`

Steps, distance and calories per day, from the `daily_totals` the phone computes. Health Connect and Apple Health count each stretch of time once even when a phone and a watch both record it, so this is the figure to use for steps per day. One row per install and day: when two payload sources wrote the same day, the newest build time wins.

| Column | Type | Meaning |
|---|---|---|
| `time` | timestamptz | Local midnight of `day` |
| `day` | date | The phone's date for the totals |
| `install` | text | |
| `payload_source` | text | `health_connect` or `healthkit_ios` |
| `steps` | bigint | Steps |
| `distance_m` | double precision | Meters |
| `active_kcal` | double precision | Active energy, kcal |
| `total_kcal` | double precision | Total energy, kcal |
| `complete` | boolean | `day` is before today |

### `dash.steps_hourly`

Steps per local hour from the raw step records: first the sum per writing app, then the largest of those. That avoids the double count of a phone and a watch, but it is an approximation and does not add up to `activity_days`.

| Column | Type | Meaning |
|---|---|---|
| `time` | timestamptz | Start of the local hour |
| `day` | date | Local date of that hour |
| `hour` | integer | Local hour, 0 through 23 |
| `install` | text | |
| `steps` | bigint | Steps of the app that counted the most in that hour |
| `sources` | integer | How many apps wrote steps in that hour |

### `dash.steps_raw_days`

The raw sum of step records per writing app and day. Adding these up across apps counts a walk twice when two apps recorded it; this view exists to show exactly that next to `activity_days`.

| Column | Type | Meaning |
|---|---|---|
| `time` | timestamptz | Local midnight of `day` |
| `day` | date | Local date the step records started on |
| `install` | text | |
| `source_app` | text | The app that wrote the records, such as `com.google.android.apps.fitness`; `''` when unknown |
| `steps` | bigint | Sum of its step records |
| `complete` | boolean | `day` is before today |

## Heart rate

### `dash.heart_rate`

Every heart rate sample, plus heart rate the Android app sent per time window (see `buckets` under [Storage underneath](#storage-underneath)). A window is one row at its start.

| Column | Type | Meaning |
|---|---|---|
| `time` | timestamptz | Moment of the sample, or start of the window |
| `install` | text | |
| `source_app` | text | The app that wrote it; for a window, the first of its sources |
| `bpm` | double precision | Beats per minute; for a window, its average |
| `bpm_min` | double precision | Same as `bpm` for a sample; the window's lowest value |
| `bpm_max` | double precision | Same as `bpm` for a sample; the window's highest value |
| `n` | integer | 1 for a sample; the window's sample count |

### `dash.heart_rate_hourly`

Heart rate per local hour. Use `bpm_min` and `bpm_max` for a band around `bpm`.

| Column | Type | Meaning |
|---|---|---|
| `time` | timestamptz | Start of the local hour |
| `install` | text | |
| `bpm` | double precision | Mean of the samples |
| `bpm_min` | double precision | Lowest sample |
| `bpm_max` | double precision | Highest sample |
| `n` | integer | Number of samples |

### `dash.heart_days`

Heart rate per local day.

| Column | Type | Meaning |
|---|---|---|
| `time` | timestamptz | Local midnight of `day` |
| `day` | date | |
| `install` | text | |
| `bpm` | double precision | Mean of the samples |
| `bpm_min` | double precision | Lowest sample |
| `bpm_max` | double precision | Highest sample |
| `n` | integer | Number of samples |
| `wear_h` | double precision | Hours with at least one sample: distinct minutes with a sample, divided by 60. A window counts for its whole length |
| `complete` | boolean | `day` is before today |

## Measurements

### `dash.measurements`

Every reading of the types below, one row per value. A blood pressure reading gives two rows.

| Column | Type | Meaning |
|---|---|---|
| `time` | timestamptz | Moment of the reading |
| `day` | date | Its local date |
| `install` | text | |
| `payload_source` | text | `health_connect` or `healthkit_ios` |
| `type` | text | The payload key, see the table below |
| `measure` | text | What the value is, see the table below |
| `source_app` | text | The app or device that wrote it |
| `value` | double precision | The value, in `unit` |
| `unit` | text | See the table below |

| `type` | `measure` | `unit` | From the field |
|---|---|---|---|
| `resting_heart_rate` | `resting_heart_rate` | bpm | `bpm` |
| `heart_rate_variability` | `rmssd` from Android, `sdnn` from iPhone | ms | `heart_rate_variability_millis` |
| `oxygen_saturation` | `oxygen_saturation` | % | `percentage` |
| `respiratory_rate` | `respiratory_rate` | /min | `rate` |
| `vo2_max` | `vo2_max` | ml/kg/min | `vo2_ml_per_min_per_kg` |
| `weight` | `weight` | kg | `kilograms` |
| `body_fat` | `body_fat` | % | `percentage` |
| `lean_body_mass`, `bone_mass`, `body_water_mass` | same as `type` | kg | `kilograms` |
| `blood_pressure` | `systolic` and `diastolic` | mmHg | `systolic`, `diastolic` |
| `blood_glucose` | `blood_glucose` | mmol/L | `mmol_per_liter` |
| `body_temperature`, `basal_body_temperature` | same as `type` | °C | `celsius` |
| `skin_temperature` | `delta` (Android only) | °C | `delta_celsius` |

Health Connect stores heart rate variability as RMSSD and Apple Health as SDNN. They are different numbers, so keep them apart by `measure`: never average them together. The iPhone's skin temperature is an absolute wrist temperature rather than a change against a baseline, so it is left out.

### `dash.measurement_days`

The readings of `measurements` per local day.

| Column | Type | Meaning |
|---|---|---|
| `time` | timestamptz | Local midnight of `day` |
| `day` | date | |
| `install` | text | |
| `payload_source` | text | |
| `type` | text | |
| `measure` | text | |
| `unit` | text | |
| `first` | double precision | The earliest reading of the day |
| `median` | double precision | Median of the day's readings |
| `avg` | double precision | Mean |
| `min` | double precision | Lowest |
| `max` | double precision | Highest |
| `n` | integer | Number of readings |
| `complete` | boolean | `day` is before today |

### `dash.weight_days`

Weight per day. A weighing that two apps wrote (the same moment and the same weight to 0.1 kg) counts once.

| Column | Type | Meaning |
|---|---|---|
| `time` | timestamptz | Local midnight of `day` |
| `day` | date | |
| `install` | text | |
| `kg` | double precision | The first reading of the day |
| `kg_7d` | double precision | Mean of all readings in the 7 days up to and including `day` (a calendar range, so days without a weighing add no readings) |
| `readings` | integer | Readings that day |
| `complete` | boolean | `day` is before today |

## Sleep

### `dash.sleep_nights`

One night per install and wake date, using the main session of that night.

- **Start and night:** a session starts at `session_end_time` minus `duration_seconds`, and belongs to the local date of `session_end_time`.
- **Which stages count:** a stage counts only when its own `source` (or, without one, the session's) is the session's source. iPhone nights mix Apple Watch stages with an iPhone in-bed sample, and only the Watch's stages count.
- **Stage names:** `light`, `deep` and `rem` stay as they are; `sleeping` becomes `asleep`; `awake`, `awake_in_bed` and `out_of_bed` become `awake`; `in_bed` and `unknown` are in no sum.
- **Main session:** of all sessions with the same wake date, the one with the most hours asleep, then the longest in bed. Two apps that both wrote the night therefore count once.

| Column | Type | Meaning |
|---|---|---|
| `time` | timestamptz | Local midnight of `day` |
| `day` | date | Local wake date |
| `install` | text | |
| `payload_source` | text | |
| `source_app` | text | The app that wrote the session |
| `start_time` | timestamptz | When the session started |
| `end_time` | timestamptz | When it ended |
| `in_bed_h` | double precision | `duration_seconds` in hours |
| `asleep_h` | double precision | Light, deep, REM and asleep stages in hours; without stages, `in_bed_h` |
| `deep_h` | double precision | Deep sleep, hours; NULL without stages |
| `light_h` | double precision | Light sleep, hours; NULL without stages |
| `rem_h` | double precision | REM sleep, hours; NULL without stages |
| `awake_h` | double precision | Awake during the night, hours; NULL without stages |
| `has_stages` | boolean | At least one stage counted |
| `bed_min` | integer | Start of the session in minutes after 18:00 local on the evening before `day`: 23:30 is 330 |
| `wake_min` | integer | End of the session, counted the same way: 07:00 is 780 |
| `hr_low` | double precision | The lowest 10-minute average heart rate inside the session; NULL without samples. This is an overnight low, not the resting heart rate a watch reports |
| `hr_avg` | double precision | Mean heart rate inside the session; NULL without samples |
| `complete` | boolean | `day` is before today |

### `dash.sleep_stages`

The stages of each night's main session, for a hypnogram. The same source rule applies; `in_bed` and `unknown` are left out.

| Column | Type | Meaning |
|---|---|---|
| `time` | timestamptz | Start of the stage |
| `day` | date | Local wake date of the night |
| `install` | text | |
| `stage` | text | `deep`, `light`, `rem`, `asleep` or `awake` |
| `start_time` | timestamptz | Start of the stage |
| `end_time` | timestamptz | End of the stage |
| `minutes` | double precision | Length in minutes |

### `dash.sleep_sessions`

Every sleep session with its stage sums, before the main session is picked. Same rules and units as `sleep_nights`.

| Column | Type | Meaning |
|---|---|---|
| `id` | bigint | The row in `records` |
| `install` | text | |
| `payload_source` | text | |
| `source_app` | text | |
| `start_time` | timestamptz | |
| `end_time` | timestamptz | |
| `day` | date | Local wake date |
| `in_bed_h` | double precision | |
| `asleep_h` | double precision | |
| `deep_h` | double precision | |
| `light_h` | double precision | |
| `rem_h` | double precision | |
| `awake_h` | double precision | |
| `has_stages` | boolean | |

### `dash.sleep_main`

The main session of each night: the rows of `sleep_sessions` that `sleep_nights` uses, with the same columns.

| Column | Type | Meaning |
|---|---|---|
| `id` | bigint | |
| `install` | text | |
| `payload_source` | text | |
| `source_app` | text | |
| `start_time` | timestamptz | |
| `end_time` | timestamptz | |
| `day` | date | |
| `in_bed_h` | double precision | |
| `asleep_h` | double precision | |
| `deep_h` | double precision | |
| `light_h` | double precision | |
| `rem_h` | double precision | |
| `awake_h` | double precision | |
| `has_stages` | boolean | |

## Workouts

### `dash.workouts`

Every exercise session. A watch and a phone app often both record the same workout: when two sources overlap for more than half of the shorter session, only the longer one is listed.

| Column | Type | Meaning |
|---|---|---|
| `time` | timestamptz | Start |
| `end_time` | timestamptz | End |
| `day` | date | Local date of the start |
| `install` | text | |
| `payload_source` | text | |
| `source_app` | text | |
| `type_code` | text | `type` as sent: a Health Connect number such as `56`, or an iPhone name such as `running` |
| `activity` | text | The Health Connect number turned into a name through `exercise_types`; iPhone names stay as they are; an unknown number becomes `other` |
| `minutes` | double precision | `duration_seconds` in minutes, pauses included |

### `dash.exercise_types`

Health Connect's exercise type numbers and their names, from `ExerciseSessionRecord`. Number 0 (other workout) is `other`.

| Column | Type | Meaning |
|---|---|---|
| `code` | text | The number, as text: `56` |
| `activity` | text | Its name: `running` |

## Screen time

Screen time comes from Android phones only. Apps filtered out on the phone, and apps used for one minute or less that day, are not in the data.

### `dash.screen_days`

One row per install, phone and day. Every screen time payload sends the last seven days again; for each day the payload with the highest `sequence` wins, so a week that arrives late still fills the days no newer payload covers.

| Column | Type | Meaning |
|---|---|---|
| `time` | timestamptz | Local midnight of `day` |
| `day` | date | The app's day, 04:00 to 04:00 by default |
| `install` | text | |
| `device` | text | Phone manufacturer and model |
| `total_min` | integer | Screen time of all apps, minutes, filtered ones included |
| `filtered_min` | integer | Minutes of the apps that were sent; NULL without an app filter |
| `app_filter` | text | `blocklist` or `allowlist` when an app filter was on, else NULL |
| `apps` | integer | Apps in the payload for that day |
| `last_use` | timestamptz | The latest `last_used` of any app that day |
| `complete` | boolean | `day` is before today |

### `dash.screen_apps`

One row per app per day.

| Column | Type | Meaning |
|---|---|---|
| `time` | timestamptz | Local midnight of `day` |
| `day` | date | The app's day |
| `install` | text | |
| `device` | text | |
| `package` | text | Package name, such as `com.whatsapp` |
| `app` | text | The app's name, else the package name |
| `minutes` | integer | Foreground minutes |
| `last_used` | timestamptz | When the app was last in the foreground that day |

## Data and sync

### `dash.type_summary`

What is stored, per install, payload source, type and writing app.

| Column | Type | Meaning |
|---|---|---|
| `install` | text | |
| `payload_source` | text | |
| `type` | text | The payload key, such as `heart_rate` |
| `source_app` | text | |
| `records` | bigint | Stored rows |
| `first_time` | timestamptz | Earliest record time |
| `last_time` | timestamptz | Latest record time |
| `last_received` | timestamptz | When the latest of them was stored |

### `dash.records_days`

Stored records per type and local day. Day records count on their own date.

| Column | Type | Meaning |
|---|---|---|
| `time` | timestamptz | Local midnight of `day` |
| `day` | date | |
| `install` | text | |
| `type` | text | |
| `records` | integer | Rows |
| `complete` | boolean | `day` is before today |

## Storage underneath

You rarely need these, but they explain the views.

**`records`** holds one row per record. `grafana_ro` can read it.

| Column | Meaning |
|---|---|
| `type` | The payload key the record arrived under |
| `ts` | `time`, else `end_time`, else `session_end_time`; midnight UTC of `date` for day records |
| `uuid` | The record's ID. Day records use `<type>:<install>:<payload source>:<device>:<date>` |
| `record_id` | `uuid` up to the first `#`: heart rate and skin temperature samples share their record's ID |
| `source_app` | The record's `source` |
| `payload_source` | The payload's `source` |
| `install` | The label from the webhook URL |
| `seq`, `built_at` | The payload's `sequence` and `timestamp` |
| `day` | The `date` of a day record (`daily_totals`, `screen_time`) |
| `device` | The phone model of a screen time record |
| `start_ts` | `start_time`, or `session_end_time` minus `duration_seconds`, else `ts` |
| `received_at` | When the receiver last wrote the row |
| `data` | The record as sent. Screen time records with an app filter also carry `_app_filter` |

**`payloads`** is the ledger: one row per payload with its body, kind (`sync`, `backfill`, `deletion`, `screen_time` or `ping`), sequence and a SHA-256 of the body. A body that arrives a second time is answered as a duplicate and not applied again. `grafana_ro` cannot read it: the bodies hold everything a phone ever sent.

**Ordering.** A payload that arrives late cannot undo a newer one. A write or a deletion is skipped only when it is older than the stored row on `sequence` (or has none) and also older on build time. A reinstalled app starts its `sequence` again at 1 but has a newer build time, so it still wins.

**Deletions.** `deleted_records` in a payload removes the named records of that type, before the records of the same payload are stored. A heart rate or skin temperature deletion names the record, and removes all its samples.

**`buckets`** holds series the Android app sends per time window instead of per record (objects with `bucket_start`, sent when **Data Resolution** is set for a type): one row per install, type, payload source and window start, with `bucket_end`, `sample_count`, `avg`, `min`, `max`, `total`, `complete` and `sources`. A window marked `complete` replaces the stored one, unless it is older by the ordering rule; any other window is combined with it (counts and totals added, the average weighted by sample count). `grafana_ro` can read it. Only the heart rate views (`heart_rate`, `heart_rate_hourly`, `heart_days`) read it so far; windows of other types are stored but not in any view yet.
