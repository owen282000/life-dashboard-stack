"""The MCP server: six read-only tools on the sample data, over the SDK's client, in
process, over streamable HTTP with the bearer token, and over stdio."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

import httpx2
import pytest
import uvicorn
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

import server
from conftest import MCP_DIR, REPO_DIR

TOKEN = "test-mcp-token-0123456789abcdef"
PYTHONPATH = str(REPO_DIR / "receiver")  # queries.py; the image has it next to server.py
TOOLS = {"list_phones", "daily_summary", "sleep_nights", "workouts", "trend", "screen_time"}


def call(name, args=None, target=None):
    async def run():
        async with Client(target or server.server) as client:
            return await client.call_tool(name, args or {})
    return asyncio.run(run())


def ok(result):
    """The structured data of a successful call, after checking its two text blocks."""
    assert not result.is_error, result.content
    summary, data = result.content
    assert "\n" not in summary.text and len(summary.text) < 1500
    assert json.loads(data.text) == result.structured_content
    return result.structured_content


def last_days(n):
    today = date.today()
    return (today - timedelta(days=n - 1)).isoformat(), today.isoformat()


# ------------------------------------------------------------------ tools

def test_six_read_only_tools_and_nothing_else():
    async def run():
        async with Client(server.server) as client:
            return (await client.list_tools()).tools
    tools = asyncio.run(run())
    assert {t.name for t in tools} == TOOLS
    for tool in tools:
        assert tool.annotations.read_only_hint is True and tool.annotations.destructive_hint is False
        assert len(tool.description) > 80
    trend = next(t for t in tools if t.name == "trend")
    assert "steps" in trend.input_schema["properties"]["metric"]["enum"]
    assert trend.input_schema["properties"]["days"]["maximum"] == 365


def test_list_phones(sample_dsn):
    result = call("list_phones")
    data = ok(result)
    assert data["timezone"] == "Europe/Amsterdam"
    assert [p["phone"] for p in data["phones"]] == ["demo-android", "demo-iphone"]
    assert result.content[0].text.startswith("2 phone(s), time zone Europe/Amsterdam")


def test_daily_summary(sample_dsn):
    start, end = last_days(14)
    result = call("daily_summary", {"from_date": start, "to_date": end, "phone": "demo-android"})
    data = ok(result)
    assert data["phone"] == "demo-android" and data["truncated"] is False
    assert 13 <= len(data["rows"]) <= 14
    row = data["rows"][0]
    assert {"day", "steps", "distance_km", "sleep_h", "exercise_min", "screen_min", "complete"} <= set(row)
    assert row["steps"] > 0
    assert "steps mean" in result.content[0].text


def test_sleep_nights_and_workouts(sample_dsn):
    start, end = last_days(14)
    data = ok(call("sleep_nights", {"from_date": start, "to_date": end}))
    phones = {r["phone"] for r in data["rows"]}
    assert phones == {"demo-android", "demo-iphone"}
    night = data["rows"][0]
    assert night["bedtime"].endswith(("+01:00", "+02:00")) and night["asleep_h"] > 3
    result = call("workouts", {"from_date": start, "to_date": end, "phone": "demo-iphone"})
    data = ok(result)
    assert data["rows"] and all(r["phone"] == "demo-iphone" for r in data["rows"])
    assert "workouts" in result.content[0].text


def test_trend(sample_dsn):
    result = call("trend", {"metric": "steps", "days": 7})
    data = ok(result)
    assert [t["phone"] for t in data["phones"]] == ["demo-android", "demo-iphone"]
    t = data["phones"][0]
    assert t["period"]["to"] == (date.today() - timedelta(days=1)).isoformat()
    assert t["period"]["days_with_data"] == 7 and t["previous"]["days_with_data"] == 7
    assert t["change_pct"] is not None
    assert "% on the 7 days before" in result.content[0].text
    data = ok(call("trend", {"metric": "exercise_min", "days": 14, "phone": "demo-android"}))
    assert data["phones"][0]["period"]["total"] > 0


def test_screen_time(sample_dsn):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    result = call("screen_time", {"date": yesterday})
    data = ok(result)
    (entry,) = data["phones"]
    assert entry["phone"] == "demo-android" and 0 < len(entry["top_apps"]) <= 10
    assert result.content[0].text.startswith(f"Screen time on {yesterday}: demo-android:")
    result = call("screen_time", {"date": yesterday, "phone": "demo-iphone"})
    assert ok(result)["phones"] == [] and "Android phones only" in result.content[0].text


@pytest.mark.parametrize("name, args, message", [
    ("daily_summary", {"from_date": "2026-02-30", "to_date": "2026-03-01"}, "not a valid date"),
    ("daily_summary", {"from_date": "2026-01-01", "to_date": "2026-06-01"}, "at most 92 days"),
    ("sleep_nights", {"from_date": "2026-03-02", "to_date": "2026-03-01"}, "on or before"),
    ("workouts", {"from_date": "2026-01-01", "to_date": "2026-01-02", "phone": "nobody"}, "no phone"),
    ("workouts", {"from_date": "2026-01-01", "to_date": "2026-01-02", "phone": "Not A Label"}, "label"),
    ("trend", {"metric": "cholesterol"}, "metric"),
    ("trend", {"metric": "steps", "days": 3}, "days"),
    ("screen_time", {"date": "yesterday"}, "date"),
])
def test_bad_arguments_are_tool_errors(sample_dsn, name, args, message):
    result = call(name, args)
    assert result.is_error
    assert message in result.content[0].text


def test_results_stay_small(sample_dsn):
    start, end = last_days(92)
    for name in ("daily_summary", "sleep_nights", "workouts"):
        result = call(name, {"from_date": start, "to_date": end})
        assert len(result.content[1].text) < 60_000, name


def test_database_down_is_a_tool_error(monkeypatch):
    monkeypatch.setattr(server, "CONNINFO", "postgresql://dash_ro:x@127.0.0.1:1/none?connect_timeout=1")
    result = call("list_phones")
    assert result.is_error and "not reachable" in result.content[0].text


# ------------------------------------------------------------------ transports

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def http_url(sample_dsn):
    port = _free_port()
    config = uvicorn.Config(server.http_app(TOKEN), host="127.0.0.1", port=port, log_level="warning")
    httpd = uvicorn.Server(config)
    thread = threading.Thread(target=httpd.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not httpd.started:
        assert time.time() < deadline, "uvicorn did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    httpd.should_exit = True
    thread.join(timeout=10)


def _post(url, token=None):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)


def test_http_needs_the_bearer_token(http_url):
    status, headers = _post(http_url + "/mcp")
    assert status == 401 and headers["www-authenticate"].startswith("Bearer")
    assert _post(http_url + "/mcp", token="wrong")[0] == 401
    assert _post(http_url + "/other", token=None)[0] == 401
    with urllib.request.urlopen(http_url + "/health", timeout=5) as r:
        assert r.status == 200


def test_http_client_calls_every_tool(http_url):
    async def run():
        http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30)
        async with http, Client(streamable_http_client(http_url + "/mcp", http_client=http)) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            start, end = last_days(7)
            results = {
                "list_phones": await client.call_tool("list_phones", {}),
                "daily_summary": await client.call_tool("daily_summary", {"from_date": start, "to_date": end}),
                "sleep_nights": await client.call_tool("sleep_nights", {"from_date": start, "to_date": end}),
                "workouts": await client.call_tool("workouts", {"from_date": start, "to_date": end}),
                "trend": await client.call_tool("trend", {"metric": "sleep_h", "days": 7}),
                "screen_time": await client.call_tool("screen_time", {"date": end}),
            }
            return names, results
    names, results = asyncio.run(run())
    assert names == TOOLS
    for name, result in results.items():
        assert ok(result), name


def test_stdio_transport(sample_dsn):
    params = StdioServerParameters(
        command=sys.executable, args=[str(MCP_DIR / "server.py"), "--stdio"],
        env={**os.environ, "DASH_DATABASE_URL": sample_dsn, "MCP_TOKEN": "", "PYTHONPATH": PYTHONPATH})
    result = call("list_phones", target=params)
    assert [p["phone"] for p in ok(result)["phones"]] == ["demo-android", "demo-iphone"]


def test_http_refuses_to_start_without_a_token():
    for token in ("", "too-short"):
        proc = subprocess.run([sys.executable, str(MCP_DIR / "server.py")], capture_output=True, text=True,
                              timeout=30, env={**os.environ, "MCP_TOKEN": token, "MCP_TRANSPORT": "",
                                   "PYTHONPATH": PYTHONPATH})
        assert proc.returncode == 2 and "MCP_TOKEN" in proc.stderr


def test_docs_name_every_tool():
    text = (REPO_DIR / "docs" / "mcp.md").read_text()
    for name in TOOLS:
        assert f"| `{name}` |" in text, f"{name} is not in docs/mcp.md"
