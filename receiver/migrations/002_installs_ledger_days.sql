-- Installs, the payload ledger and one row per day record.
--
-- Every stored row gets the install it came from (the label in /webhook/<label>), the
-- sequence and build time of its payload for the ordering rule, and the columns the views
-- read without digging into the JSON. Day records (daily_totals, screen_time) written
-- before the day-key fix collapse to one row per date, keeping the figure received last.

ALTER TABLE records
    ADD COLUMN install text NOT NULL DEFAULT 'default',
    ADD COLUMN seq bigint,
    ADD COLUMN built_at timestamptz,
    ADD COLUMN day date,
    ADD COLUMN device text,
    ADD COLUMN start_ts timestamptz,
    ADD COLUMN record_id text GENERATED ALWAYS AS (split_part(uuid, '#', 1)) STORED;

UPDATE records SET payload_source = 'unknown' WHERE payload_source IS NULL;

CREATE TABLE payloads (
    id bigserial PRIMARY KEY,
    install text NOT NULL,
    source text,
    sequence bigint,
    app_version text,
    built_at timestamptz,
    received_at timestamptz NOT NULL DEFAULT now(),
    kind text NOT NULL CHECK (kind IN ('sync', 'backfill', 'deletion', 'screen_time', 'ping')),
    record_count integer NOT NULL DEFAULT 0,
    body_sha256 text NOT NULL UNIQUE,
    body jsonb NOT NULL
);
CREATE INDEX payloads_install_source_received ON payloads (install, source, received_at);
CREATE INDEX payloads_install_source_sequence ON payloads (install, source, sequence);

-- A timestamp cast that gives NULL instead of failing on a malformed value.
CREATE FUNCTION pg_temp.try_ts(value text) RETURNS timestamptz
LANGUAGE plpgsql AS $$
BEGIN
    RETURN value::timestamptz;
EXCEPTION WHEN others THEN
    RETURN NULL;
END $$;

CREATE FUNCTION pg_temp.try_num(value jsonb) RETURNS double precision
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN jsonb_typeof(value) = 'number' THEN value::double precision END
$$;

-- Day rows, old and keyed. Keyed rows carry the device in their key
-- (<type>:<payload source>:<device>:<date>); rows from before the fix are synthetic-<hash>.
CREATE TEMP TABLE day_rows ON COMMIT DROP AS
SELECT id,
       type,
       payload_source,
       received_at,
       data ->> 'date' AS d,
       CASE WHEN uuid LIKE 'synthetic-%' THEN NULL
            ELSE substring(uuid FROM '^[^:]*:[^:]*:(.*):[0-9]{4}-[0-9]{2}-[0-9]{2}$')
       END AS dev
FROM records
WHERE type IN ('daily_totals', 'screen_time')
  AND jsonb_typeof(data -> 'date') = 'string'
  AND data ->> 'date' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$';

-- Synthetic screen time rows have no device. They take the device of the keyed rows when
-- exactly one exists, else ''. Daily totals never had one.
UPDATE day_rows
SET dev = CASE
    WHEN type = 'screen_time' THEN coalesce((
        SELECT CASE WHEN count(DISTINCT k.dev) = 1 THEN min(k.dev) END
        FROM day_rows k
        WHERE k.type = 'screen_time' AND k.dev IS NOT NULL), '')
    ELSE '' END
WHERE dev IS NULL;

CREATE TEMP TABLE day_ranked ON COMMIT DROP AS
SELECT id, type, payload_source, dev, d,
       row_number() OVER (PARTITION BY type, payload_source, dev, d
                          ORDER BY received_at DESC NULLS LAST, id DESC) AS rn
FROM day_rows;

DELETE FROM records r USING day_ranked k WHERE r.id = k.id AND k.rn > 1;

UPDATE records r
SET uuid = k.type || ':default:' || k.payload_source || ':' || k.dev || ':' || k.d,
    day = k.d::date,
    device = CASE WHEN k.type = 'screen_time' THEN k.dev END
FROM day_ranked k
WHERE r.id = k.id AND k.rn = 1;

UPDATE records
SET start_ts = coalesce(
    pg_temp.try_ts(data ->> 'start_time'),
    pg_temp.try_ts(data ->> 'session_end_time')
        - make_interval(secs => pg_temp.try_num(data -> 'duration_seconds')),
    ts);

CREATE INDEX records_type_install_ts ON records (type, install, ts);
CREATE INDEX records_type_record_id ON records (type, record_id);
CREATE INDEX records_install_source_received ON records (install, payload_source, received_at);
CREATE INDEX records_type_install_day ON records (type, install, day) WHERE day IS NOT NULL;
CREATE INDEX records_ts ON records (ts);
