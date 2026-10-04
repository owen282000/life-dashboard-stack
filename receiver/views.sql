-- The dash schema: helper functions and the views the dashboards read.
--
-- The receiver drops and rebuilds this schema on every start, so a change here takes
-- effect on the next start. Put views of your own in the public schema. docs/views.md
-- describes every view and column.
--
-- __TZ__ is replaced by the receiver with the TZ setting (validated, quoted).

DROP SCHEMA IF EXISTS dash CASCADE;
CREATE SCHEMA dash;
COMMENT ON SCHEMA dash IS 'Life Dashboard views, rebuilt by the receiver on every start';

-- ---------------------------------------------------------------- helpers

CREATE FUNCTION dash.tz() RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT __TZ__::text $$;

CREATE FUNCTION dash.local_day(t timestamptz) RETURNS date
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT (t AT TIME ZONE dash.tz())::date $$;

CREATE FUNCTION dash.day_start(d date) RETURNS timestamptz
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT d::timestamp AT TIME ZONE dash.tz() $$;

CREATE FUNCTION dash.hour_start(t timestamptz) RETURNS timestamptz
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT date_trunc('hour', t AT TIME ZONE dash.tz()) AT TIME ZONE dash.tz()
$$;

CREATE FUNCTION dash.today() RETURNS date
LANGUAGE sql STABLE PARALLEL SAFE AS $$ SELECT dash.local_day(now()) $$;

CREATE FUNCTION dash.days(t_from timestamptz, t_to timestamptz)
RETURNS TABLE (day date, "time" timestamptz)
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT g::date, dash.day_start(g::date)
    FROM generate_series(dash.local_day(t_from)::timestamp,
                         dash.local_day(t_to)::timestamp,
                         interval '1 day') AS g
$$;

-- A JSON number as double precision; anything else (a string, null, an object) is NULL.
CREATE FUNCTION dash.num(value jsonb) RETURNS double precision
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE WHEN jsonb_typeof(value) = 'number' THEN value::double precision END
$$;

-- An ISO 8601 timestamp from the payload, or NULL when it does not look like one.
CREATE FUNCTION dash.ts(value text) RETURNS timestamptz
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT CASE WHEN value ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}(:?\d{2})?)$'
                THEN value::timestamptz END
$$;

-- Health Connect exercise type constants (ExerciseSessionRecord.EXERCISE_TYPE_*).
CREATE VIEW dash.exercise_types (code, activity) AS
VALUES
    ('0', 'other'), ('2', 'badminton'), ('4', 'baseball'), ('5', 'basketball'),
    ('8', 'biking'), ('9', 'biking_stationary'), ('10', 'boot_camp'), ('11', 'boxing'),
    ('13', 'calisthenics'), ('14', 'cricket'), ('16', 'dancing'), ('25', 'elliptical'),
    ('26', 'exercise_class'), ('27', 'fencing'), ('28', 'football_american'),
    ('29', 'football_australian'), ('31', 'frisbee_disc'), ('32', 'golf'),
    ('33', 'guided_breathing'), ('34', 'gymnastics'), ('35', 'handball'),
    ('36', 'high_intensity_interval_training'), ('37', 'hiking'), ('38', 'ice_hockey'),
    ('39', 'ice_skating'), ('44', 'martial_arts'), ('46', 'paddling'), ('47', 'paragliding'),
    ('48', 'pilates'), ('50', 'racquetball'), ('51', 'rock_climbing'), ('52', 'roller_hockey'),
    ('53', 'rowing'), ('54', 'rowing_machine'), ('55', 'rugby'), ('56', 'running'),
    ('57', 'running_treadmill'), ('58', 'sailing'), ('59', 'scuba_diving'), ('60', 'skating'),
    ('61', 'skiing'), ('62', 'snowboarding'), ('63', 'snowshoeing'), ('64', 'soccer'),
    ('65', 'softball'), ('66', 'squash'), ('68', 'stair_climbing'),
    ('69', 'stair_climbing_machine'), ('70', 'strength_training'), ('71', 'stretching'),
    ('72', 'surfing'), ('73', 'swimming_open_water'), ('74', 'swimming_pool'),
    ('75', 'table_tennis'), ('76', 'tennis'), ('78', 'volleyball'), ('79', 'walking'),
    ('80', 'water_polo'), ('81', 'weightlifting'), ('82', 'wheelchair'), ('83', 'yoga');

