"""A new install starts only with a secret and passwords of its own; an existing one as before."""

import os
import socket
import subprocess
import sys

import pytest

import receiver
from conftest import RECEIVER_DIR

SECRET = "s" * 64


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_receiver(dsn: str, **settings) -> tuple[int | None, str]:
    """Start the receiver on dsn; return its exit code (None if it got to ready) and its output."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("WEBHOOK_SECRET", "ALLOW_UNSIGNED", "GRAFANA_DB_PASSWORD", "DASH_DB_PASSWORD")}
    env.update(PORT=str(free_port()), DATABASE_URL=dsn, TZ="UTC", PAIRING_LOG="0", **settings)
    proc = subprocess.Popen([sys.executable, str(RECEIVER_DIR / "receiver.py")], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out = []
    try:
        for line in proc.stdout:
            out.append(line)
            if "database ready" in line:
                proc.terminate()
                proc.wait(timeout=20)
                return None, "".join(out)
        return proc.wait(timeout=20), "".join(out)
    finally:
        if proc.poll() is None:
            proc.kill()


@pytest.mark.parametrize("env, allow, expected", [
    ({}, False, ["WEBHOOK_SECRET"]),
    ({}, True, []),
    ({"WEBHOOK_SECRET": SECRET}, False, []),
    ({"WEBHOOK_SECRET": SECRET, "GRAFANA_DB_PASSWORD": "lifedash-ro"}, False, ["GRAFANA_DB_PASSWORD"]),
    ({"WEBHOOK_SECRET": SECRET, "GRAFANA_DB_PASSWORD": ""}, False, ["GRAFANA_DB_PASSWORD"]),
    ({"WEBHOOK_SECRET": SECRET, "DASH_DB_PASSWORD": "lifedash-dash-ro"}, False, ["DASH_DB_PASSWORD"]),
    ({"WEBHOOK_SECRET": SECRET, "GRAFANA_DB_PASSWORD": "own", "DASH_DB_PASSWORD": "own2"}, False, []),
])
def test_what_a_new_install_needs(env, allow, expected):
    problems = receiver.new_install_problems(env, allow)
    assert [p.split(" ")[0] for p in problems] == expected


def test_a_new_database_without_a_secret_does_not_start(make_db):
    dsn = make_db(setup=False)
    code, out = run_receiver(dsn, WEBHOOK_SECRET="", GRAFANA_DB_PASSWORD="lifedash-ro")
    assert code == 2
    assert "a new install needs these settings first" in out
    assert "WEBHOOK_SECRET is empty" in out and "GRAFANA_DB_PASSWORD is the old default" in out
    assert receiver.is_new_database(dsn), "nothing was set up"


def test_a_new_database_starts_with_its_own_settings(make_db):
    code, out = run_receiver(make_db(setup=False), WEBHOOK_SECRET=SECRET,
                             GRAFANA_DB_PASSWORD="own-ro", DASH_DB_PASSWORD="own-dash")
    assert code is None, out


def test_allow_unsigned_starts_a_new_database_without_a_secret(make_db):
    code, out = run_receiver(make_db(setup=False), WEBHOOK_SECRET="", ALLOW_UNSIGNED="1")
    assert code is None, out
    assert "WEBHOOK_SECRET is empty" in out  # the warning at every start stays


def test_an_existing_database_starts_as_before(make_db):
    # A stack from before 1.2 without a secret and with the old defaults keeps running.
    code, out = run_receiver(make_db(), WEBHOOK_SECRET="", GRAFANA_DB_PASSWORD="lifedash-ro",
                             DASH_DB_PASSWORD="lifedash-dash-ro")
    assert code is None, out
    assert "WEBHOOK_SECRET is empty" in out
