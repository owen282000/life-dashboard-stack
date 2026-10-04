-- Bucketed series: one value per time window instead of per record, sent when Data
-- Resolution is set for a type on the phone. A bucket marked complete replaces the stored
-- window; any other bucket is combined with it (counts and totals added, the average
-- weighted by sample count). The ledger's duplicate check keeps a repeated payload from
-- being combined twice.

CREATE TABLE buckets (
    install text NOT NULL,
    type text NOT NULL,
    payload_source text NOT NULL,
    bucket_start timestamptz NOT NULL,
    bucket_end timestamptz NOT NULL,
    sample_count bigint,
    avg double precision,
    min double precision,
    max double precision,
    total double precision,
    complete boolean NOT NULL DEFAULT false,
    sources text[],
    seq bigint,
    built_at timestamptz,
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (install, type, payload_source, bucket_start)
);
CREATE INDEX buckets_type_start ON buckets (type, bucket_start);
