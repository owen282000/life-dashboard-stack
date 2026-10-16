"""Roles the receiver may or may not manage.

A database owned by a plain login role without SUPERUSER or CREATEROLE, like the app user of
a CloudNativePG cluster: grafana_ro and dash_ro already exist, created by a superuser the way
CNPG's managed roles create them. The receiver must leave them as they are without sending a
statement Postgres would refuse, still grant them their reads, and say so in its log.

A database owned by a role with CREATEROLE but no SUPERUSER, like the admin user of a managed
Postgres service: the receiver creates the roles itself, naming only attributes it holds.
"""

import uuid

import psycopg
import pytest
from psycopg import sql

import api
import roles
import queries
import receiver
from conftest import RO_PASSWORD, envelope, post

OWNER_PASSWORD = "owner-password"


def make_owner_db(server_url, attributes):
    owner = f"ld_owner_{uuid.uuid4().hex[:8]}"
    name = f"t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(server_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER " + attributes + " PASSWORD {}").format(
            sql.Identifier(owner), sql.Literal(OWNER_PASSWORD)))
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name), sql.Identifier(owner)))
    host = server_url.split("@", 1)[1].rsplit("/", 1)[0]
    return owner, name, f"postgresql://{owner}:{OWNER_PASSWORD}@{host}/{name}"


def drop_owner_db(server_url, owner, name):
    with psycopg.connect(server_url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(owner)))


@pytest.fixture
def owner_db(server_url, db):
    """A database owned by a role that may not create or alter roles; returns its owner URL.

    The db fixture runs first, as the superuser, so grafana_ro and dash_ro exist with
    RO_PASSWORD and their settings, like roles that CNPG created.
    """
    owner, name, dsn = make_owner_db(server_url, "NOCREATEROLE NOCREATEDB")
    yield dsn
    drop_owner_db(server_url, owner, name)


class Spy:
    """A connection that records every statement it runs."""

    def __init__(self, conn):
        self.conn = conn
        self.statements = []

    def execute(self, query, params=None):
        self.statements.append(query if isinstance(query, str) else query.as_string(self.conn))
        return self.conn.execute(query, params)

    def __getattr__(self, name):
        return getattr(self.conn, name)


def as_role(dsn, role, password=RO_PASSWORD):
    rest = dsn.split("@", 1)[1]
    return f"postgresql://{role}:{password}@{rest}"


def test_setup_succeeds_and_says_the_roles_are_managed_elsewhere(owner_db, capfd):
    receiver.setup_database(owner_db, "UTC", RO_PASSWORD)
    err = capfd.readouterr().err
    assert "grafana_ro is managed outside the receiver" in err
    assert "dash_ro is managed outside the receiver" in err
    assert "WARNING" not in err
    with psycopg.connect(owner_db) as conn:
        assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] >= 4


def test_grants_still_apply(owner_db):
    receiver.setup_database(owner_db, "UTC", RO_PASSWORD)
    post(owner_db, envelope(1, weight=[{"kilograms": 80.0, "time": "2026-10-04T07:00:00Z", "uuid": "w"}]))
    with psycopg.connect(as_role(owner_db, "grafana_ro"), autocommit=True) as ro:
        assert ro.execute("SELECT count(*) FROM records").fetchone() == (1,)
        assert ro.execute("SELECT count(*) FROM dash.measurements").fetchone() == (1,)
        for statement in ["SELECT * FROM payloads", "INSERT INTO records (type, ts, data) VALUES ('x', now(), '{}')",
                          "CREATE TABLE public.mine (id int)"]:
            with pytest.raises(psycopg.Error):
                ro.execute(statement)
    with queries.open_reader(api.dsn(owner_db)) as reader:
        assert [p["phone"] for p in reader.phones()] == ["phone"]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            reader.conn.execute("SELECT * FROM records")


def test_the_password_stays_as_the_role_manager_set_it(owner_db):
    receiver.setup_database(owner_db, "UTC", "a-password-the-receiver-cannot-set")
    psycopg.connect(as_role(owner_db, "grafana_ro")).close()
    psycopg.connect(api.dsn(owner_db)).close()


def test_missing_settings_are_named_in_the_log(server_url, owner_db, capfd):
    with psycopg.connect(server_url, autocommit=True) as su:
        su.execute("ALTER ROLE dash_ro RESET statement_timeout")
    try:
        receiver.setup_database(owner_db, "UTC", RO_PASSWORD)
        err = capfd.readouterr().err
        assert "WARNING: dash_ro lacks statement_timeout" in err
        assert "ALTER ROLE dash_ro SET statement_timeout = '15s';" in err
    finally:
        with psycopg.connect(server_url, autocommit=True) as su:
            su.execute("ALTER ROLE dash_ro SET statement_timeout = '15s'")