-- ---------------------------------------------------------------- installs

CREATE VIEW dash.installs AS
WITH RECURSIVE ledger AS (
    SELECT p.install,
           p.source AS payload_source,
           max(p.received_at) FILTER (WHERE p.kind <> 'ping') AS last_received,
           max(p.built_at) FILTER (WHERE p.kind <> 'ping') AS last_built,
           (array_agg(p.sequence ORDER BY p.received_at DESC, p.id DESC)
                FILTER (WHERE p.sequence IS NOT NULL))[1] AS last_sequence,
           (array_agg(p.app_version ORDER BY p.received_at DESC, p.id DESC))[1] AS app_version,
           (count(*) FILTER (WHERE p.kind <> 'ping'
                              AND p.received_at > now() - interval '24 hours'))::int AS payloads_24h,
           (array_agg(p.body ->> 'device' ORDER BY p.received_at DESC, p.id DESC)
                FILTER (WHERE p.body ? 'device'))[1] AS device
    FROM payloads p
    GROUP BY p.install, p.source
),
-- Distinct (install, payload_source) pairs in records, one index probe each, so rows
-- stored before the ledger existed still show up without scanning the table.
pairs AS (
    (SELECT install, payload_source FROM records
     ORDER BY install, payload_source LIMIT 1)
    UNION ALL
    SELECT n.install, n.payload_source
    FROM pairs p
    CROSS JOIN LATERAL (
        SELECT r.install, r.payload_source FROM records r
        WHERE (r.install, r.payload_source) > (p.install, p.payload_source)
        ORDER BY r.install, r.payload_source LIMIT 1) n
),
stored AS (
    SELECT p.install, p.payload_source,
           (SELECT max(r.received_at) FROM records r
            WHERE r.install = p.install AND r.payload_source = p.payload_source) AS last_received,
           (SELECT r.device FROM records r
            WHERE r.type = 'screen_time' AND r.install = p.install
              AND r.payload_source = p.payload_source
            ORDER BY r.ts DESC LIMIT 1) AS device
    FROM pairs p
)
SELECT coalesce(l.install, s.install) AS install,
       coalesce(l.payload_source, s.payload_source) AS payload_source,
       CASE coalesce(l.payload_source, s.payload_source)
           WHEN 'health_connect' THEN 'Android'
           WHEN 'healthkit_ios' THEN 'iPhone'
           WHEN 'screen_time' THEN 'Screen time'
           ELSE coalesce(l.payload_source, s.payload_source)
       END AS platform,
       coalesce(l.device, s.device) AS device,
       greatest(l.last_received, s.last_received) AS last_received,
       l.last_built,
       l.last_sequence,
       l.app_version,
       coalesce(l.payloads_24h, 0) AS payloads_24h,
       extract(epoch FROM now() - greatest(l.last_received, s.last_received))::double precision
           / 3600 AS hours_since
FROM ledger l
FULL JOIN stored s ON s.install = l.install AND s.payload_source = l.payload_source;

-- ---------------------------------------------------------------- activity

CREATE VIEW dash.activity_days AS
SELECT dash.day_start(d.day) AS "time",
       d.day,
       d.install,
       d.payload_source,
       round(dash.num(d.data -> 'steps'))::bigint AS steps,
       dash.num(d.data -> 'distance_meters') AS distance_m,
       dash.num(d.data -> 'active_calories') AS active_kcal,
       dash.num(d.data -> 'total_calories') AS total_kcal,
       d.day < dash.today() AS complete
FROM (
    SELECT DISTINCT ON (r.install, r.day) r.install, r.day, r.payload_source, r.data
    FROM records r
    WHERE r.type = 'daily_totals' AND r.day IS NOT NULL
    ORDER BY r.install, r.day, r.built_at DESC NULLS LAST, r.received_at DESC, r.id DESC
) d;

CREATE VIEW dash.steps_hourly AS
SELECT x.hour_start AS "time",
       dash.local_day(x.hour_start) AS day,
       extract(hour FROM x.hour_start AT TIME ZONE dash.tz())::int AS hour,
       x.install,
       round(max(x.steps))::bigint AS steps,
       count(*)::int AS sources
