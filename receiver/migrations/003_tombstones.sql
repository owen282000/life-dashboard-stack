-- Deleted records leave a tombstone, so a payload that arrives late cannot bring back a
-- record that a newer payload deleted. A newer payload can still write it again: some
-- sources revise a record by deleting it and writing it under the same ID.

CREATE TABLE tombstones (
    type text NOT NULL,
    uuid text NOT NULL,
    seq bigint,
    built_at timestamptz,
    deleted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (type, uuid)
);
