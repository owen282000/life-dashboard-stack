"""Fixtures: the receiver's Postgres, and one database with the stack's sample data.

The Postgres comes from the receiver's own fixtures (a container, or TEST_DATABASE_URL).
The sample data is tools/sample_data.py in its DRY mode, stored through the receiver.
"""

import importlib.util
import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

REPO_DIR = Path(__file__).resolve().parents[2]
MCP_DIR = REPO_DIR / "mcp"
sys.path.insert(0, str(MCP_DIR))

_spec = importlib.util.spec_from_file_location("receiver_conftest", REPO_DIR / "receiver" / "tests" / "conftest.py")
receiver_conftest = importlib.util.module_from_spec(_spec)
sys.modules["receiver_conftest"] = receiver_conftest
_spec.loader.exec_module(receiver_conftest)
server_url = receiver_conftest.server_url  # the session's Postgres

import api  # noqa: E402  (the receiver's modules, on the path through its conftest)
import receiver  # noqa: E402
import server  # noqa: E402

TZ = "Europe/Amsterdam"


@pytest.fixture(scope="session")
def sample_dsn(server_url, tmp_path_factory):
    """A database with 30 days of sample data for demo-android and demo-iphone; the dash_ro URL."""
    out = tmp_path_factory.mktemp("sample")
    subprocess.run([sys.executable, str(REPO_DIR / "tools" / "sample_data.py")], cwd=out, check=True,
                   capture_output=True, env={**os.environ, "DRY": "1", "DAYS": "30", "TZ": TZ})
    name = f"t_mcp_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(server_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    owner = server_url.rsplit("/", 1)[0] + "/" + name
    receiver.setup_database(owner, TZ, receiver_conftest.RO_PASSWORD)
    for path in sorted((out / "sample-out").glob("*.json")):
        install = "demo-iphone" if path.name.startswith("demo-iphone") else "demo-android"
        status, body = receiver.ingest(owner, install, path.read_bytes())
        assert status == 200, (path.name, body)
    yield api.dsn(owner)
    with psycopg.connect(server_url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture(autouse=True)
def conninfo(request, monkeypatch):
    """Point the server at the sample database, for the tests that use it."""
    if "sample_dsn" in request.fixturenames:
        monkeypatch.setattr(server, "CONNINFO", request.getfixturevalue("sample_dsn"))