FROM (
    SELECT r.install, r.source_app, dash.hour_start(r.start_ts) AS hour_start,
           sum(dash.num(r.data -> 'count')) AS steps
    FROM records r
    WHERE r.type = 'steps' AND dash.num(r.data -> 'count') IS NOT NULL
    GROUP BY r.install, r.source_app, dash.hour_start(r.start_ts)
) x
GROUP BY x.install, x.hour_start;

CREATE VIEW dash.steps_raw_days AS
SELECT dash.day_start(x.day) AS "time", x.day, x.install, x.source_app, x.steps,
       x.day < dash.today() AS complete
FROM (
    SELECT r.install, coalesce(r.source_app, '') AS source_app,
           dash.local_day(r.start_ts) AS day,
           round(sum(dash.num(r.data -> 'count')))::bigint AS steps
    FROM records r
    WHERE r.type = 'steps' AND dash.num(r.data -> 'count') IS NOT NULL
    GROUP BY 1, 2, 3
) x;

-- ---------------------------------------------------------------- heart rate

-- Raw samples, plus heart rate sent in buckets (Data Resolution on the phone): a bucket
-- is one row at its start, with its average, lowest and highest value and its sample count.
CREATE VIEW dash.heart_rate AS
SELECT r.ts AS "time",
       r.install,
       r.source_app,
       dash.num(r.data -> 'bpm') AS bpm,
       dash.num(r.data -> 'bpm') AS bpm_min,
       dash.num(r.data -> 'bpm') AS bpm_max,
       1 AS n
FROM records r
WHERE r.type = 'heart_rate' AND dash.num(r.data -> 'bpm') IS NOT NULL
UNION ALL
SELECT b.bucket_start,
       b.install,
       b.sources[1],
       b.avg,
       coalesce(b.min, b.avg),
       coalesce(b.max, b.avg),
       greatest(coalesce(b.sample_count, 1), 1)::int
FROM buckets b
WHERE b.type = 'heart_rate' AND b.avg IS NOT NULL;

-- Hours and days are generated first and each one reads its own range of records and
-- buckets through an index (a join to the heart_rate view would scan all of it), so a
-- dashboard's time filter keeps the work to the range shown.
CREATE VIEW dash.heart_rate_hourly AS
SELECT h."time",
       r.install,
       sum(r.bpm * r.n) / sum(r.n) AS bpm,
       min(r.bpm_min) AS bpm_min,
       max(r.bpm_max) AS bpm_max,
       sum(r.n)::int AS n
FROM generate_series(
         dash.hour_start(least((SELECT min(ts) FROM records WHERE type = 'heart_rate'),
                               (SELECT min(bucket_start) FROM buckets WHERE type = 'heart_rate'))),
         greatest((SELECT max(ts) FROM records WHERE type = 'heart_rate'),
                  (SELECT max(bucket_start) FROM buckets WHERE type = 'heart_rate')),
         interval '1 hour') AS h("time")
CROSS JOIN LATERAL (
    SELECT r.install, dash.num(r.data -> 'bpm') AS bpm, dash.num(r.data -> 'bpm') AS bpm_min,
           dash.num(r.data -> 'bpm') AS bpm_max, 1 AS n
    FROM records r
    WHERE r.type = 'heart_rate' AND r.ts >= h."time" AND r.ts < h."time" + interval '1 hour'
      AND dash.num(r.data -> 'bpm') IS NOT NULL
    UNION ALL
    SELECT b.install, b.avg, coalesce(b.min, b.avg), coalesce(b.max, b.avg),
           greatest(coalesce(b.sample_count, 1), 1)::int
    FROM buckets b
    WHERE b.type = 'heart_rate' AND b.bucket_start >= h."time"
      AND b.bucket_start < h."time" + interval '1 hour' AND b.avg IS NOT NULL
) r
GROUP BY h."time", r.install;

CREATE VIEW dash.heart_days AS
SELECT d."time",
       d.day,
       x.install,
       sum(x.bpm * x.n) / sum(x.n) AS bpm,
       min(x.bpm_min) AS bpm_min,
       max(x.bpm_max) AS bpm_max,
       sum(x.n)::int AS n,
       -- Minutes with a sample; a bucket counts for its whole window.
       (count(DISTINCT date_trunc('minute', x."time")) FILTER (WHERE x.span_min IS NULL)
        + coalesce(sum(x.span_min), 0))::double precision / 60 AS wear_h,
       d.day < dash.today() AS complete
