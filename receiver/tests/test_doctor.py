"""The doctor: every check against a real database, its verdicts and the fixes it gives."""

import psycopg
import pytest

import doctor
from conftest import RO_PASSWORD, envelope, post, query

SECRET = "c" * 64
HEALTHY_ENV = {
    "WEBHOOK_SECRET": SECRET,
    "PUBLIC_URL": "http://192.168.1.10:8080",
    "TZ": "Europe/Amsterdam",
    "GRAFANA_DB_PASSWORD": RO_PASSWORD,
    "DASH_DB_PASSWORD": "lifedash-dash-ro",
}


def by_name(results):
    return {r.name: r for r in results}


def statuses(results):
    return {r.name: r.status for r in results}


# ------------------------------------------------------------------ settings

def test_healthy_settings_pass():
    got = statuses(doctor.check_settings(HEALTHY_ENV))
    assert got == {"WEBHOOK_SECRET": "PASS", "PUBLIC_URL": "PASS", "TZ": "PASS"}


@pytest.mark.parametrize("secret, status", [("", "FAIL"), ("has a space", "FAIL"), ("x" * 600, "FAIL"),
                                            ("short", "WARN"), (SECRET, "PASS")])
def test_the_webhook_secret(secret, status):
    result = by_name(doctor.check_settings({**HEALTHY_ENV, "WEBHOOK_SECRET": secret}))["WEBHOOK_SECRET"]
    assert result.status == status
    assert SECRET not in result.message and (status == "PASS" or result.fix)


@pytest.mark.parametrize("url, status", [
    ("", "WARN"),
    ("http://localhost:8080", "FAIL"),
    ("http://127.0.0.1:8080", "FAIL"),
    ("ftp://192.168.1.10", "FAIL"),
    ("http://nas.example.com:8080", "WARN"),   # an iPhone refuses plain http to a name
    ("https://lifedash.example.com", "PASS"),
    ("http://192.168.1.10:8080", "PASS"),
    ("http://nas.local:8080", "PASS"),
])
def test_public_url(url, status):
    result = doctor.check_public_url(url)
    assert result.status == status, result
    if status != "PASS":
        assert result.fix


def test_tz_and_the_pairing_token():
    got = by_name(doctor.check_settings({**HEALTHY_ENV, "TZ": "", "PAIRING_TOKEN": "short"}))
    assert got["TZ"].status == "WARN"
    assert got["PAIRING_TOKEN"].status == "WARN"
    assert by_name(doctor.check_settings({**HEALTHY_ENV, "TZ": "UTC"}))["TZ"].status == "PASS"


# ------------------------------------------------------------------ database

def test_a_set_up_database_passes(db):
    got = by_name(doctor.check_database(db, HEALTHY_ENV, 24))
    for name in ("database", "migrations", "dash views", "grafana_ro", "dash_ro"):
        assert got[name].status == "PASS", got[name]
    assert got["phones"].status == "WARN"   # nothing sent yet
    assert "TZ" not in got


def test_no_connection_is_one_failure():
    results = doctor.check_database("postgresql://x:y@127.0.0.1:1/none", HEALTHY_ENV, 24)
    assert [(r.name, r.status) for r in results] == [("database", "FAIL")]
    assert results[0].fix


def test_an_empty_database_has_no_migrations(make_db):
    got = by_name(doctor.check_database(make_db(setup=False), HEALTHY_ENV, 24))
    assert got["database"].status == "PASS"
    assert got["migrations"].status == "FAIL"
    assert got["dash views"].status == "FAIL"


def test_a_missing_and_an_unknown_migration(db):
    with psycopg.connect(db, autocommit=True) as conn:
        latest = conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
        conn.execute("DELETE FROM schema_migrations WHERE version = %s", (latest,))
    assert doctor.check_migrations(psycopg.connect(db)).status == "FAIL"
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO schema_migrations (version) VALUES (%s), ('999_from_the_future.sql')", (latest,))
        result = doctor.check_migrations(conn)
    assert result.status == "WARN" and "999_from_the_future.sql" in result.message


def test_a_wrong_role_password_fails_with_the_fix(db):
    got = by_name(doctor.check_database(db, {**HEALTHY_ENV, "GRAFANA_DB_PASSWORD": "not-it"}, 24))
    assert got["grafana_ro"].status == "FAIL"
    assert "password authentication failed" in got["grafana_ro"].message
    assert "GRAFANA_DB_PASSWORD" in got["grafana_ro"].fix
    assert "not-it" not in got["grafana_ro"].message + got["grafana_ro"].fix
    assert got["dash_ro"].status == "PASS"


def test_a_role_managed_elsewhere_is_checked_by_its_grants(db):
    env = {k: v for k, v in HEALTHY_ENV.items() if k != "GRAFANA_DB_PASSWORD"}
    result = by_name(doctor.check_database(db, env, 24))["grafana_ro"]
    assert result.status == "PASS" and "managed outside" in result.message
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("REVOKE SELECT ON ALL TABLES IN SCHEMA dash FROM grafana_ro")
    assert by_name(doctor.check_database(db, env, 24))["grafana_ro"].status == "FAIL"


def test_a_role_without_read_only_sessions_warns(db):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("ALTER ROLE dash_ro RESET default_transaction_read_only")
    try:
        assert by_name(doctor.check_database(db, HEALTHY_ENV, 24))["dash_ro"].status == "WARN"
    finally:
        with psycopg.connect(db, autocommit=True) as conn:
            conn.execute("ALTER ROLE dash_ro SET default_transaction_read_only = on")


