CREATE TABLE IF NOT EXISTS records (
    id bigserial PRIMARY KEY,
    type text NOT NULL,
    ts timestamptz NOT NULL,
    uuid text UNIQUE,
    source_app text,
    payload_source text,
    received_at timestamptz DEFAULT now(),
    data jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS records_type_ts ON records (type, ts);