FROM dash.days(least((SELECT min(ts) FROM records WHERE type = 'heart_rate'),
                     (SELECT min(bucket_start) FROM buckets WHERE type = 'heart_rate')),
               greatest((SELECT max(ts) FROM records WHERE type = 'heart_rate'),
                        (SELECT max(bucket_start) FROM buckets WHERE type = 'heart_rate'))) d
CROSS JOIN LATERAL (
    SELECT r.ts AS "time", r.install, dash.num(r.data -> 'bpm') AS bpm,
           dash.num(r.data -> 'bpm') AS bpm_min, dash.num(r.data -> 'bpm') AS bpm_max,
           1 AS n, NULL::double precision AS span_min
    FROM records r
    WHERE r.type = 'heart_rate' AND r.ts >= d."time" AND r.ts < dash.day_start(d.day + 1)
      AND dash.num(r.data -> 'bpm') IS NOT NULL
    UNION ALL
    SELECT b.bucket_start, b.install, b.avg, coalesce(b.min, b.avg), coalesce(b.max, b.avg),
           greatest(coalesce(b.sample_count, 1), 1)::int,
           extract(epoch FROM b.bucket_end - b.bucket_start) / 60
    FROM buckets b
    WHERE b.type = 'heart_rate' AND b.bucket_start >= d."time"
      AND b.bucket_start < dash.day_start(d.day + 1) AND b.avg IS NOT NULL
) x
GROUP BY d."time", d.day, x.install;

-- ---------------------------------------------------------------- measurements

CREATE VIEW dash.measurements AS
SELECT r.ts AS "time",
       dash.local_day(r.ts) AS day,
       r.install,
       r.payload_source,
       r.type,
       CASE WHEN m.measure = 'hrv'
            THEN CASE WHEN r.payload_source = 'healthkit_ios' THEN 'sdnn' ELSE 'rmssd' END
            ELSE m.measure END AS measure,
       r.source_app,
       dash.num(r.data -> m.field) AS value,
       m.unit
FROM (VALUES
    ('resting_heart_rate', 'resting_heart_rate', 'bpm', 'bpm', NULL),
    ('heart_rate_variability', 'hrv', 'heart_rate_variability_millis', 'ms', NULL),
    ('oxygen_saturation', 'oxygen_saturation', 'percentage', '%', NULL),
    ('respiratory_rate', 'respiratory_rate', 'rate', '/min', NULL),
    ('vo2_max', 'vo2_max', 'vo2_ml_per_min_per_kg', 'ml/kg/min', NULL),
    ('weight', 'weight', 'kilograms', 'kg', NULL),
    ('body_fat', 'body_fat', 'percentage', '%', NULL),
    ('lean_body_mass', 'lean_body_mass', 'kilograms', 'kg', NULL),
    ('bone_mass', 'bone_mass', 'kilograms', 'kg', NULL),
    ('body_water_mass', 'body_water_mass', 'kilograms', 'kg', NULL),
    ('blood_pressure', 'systolic', 'systolic', 'mmHg', NULL),
    ('blood_pressure', 'diastolic', 'diastolic', 'mmHg', NULL),
    ('blood_glucose', 'blood_glucose', 'mmol_per_liter', 'mmol/L', NULL),
    ('body_temperature', 'body_temperature', 'celsius', '°C', NULL),
    ('basal_body_temperature', 'basal_body_temperature', 'celsius', '°C', NULL),
    ('skin_temperature', 'delta', 'delta_celsius', '°C', 'health_connect')
) AS m(type, measure, field, unit, only_source)
JOIN records r ON r.type = m.type
WHERE dash.num(r.data -> m.field) IS NOT NULL
  AND (m.only_source IS NULL OR r.payload_source = m.only_source);

CREATE VIEW dash.measurement_days AS
SELECT dash.day_start(m.day) AS "time",
       m.day,
       m.install,
       m.payload_source,
       m.type,
       m.measure,
       m.unit,
       (array_agg(m.value ORDER BY m."time"))[1] AS first,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY m.value) AS median,
       avg(m.value) AS avg,
       min(m.value) AS min,
       max(m.value) AS max,
       count(*)::int AS n,
       m.day < dash.today() AS complete
