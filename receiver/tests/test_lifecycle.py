"""Start and stop: a database that refuses the login fails fast, and SIGTERM lets a payload finish."""

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest

import receiver
from conftest import RECEIVER_DIR


def with_password(dsn, password):
    user_rest = dsn.split("://", 1)[1]
    user = user_rest.split(":", 1)[0]
    return f"postgresql://{user}:{password}@{user_rest.split('@', 1)[1]}"


def test_a_wrong_password_stops_the_start_at_once(db):
    started = time.time()
    with pytest.raises(receiver.SetupError, match="refused the connection: .*password authentication failed"):
        receiver.wait_for_db(with_password(db, "not-the-password"))
    assert time.time() - started < 10


def test_a_missing_database_stops_the_start_at_once(db):
    with pytest.raises(receiver.SetupError, match="refused the connection: .*does not exist"):
        receiver.wait_for_db(db.rsplit("/", 1)[0] + "/no_such_database")


def test_an_unreachable_database_logs_its_reason_once(capfd):
    with pytest.raises(receiver.SetupError, match="giving up. The last error: .+"):
        receiver.wait_for_db("postgresql://x:y@127.0.0.1:1/none", retries=3, pause=0)
    assert capfd.readouterr().err.count("waiting for the database:") == 1


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_sigterm_lets_a_payload_in_flight_finish(db):
    port = free_port()
    env = dict(os.environ, WEBHOOK_SECRET="", PORT=str(port), DATABASE_URL=db, TZ="UTC", PAIRING_LOG="0")
    proc = subprocess.Popen([sys.executable, str(RECEIVER_DIR / "receiver.py")], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    timer = threading.Timer(60, proc.kill)
    timer.start()
    try:
        for line in proc.stdout:
            if "database ready" in line:
                break
        body = json.dumps({"timestamp": "2026-10-04T08:00:00Z", "source": "health_connect", "sequence": 1,
                           "weight": [{"kilograms": 80.0, "time": "2026-10-04T07:00:00Z", "uuid": "w"}]}).encode()
        conn = socket.create_connection(("127.0.0.1", port))
        conn.sendall(b"POST /webhook/phone HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                     + f"Content-Length: {len(body)}\r\n\r\n".encode() + body[:10])
        time.sleep(0.5)
        proc.send_signal(signal.SIGTERM)
        time.sleep(0.5)
        conn.sendall(body[10:])
        reply = b""
        while chunk := conn.recv(4096):
            reply += chunk
        reply = reply.decode()
        conn.close()
        out, _ = proc.communicate(timeout=20)
    finally:
        timer.cancel()
        proc.kill()
    assert reply.startswith("HTTP/1.0 200") or reply.startswith("HTTP/1.1 200"), reply
    assert '"stored": 1' in reply
    assert proc.returncode == 0
    assert "receiver stopped" in out
