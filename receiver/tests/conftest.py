"""Test fixtures: a real Postgres 18 in Docker and a fresh database per test.

Set TEST_DATABASE_URL to a superuser URL of a server you already run (for example
postgresql://lifedash:lifedash@localhost:5432/postgres) to skip starting a container.
"""

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

RECEIVER_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = RECEIVER_DIR.parent
sys.path.insert(0, str(RECEIVER_DIR))

import receiver  # noqa: E402

POSTGRES_IMAGE = "postgres:18.6-alpine"
RO_PASSWORD = "test-ro-password"


def _compose_image() -> str:
    for line in (REPO_DIR / "docker-compose.yml").read_text().splitlines():
        if "image: postgres:" in line:
            return line.split("image:", 1)[1].strip()
    return POSTGRES_IMAGE


@pytest.fixture(scope="session")
def server_url():
    """A superuser URL ending in the maintenance database."""
    if os.environ.get("TEST_DATABASE_URL"):
        yield os.environ["TEST_DATABASE_URL"]
        return
    name = f"ld-a-test-db-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name,
         "--label", "com.docker.compose.project=ld-a-test",
         "-e", "POSTGRES_USER=lifedash", "-e", "POSTGRES_PASSWORD=lifedash",
         "-p", "127.0.0.1::5432", _compose_image(),
         "-c", "fsync=off", "-c", "synchronous_commit=off", "-c", "full_page_writes=off"],
        check=True, capture_output=True,
    )
    try:
        port = subprocess.run(["docker", "port", name, "5432/tcp"], check=True,
                              capture_output=True, text=True).stdout.split(":")[-1].strip()
        url = f"postgresql://lifedash:lifedash@127.0.0.1:{port}/postgres"
        deadline = time.time() + 60
        while True:
            try:
                with psycopg.connect(url, connect_timeout=2) as conn:
                    conn.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                if time.time() > deadline:
                    raise
                time.sleep(0.5)
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def _dsn_for(server: str, dbname: str) -> str:
    return server.rsplit("/", 1)[0] + "/" + dbname


@pytest.fixture
def make_db(server_url):
    """Create an empty database; optionally run the receiver's setup with a time zone."""
    created = []

    def factory(tz: str | None = "UTC", setup: bool = True) -> str:
        name = f"t_{uuid.uuid4().hex[:12]}"
        with psycopg.connect(server_url, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        created.append(name)
        dsn = _dsn_for(server_url, name)
        if setup:
            receiver.setup_database(dsn, tz, RO_PASSWORD)
        return dsn

    yield factory
    with psycopg.connect(server_url, autocommit=True) as conn:
        for name in created:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def db(make_db):
    return make_db("UTC")


def post(dsn: str, payload: dict, install: str = "phone") -> dict:
    status, body = receiver.ingest(dsn, install, json.dumps(payload).encode())
    assert status == 200, body
    return body


def query(dsn: str, text: str, params=None) -> list[tuple]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(text, params).fetchall()


def envelope(seq=None, ts="2026-10-04T08:00:00Z", source="health_connect", **records) -> dict:
    p = {"timestamp": ts, "app_version": "1.23.0", "source": source}
    if seq is not None:
        p["sequence"] = seq
    p.update(records)
    return p


@pytest.fixture
def http_server(db):
    """The receiver's HTTP handler on a free port, bound to the test database."""
    servers = []

    def start(secret: str = "s3cret"):
        handler = type("TestHandler", (receiver.Handler,), {"dsn": db, "secret": secret})
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        receiver.READY.set()
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    yield start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()