def test_an_unknown_time_zone_fails(db):
    assert by_name(doctor.check_database(db, {**HEALTHY_ENV, "TZ": "Mars/Olympus"}, 24))["TZ"].status == "FAIL"


def test_each_phone_with_its_last_data(db):
    post(db, envelope(seq=1, ts="2026-10-04T08:00:00Z",
                      steps=[{"uuid": "s1", "start_time": "2026-10-04T07:00:00Z",
                              "end_time": "2026-10-04T07:30:00Z", "count": 100}]), install="pixel")
    post(db, envelope(seq=1, ts="2026-10-04T08:00:00Z", source="healthkit_ios",
                      steps=[{"uuid": "s2", "start_time": "2026-10-04T07:00:00Z",
                              "end_time": "2026-10-04T07:30:00Z", "count": 100}]), install="iphone")
    post(db, {"test": True, "timestamp": "2026-10-04T08:00:00Z", "source": "health_connect"}, install="new")
    # The iPhone went quiet three days ago, then pinged an hour ago.
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE records SET received_at = now() - interval '3 days' WHERE install = 'iphone'")
        conn.execute("UPDATE payloads SET received_at = now() - interval '3 days' WHERE install = 'iphone'")
        conn.execute("INSERT INTO payloads (install, source, kind, body_sha256, body, received_at)"
                     " VALUES ('iphone', 'healthkit_ios', 'ping', 'x', '{}', now() - interval '1 hour')")
    got = by_name(doctor.check_database(db, HEALTHY_ENV, 24))
    assert got["phone pixel"].status == "PASS" and "Android" in got["phone pixel"].message
    assert got["phone iphone"].status == "WARN"
    assert "3 days" in got["phone iphone"].message and "ping 1 h ago" in got["phone iphone"].message
    assert got["phone new"].status == "WARN" and "only test pings" in got["phone new"].message
    assert "phones" not in got
    # A longer limit calls nobody quiet.
    assert by_name(doctor.check_database(db, HEALTHY_ENV, 24 * 7))["phone iphone"].status == "PASS"
    assert query(db, "SELECT count(*) FROM payloads")[0][0] == 4   # the doctor wrote nothing


# ------------------------------------------------------------------ HTTP

def test_the_receivers_port_the_api_and_an_unreachable_public_url(db, http_server, monkeypatch):
    import api
    import receiver
    token = "t" * 32
    monkeypatch.setattr(receiver.Handler, "api_token", token)
    monkeypatch.setattr(receiver.Handler, "api_dsn", api.dsn(db))
    port = int(http_server(secret=SECRET).rsplit(":", 1)[1])
    env = {**HEALTHY_ENV, "API_TOKEN": token, "PUBLIC_URL": "http://192.0.2.1:9"}
    got = by_name(doctor.check_http(env, port))
    assert got["receiver /health"].status == "PASS"
    assert got["PUBLIC_URL from the container"].status == "WARN"   # TEST-NET, never answers
    assert "hairpin" in got["PUBLIC_URL from the container"].fix
    assert got["API"].status == "PASS", got["API"]
    # A token the receiver doesn't have: the API answers 401 both ways.
    got = by_name(doctor.check_http({**env, "API_TOKEN": "u" * 32}, port))
    assert got["API"].status == "FAIL" and "u" * 32 not in got["API"].message


def test_public_url_reached_from_the_container_passes(http_server):
    base = http_server(secret=SECRET)
    port = int(base.rsplit(":", 1)[1])
    # 127.0.0.1 is refused as PUBLIC_URL, so check a reachable one through the same helper.
    got = by_name(doctor.check_http({**HEALTHY_ENV, "PUBLIC_URL": base.replace("127.0.0.1", "127.0.0.2")}, port))
    assert got["receiver /health"].status == "PASS"
    assert got["API"].status == "INFO"


def test_a_short_api_token_warns():
    assert doctor.api_token({"API_TOKEN": "short"})[1]
    assert doctor.api_token({"API_TOKEN": "x" * 16}) == ("x" * 16, None)


def test_nothing_on_the_port_fails():
    got = doctor.check_http(HEALTHY_ENV, 1)
    assert [(r.name, r.status) for r in got] == [("receiver /health", "FAIL")]


def test_the_mcp_check_needs_a_401_without_the_token(http_server):
    base = http_server(secret=SECRET)
    # The receiver is no MCP server: /health answers, /mcp is a 404, so the check fails.
    result = doctor.check_mcp(base)[0]
    assert result.status == "FAIL" and "404" in result.message
    assert doctor.check_mcp("http://127.0.0.1:1")[0].status == "FAIL"


# ------------------------------------------------------------------ the whole run

def test_run_report_and_exit_code(db, http_server, monkeypatch, capsys):
    base = http_server(secret=SECRET)
    monkeypatch.setattr(doctor.os, "environ", {**HEALTHY_ENV, "DATABASE_URL": db, "PORT": base.rsplit(":", 1)[1]})
    code = doctor.main([])
    out = capsys.readouterr().out
    assert "PASS  database:" in out and "PASS  receiver /health:" in out
    assert "INFO  phone: no check on the server can tell" in out
    assert "http://192.168.1.10:8080/health" in out
    assert SECRET not in out and RO_PASSWORD not in out
    assert out.strip().splitlines()[-1].endswith("0 failed")
    assert code == 0

    monkeypatch.setattr(doctor.os, "environ", {**HEALTHY_ENV, "WEBHOOK_SECRET": "", "DATABASE_URL": db,
                                               "PORT": base.rsplit(":", 1)[1]})
    assert doctor.main(["--json"]) == 1
    assert '"status": "FAIL"' in capsys.readouterr().out