def test_no_role_statement_is_sent_that_postgres_would_refuse(owner_db):
    """A refused ALTER ROLE ... PASSWORD would land, password and all, in the server's log."""
    with psycopg.connect(owner_db, autocommit=True) as conn:
        spy = Spy(conn)
        with conn.transaction():
            assert roles.ensure_login_role(spy, "grafana_ro", "secret-password", {}, "GRAFANA_DB_PASSWORD") is False
    assert not [s for s in spy.statements if s.lstrip().upper().startswith(("CREATE ROLE", "ALTER ROLE"))]
    assert not [s for s in spy.statements if "secret-password" in s]


def test_a_password_goes_to_postgres_as_a_scram_verifier(make_db):
    dsn = make_db("UTC")
    with psycopg.connect(dsn, autocommit=True) as conn:
        spy = Spy(conn)
        with conn.transaction():
            assert roles.ensure_login_role(spy, "grafana_ro", RO_PASSWORD, receiver.GRAFANA_RO_SETTINGS,
                                           "GRAFANA_DB_PASSWORD") is True
    password_statements = [s for s in spy.statements if "PASSWORD" in s]
    assert len(password_statements) == 1
    assert "'SCRAM-SHA-256$" in password_statements[0]
    assert RO_PASSWORD not in " ".join(spy.statements)
    psycopg.connect(as_role(dsn, "grafana_ro")).close()


@pytest.fixture
def parked_roles(server_url):
    """grafana_ro and dash_ro moved aside, so a test can create them anew; restored afterwards."""
    with psycopg.connect(server_url, autocommit=True) as su:
        for role in ("grafana_ro", "dash_ro"):
            su.execute(sql.SQL("ALTER ROLE {} RENAME TO {}").format(
                sql.Identifier(role), sql.Identifier(role + "_parked")))
    yield
    with psycopg.connect(server_url, autocommit=True) as su:
        for role in ("grafana_ro", "dash_ro"):
            su.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
            su.execute(sql.SQL("ALTER ROLE {} RENAME TO {}").format(
                sql.Identifier(role + "_parked"), sql.Identifier(role)))


def test_a_user_with_createrole_creates_and_updates_the_roles(server_url, db, parked_roles, capfd):
    """Like the admin user of a managed Postgres: CREATEROLE, but no SUPERUSER or REPLICATION."""
    owner, name, dsn = make_owner_db(server_url, "CREATEROLE NOCREATEDB")
    try:
        receiver.setup_database(dsn, "UTC", RO_PASSWORD)
        assert "managed outside" not in capfd.readouterr().err
        with psycopg.connect(dsn) as conn:
            rows = conn.execute("SELECT rolname, rolsuper, rolcreaterole, rolcanlogin, rolconfig FROM pg_roles"
                                " WHERE rolname IN ('grafana_ro', 'dash_ro') ORDER BY 1").fetchall()
        assert [r[:4] for r in rows] == [("dash_ro", False, False, True), ("grafana_ro", False, False, True)]
        assert "statement_timeout=15s" in rows[0][4] and "statement_timeout=30s" in rows[1][4]
        post(dsn, envelope(1, weight=[{"kilograms": 80.0, "time": "2026-10-04T07:00:00Z", "uuid": "w"}]))
        with psycopg.connect(as_role(dsn, "grafana_ro"), autocommit=True) as ro:
            assert ro.execute("SELECT count(*) FROM records").fetchone() == (1,)
        # A second start, with a new password, updates the roles it created.
        receiver.setup_database(dsn, "UTC", "a-rotated-password")
        psycopg.connect(as_role(dsn, "grafana_ro", "a-rotated-password")).close()
    finally:
        drop_owner_db(server_url, owner, name)


def test_a_user_with_createrole_leaves_a_role_it_did_not_create(server_url, owner_db, capfd):
    """Postgres 16 and later: CREATEROLE alone may not alter a role created by someone else."""
    owner, name, dsn = make_owner_db(server_url, "CREATEROLE NOCREATEDB")
    try:
        receiver.setup_database(dsn, "UTC", "a-password-it-may-not-set")
        assert "grafana_ro is managed outside the receiver" in capfd.readouterr().err
        psycopg.connect(as_role(dsn, "grafana_ro")).close()
    finally:
        drop_owner_db(server_url, owner, name)


def test_a_missing_role_stops_the_start_with_a_clear_message(server_url, owner_db):
    with psycopg.connect(server_url, autocommit=True) as su:
        su.execute("ALTER ROLE grafana_ro RENAME TO grafana_ro_parked")
    try:
        with pytest.raises(receiver.SetupError, match="grafana_ro does not exist, and ld_owner_"):
            receiver.setup_database(owner_db, "UTC", RO_PASSWORD)
    finally:
        with psycopg.connect(server_url, autocommit=True) as su:
            su.execute("ALTER ROLE grafana_ro_parked RENAME TO grafana_ro")


def test_a_superuser_still_creates_and_updates_the_roles(make_db, capfd):
    dsn = make_db("UTC")
    receiver.setup_database(dsn, "UTC", "rotated-password")
    assert "managed outside" not in capfd.readouterr().err
    psycopg.connect(as_role(dsn, "grafana_ro", "rotated-password")).close()
    receiver.setup_database(dsn, "UTC", RO_PASSWORD)
    psycopg.connect(as_role(dsn, "grafana_ro")).close()
