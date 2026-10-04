"""grafana_ro: reads the views and records, nothing else."""

import psycopg
import pytest

from conftest import RO_PASSWORD, envelope, post


def ro_dsn(dsn):
    rest = dsn.split("@", 1)[1]
    return f"postgresql://grafana_ro:{RO_PASSWORD}@{rest}"


@pytest.fixture
def ro(db):
    post(db, envelope(1, weight=[{"kilograms": 80.0, "time": "2026-10-04T07:00:00Z", "uuid": "w"}]))
    with psycopg.connect(ro_dsn(db), autocommit=True) as conn:
        yield conn


def fails(conn, statement):
    with pytest.raises(psycopg.Error):
        conn.execute(statement)


def test_role_attributes(db):
    with psycopg.connect(db) as conn:
        row = conn.execute("SELECT rolsuper, rolcreatedb, rolcreaterole, rolcanlogin, rolconfig"
                           " FROM pg_roles WHERE rolname = 'grafana_ro'").fetchone()
    assert row[:4] == (False, False, False, True)
    assert "statement_timeout=30s" in row[4]
    assert "default_transaction_read_only=on" in row[4]


def test_reads_views_and_records(ro):
    assert ro.execute("SELECT count(*) FROM dash.measurements").fetchone() == (1,)
    assert ro.execute("SELECT count(*) FROM records").fetchone() == (1,)
    assert ro.execute("SELECT install FROM dash.installs").fetchone() == ("phone",)
    assert ro.execute("SELECT count(*) FROM dash.days(now() - interval '2 days', now())").fetchone() == (3,)


def test_cannot_read_the_ledger_or_migrations(ro):
    fails(ro, "SELECT * FROM payloads")
    fails(ro, "SELECT * FROM schema_migrations")
    fails(ro, "SELECT * FROM tombstones")


def test_cannot_write_create_copy_or_switch_role(ro):
    ro.execute("SET default_transaction_read_only = off")  # only extra protection: grants must hold
    fails(ro, "INSERT INTO records (type, ts, data) VALUES ('x', now(), '{}')")
    fails(ro, "UPDATE records SET type = 'x'")
    fails(ro, "DELETE FROM records")
    fails(ro, "CREATE TABLE public.mine (id int)")
    fails(ro, "CREATE VIEW dash.mine AS SELECT 1")
    fails(ro, "COPY (SELECT 1) TO PROGRAM 'id'")
    fails(ro, "SET ROLE lifedash")


def test_grants_survive_a_view_rebuild(db):
    import receiver
    receiver.setup_database(db, "UTC", RO_PASSWORD)
    with psycopg.connect(ro_dsn(db)) as conn:
        assert conn.execute("SELECT count(*) FROM dash.activity_days").fetchone() == (0,)


def test_password_comes_from_setup(db):
    import receiver
    receiver.setup_database(db, "UTC", "another-password")
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(ro_dsn(db)).close()
    receiver.setup_database(db, "UTC", RO_PASSWORD)
