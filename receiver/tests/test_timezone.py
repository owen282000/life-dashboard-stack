"""TZ is checked against Postgres at start."""

import os
import subprocess
import sys

import pytest

import receiver
from conftest import RECEIVER_DIR, RO_PASSWORD


def test_invalid_tz_exits_non_zero_with_a_message(make_db):
    dsn = make_db(setup=False)
    env = dict(os.environ, DATABASE_URL=dsn, TZ="Mars/Olympus_Mons", PORT="0", WEBHOOK_SECRET="x")
    proc = subprocess.run([sys.executable, str(RECEIVER_DIR / "receiver.py")], env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode != 0
    assert "TZ='Mars/Olympus_Mons' is not a time zone" in proc.stderr


def test_setup_refuses_an_invalid_tz(make_db):
    dsn = make_db(setup=False)
    with pytest.raises(receiver.SetupError):
        receiver.setup_database(dsn, "Not/AZone", RO_PASSWORD)


def test_quotes_in_tz_cannot_inject(make_db):
    dsn = make_db(setup=False)
    with pytest.raises(receiver.SetupError):
        receiver.setup_database(dsn, "UTC'; DROP TABLE records; --", RO_PASSWORD)