FROM dash.measurements m
GROUP BY m.day, m.install, m.payload_source, m.type, m.measure, m.unit;

CREATE VIEW dash.weight_days AS
WITH readings AS (
    -- The same weighing written by two apps counts once.
    SELECT DISTINCT ON (m.install, m."time", round(m.value::numeric, 1))
           m.install, m."time", m.day, m.value
    FROM dash.measurements m
    WHERE m.type = 'weight'
    ORDER BY m.install, m."time", round(m.value::numeric, 1)
),
per_day AS (
    SELECT install, day,
           (array_agg(value ORDER BY "time"))[1] AS kg,
           sum(value) AS kg_sum,
           count(*) AS readings
    FROM readings
    GROUP BY install, day
)
SELECT dash.day_start(day) AS "time",
       day,
       install,
       kg,
       sum(kg_sum) OVER w / sum(readings) OVER w AS kg_7d,
       readings::int AS readings,
       day < dash.today() AS complete
FROM per_day
WINDOW w AS (PARTITION BY install ORDER BY day
             RANGE BETWEEN interval '6 days' PRECEDING AND CURRENT ROW);

-- ---------------------------------------------------------------- sleep

-- Every session with its stage sums. A stage counts only when it was written by the
-- session's own source: iOS nights mix Watch stages with an iPhone in_bed sample.
CREATE VIEW dash.sleep_sessions AS
SELECT s.id,
       s.install,
       s.payload_source,
       s.source_app,
       s.start_ts AS start_time,
       s.ts AS end_time,
       dash.local_day(s.ts) AS day,
       dash.num(s.data -> 'duration_seconds') / 3600 AS in_bed_h,
       CASE WHEN st.counted > 0
            THEN coalesce(st.light, 0) + coalesce(st.deep, 0) + coalesce(st.rem, 0)
                 + coalesce(st.asleep, 0)
            ELSE dash.num(s.data -> 'duration_seconds') / 3600 END AS asleep_h,
       CASE WHEN st.counted > 0 THEN coalesce(st.deep, 0) END AS deep_h,
       CASE WHEN st.counted > 0 THEN coalesce(st.light, 0) END AS light_h,
       CASE WHEN st.counted > 0 THEN coalesce(st.rem, 0) END AS rem_h,
       CASE WHEN st.counted > 0 THEN coalesce(st.awake, 0) END AS awake_h,
       coalesce(st.counted, 0) > 0 AS has_stages
FROM records s
LEFT JOIN LATERAL (
    SELECT count(*) AS counted,
           sum(x.hours) FILTER (WHERE x.stage = 'deep') AS deep,
           sum(x.hours) FILTER (WHERE x.stage = 'light') AS light,
           sum(x.hours) FILTER (WHERE x.stage = 'rem') AS rem,
           sum(x.hours) FILTER (WHERE x.stage = 'asleep') AS asleep,
           sum(x.hours) FILTER (WHERE x.stage = 'awake') AS awake
    FROM (
        SELECT CASE e ->> 'stage'
                   WHEN 'light' THEN 'light' WHEN 'deep' THEN 'deep' WHEN 'rem' THEN 'rem'
                   WHEN 'sleeping' THEN 'asleep'
                   WHEN 'awake' THEN 'awake' WHEN 'awake_in_bed' THEN 'awake'
                   WHEN 'out_of_bed' THEN 'awake'
               END AS stage,
               coalesce(dash.num(e -> 'duration_seconds'),
                        extract(epoch FROM dash.ts(e ->> 'end_time') - dash.ts(e ->> 'start_time'))
                       ) / 3600 AS hours
        FROM jsonb_array_elements(CASE WHEN jsonb_typeof(s.data -> 'stages') = 'array'
                                       THEN s.data -> 'stages' ELSE '[]'::jsonb END) e
        WHERE coalesce(e ->> 'source', s.source_app) IS NOT DISTINCT FROM s.source_app
    ) x
    WHERE x.stage IS NOT NULL
) st ON true
WHERE s.type = 'sleep' AND dash.num(s.data -> 'duration_seconds') IS NOT NULL;

-- The main session of each night: most hours asleep, then longest in bed.
CREATE VIEW dash.sleep_main AS
SELECT DISTINCT ON (install, day) *
FROM dash.sleep_sessions
ORDER BY install, day, asleep_h DESC NULLS LAST, in_bed_h DESC NULLS LAST, id DESC;

CREATE VIEW dash.sleep_nights AS
SELECT dash.day_start(m.day) AS "time",
       m.day,
       m.install,
       m.payload_source,
       m.source_app,
       m.start_time,
       m.end_time,
       m.in_bed_h,
       m.asleep_h,
       m.deep_h,
       m.light_h,
       m.rem_h,
       m.awake_h,
       m.has_stages,
       round(extract(epoch FROM (m.start_time AT TIME ZONE dash.tz())
                                - ((m.day - 1)::timestamp + interval '18 hours')) / 60)::int AS bed_min,
       round(extract(epoch FROM (m.end_time AT TIME ZONE dash.tz())
                                - ((m.day - 1)::timestamp + interval '18 hours')) / 60)::int AS wake_min,
       hr.hr_low,
       hr.hr_avg,
       m.day < dash.today() AS complete
FROM dash.sleep_main m
LEFT JOIN LATERAL (
    SELECT min(b.bpm) AS hr_low,
           sum(b.bpm * b.n) / nullif(sum(b.n), 0) AS hr_avg
    FROM (
        SELECT sum(h.bpm * h.n) / sum(h.n) AS bpm, sum(h.n) AS n
        FROM dash.heart_rate h
        WHERE h.install = m.install
          AND h."time" >= m.start_time AND h."time" < m.end_time
        GROUP BY date_bin(interval '10 minutes', h."time", timestamptz '2000-01-01 00:00:00+00')
    ) b
) hr ON true;

CREATE VIEW dash.sleep_stages AS
SELECT dash.ts(e ->> 'start_time') AS "time",
       m.day,
       m.install,
       CASE e ->> 'stage'
           WHEN 'light' THEN 'light' WHEN 'deep' THEN 'deep' WHEN 'rem' THEN 'rem'
           WHEN 'sleeping' THEN 'asleep'
           WHEN 'awake' THEN 'awake' WHEN 'awake_in_bed' THEN 'awake' WHEN 'out_of_bed' THEN 'awake'
       END AS stage,
       dash.ts(e ->> 'start_time') AS start_time,
       dash.ts(e ->> 'end_time') AS end_time,
       coalesce(dash.num(e -> 'duration_seconds'),
                extract(epoch FROM dash.ts(e ->> 'end_time') - dash.ts(e ->> 'start_time'))) / 60
           AS minutes
FROM dash.sleep_main m
JOIN records s ON s.id = m.id
CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(s.data -> 'stages') = 'array'
                                             THEN s.data -> 'stages' ELSE '[]'::jsonb END) e
WHERE coalesce(e ->> 'source', s.source_app) IS NOT DISTINCT FROM s.source_app
  AND e ->> 'stage' IN ('light', 'deep', 'rem', 'sleeping', 'awake', 'awake_in_bed', 'out_of_bed')
  AND dash.ts(e ->> 'start_time') IS NOT NULL;

-- ---------------------------------------------------------------- workouts

CREATE VIEW dash.workouts AS
SELECT r.start_ts AS "time",
       r.ts AS end_time,
       dash.local_day(r.start_ts) AS day,
       r.install,
       r.payload_source,
       r.source_app,
       r.data ->> 'type' AS type_code,
       CASE WHEN r.data ->> 'type' ~ '^[0-9]+$' THEN coalesce(t.activity, 'other')
            WHEN coalesce(r.data ->> 'type', '') = '' THEN 'other'
            ELSE r.data ->> 'type' END AS activity,
       coalesce(dash.num(r.data -> 'duration_seconds'),
                extract(epoch FROM r.ts - r.start_ts)) / 60 AS minutes
FROM records r
LEFT JOIN dash.exercise_types t ON t.code = r.data ->> 'type'
WHERE r.type = 'exercise'
  -- A watch and a phone app often both record one workout. Keep the longer copy when two
  -- sources overlap for more than half of the shorter one.
  AND NOT EXISTS (
      SELECT 1 FROM records o
      WHERE o.type = 'exercise' AND o.install = r.install AND o.id <> r.id
        AND o.source_app IS DISTINCT FROM r.source_app
        AND o.start_ts < r.ts AND o.ts > r.start_ts
        AND extract(epoch FROM least(o.ts, r.ts) - greatest(o.start_ts, r.start_ts))
            > 0.5 * least(extract(epoch FROM o.ts - o.start_ts), extract(epoch FROM r.ts - r.start_ts))
        AND (o.ts - o.start_ts > r.ts - r.start_ts
             OR (o.ts - o.start_ts = r.ts - r.start_ts AND o.id < r.id)));

-- ---------------------------------------------------------------- screen time

CREATE VIEW dash.screen_days AS
SELECT dash.day_start(d.day) AS "time",
       d.day,
       d.install,
       d.device,
       round(dash.num(d.data -> 'total_screen_time_minutes'))::int AS total_min,
       round(dash.num(d.data -> 'filtered_screen_time_minutes'))::int AS filtered_min,
       d.data ->> '_app_filter' AS app_filter,
       (SELECT count(*)::int FROM jsonb_array_elements(
            CASE WHEN jsonb_typeof(d.data -> 'apps') = 'array' THEN d.data -> 'apps'
                 ELSE '[]'::jsonb END) a) AS apps,
       (SELECT max(dash.ts(a ->> 'last_used')) FROM jsonb_array_elements(
            CASE WHEN jsonb_typeof(d.data -> 'apps') = 'array' THEN d.data -> 'apps'
                 ELSE '[]'::jsonb END) a) AS last_use,
       d.day < dash.today() AS complete
FROM (
    SELECT DISTINCT ON (r.install, coalesce(r.device, ''), r.day)
           r.install, coalesce(r.device, '') AS device, r.day, r.data
    FROM records r
    WHERE r.type = 'screen_time' AND r.day IS NOT NULL
    ORDER BY r.install, coalesce(r.device, ''), r.day,
             r.seq DESC NULLS LAST, r.built_at DESC NULLS LAST, r.id DESC
) d;

CREATE VIEW dash.screen_apps AS
SELECT dash.day_start(d.day) AS "time",
       d.day,
       d.install,
       d.device,
       a ->> 'package' AS package,
       coalesce(nullif(a ->> 'name', ''), a ->> 'package') AS app,
       round(dash.num(a -> 'minutes'))::int AS minutes,
       dash.ts(a ->> 'last_used') AS last_used
FROM (
    SELECT DISTINCT ON (r.install, coalesce(r.device, ''), r.day)
           r.install, coalesce(r.device, '') AS device, r.day, r.data
    FROM records r
    WHERE r.type = 'screen_time' AND r.day IS NOT NULL
    ORDER BY r.install, coalesce(r.device, ''), r.day,
             r.seq DESC NULLS LAST, r.built_at DESC NULLS LAST, r.id DESC
) d
CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(d.data -> 'apps') = 'array'
                                             THEN d.data -> 'apps' ELSE '[]'::jsonb END) a
WHERE a ? 'package';

-- ---------------------------------------------------------------- data and sync

CREATE VIEW dash.type_summary AS
SELECT r.install,
       r.payload_source,
       r.type,
       r.source_app,
       count(*) AS records,
       min(r.ts) AS first_time,
       max(r.ts) AS last_time,
       max(r.received_at) AS last_received
FROM records r
GROUP BY r.install, r.payload_source, r.type, r.source_app;

CREATE VIEW dash.records_days AS
SELECT x."time", x.day, x.install, x.type, sum(x.records)::int AS records,
       x.day < dash.today() AS complete
FROM (
    -- Day records count on their own date.
    SELECT dash.day_start(r.day) AS "time", r.day, r.install, r.type, count(*) AS records
    FROM records r
    WHERE r.type IN ('daily_totals', 'screen_time') AND r.day IS NOT NULL
    GROUP BY r.day, r.install, r.type
    UNION ALL
    -- Everything else on the local day of its time, one index range per day.
    SELECT d."time", d.day, r.install, r.type, count(*) AS records
    FROM dash.days((SELECT min(ts) FROM records), (SELECT max(ts) FROM records)) d
    JOIN records r ON r.ts >= d."time" AND r.ts < dash.day_start(d.day + 1)
    WHERE r.type NOT IN ('daily_totals', 'screen_time')
    GROUP BY d."time", d.day, r.install, r.type
) x
GROUP BY x."time", x.day, x.install, x.type;
