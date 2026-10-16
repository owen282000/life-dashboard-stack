"""Read-only MCP server for the Life Dashboard stack.

Six tools let a model read the history the stack keeps: the phones, totals per day,
nights, workouts, a trend for one metric, and screen time. They read the dash views
through queries.py, the same module the receiver's /api/ uses, as dash_ro: a database
user that can only SELECT from the dash schema. There is no tool that writes and no tool
that takes SQL.

Transports:
    streamable HTTP (the default): port 8090 (PORT), path /mcp, and every request needs
        "Authorization: Bearer <MCP_TOKEN>". The server refuses to start without a token
        of at least 16 characters. GET /health answers without one.
    stdio: python server.py --stdio, or MCP_TRANSPORT=stdio, for Claude Desktop and other
        clients that start the server themselves. No token: the client owns the process.

Database: libpq's PGHOST, PGPORT, PGDATABASE, PGUSER and PGPASSWORD, or a full
connection string in DASH_DATABASE_URL. docs/mcp.md has the setup.
"""

import hashlib
import hmac
import json
import logging
import os
import sys
from datetime import date as Date
from typing import Annotated, Literal

import psycopg
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

import queries

CONNINFO = os.environ.get("DASH_DATABASE_URL", "")
MIN_TOKEN_LENGTH = 16
MAX_DAYS = 92          # range of daily_summary, sleep_nights and workouts
MAX_ROWS = 200         # rows in one result
MAX_WORKOUTS = 100
TOP_APPS = 10

INSTRUCTIONS = """\
Read-only access to one household's health and screen time history from Life Dashboard: \
Health Connect or Apple Health data and Android screen time, sent by their phones to a \
server they run. Start with list_phones: it gives the phone labels, the time zone, today's \
date and the first and last day with data. Days are local days in that time zone; a night \
belongs to the date the person woke up; today is a running total and is marked \
complete=false. null or a missing day means nothing was recorded, not zero. HRV from \
Android (RMSSD) and from an iPhone (SDNN) are different measures and must not be mixed. \
You are reading personal health data: answer what was asked, and do not give medical \
diagnoses."""

server = MCPServer("life-dashboard", title="Life Dashboard (read-only)", instructions=INSTRUCTIONS,
                   version="1.2.0")

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                            open_world_hint=False)

Day = Annotated[str, Field(description="A date as YYYY-MM-DD", pattern=r"^\d{4}-\d{2}-\d{2}$")]
Phone = Annotated[str | None, Field(
    description="A phone label from list_phones, such as pixel. Leave it out for every phone.")]
Metric = Literal[tuple(queries.METRICS)]


# ------------------------------------------------------------------ helpers

def _result(summary: str, data: dict) -> CallToolResult:
    """A one-line summary for the model, then the data as compact JSON."""
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return CallToolResult(content=[TextContent(type="text", text=summary),
                                   TextContent(type="text", text=text)],
                          structured_content=data)


def _read(fn):
    """Run fn(reader) on a read-only connection and turn failures into tool errors."""
    try:
        with queries.open_reader(CONNINFO) as reader:
            return fn(reader)
    except (queries.BadRequest, queries.NotFound) as exc:
        raise ToolError(str(exc)) from None
    except psycopg.errors.QueryCanceled:
        raise ToolError("The query took too long. Ask for a shorter range.") from None
    except psycopg.Error as exc:
        print(f"mcp: database error: {type(exc).__name__}: {exc}".strip(), file=sys.stderr, flush=True)
        raise ToolError("The database is not reachable right now.") from None


def _range(from_date: str, to_date: str) -> tuple[Date, Date]:
    start, end = queries.parse_day(from_date, "from_date"), queries.parse_day(to_date, "to_date")
    queries.check_range(start, end, MAX_DAYS)
    return start, end


def _num(value, digits=0) -> str:
    return f"{value:,.{digits}f}"


def _mean(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _by_phone(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in rows:
        out.setdefault(row["phone"], []).append(row)
    return out


def _truncated(truncated: bool, shown: int) -> str:
    return f" Only the first {shown} rows are shown; ask for a shorter range." if truncated else ""


# ------------------------------------------------------------------ tools

@server.tool(annotations=READ_ONLY)
def list_phones() -> CallToolResult:
    """List the phones that send data to this stack: label, platforms (Android, iPhone,
    Screen time), device, app version, when it last sent data, and the first and last day
    with data. Also gives the time zone and today's date. Call this first."""
    def run(reader):
        return {"timezone": reader.tz, "today": reader.today().isoformat(), "phones": reader.phones()}
    data = _read(run)
    if not data["phones"]:
        return _result("No phone has sent data yet.", data)
    parts = [f"{p['phone']} ({', '.join(p['platforms'])}, data {p['first_day']} to {p['last_day']})"
             for p in data["phones"]]
    return _result(f"{len(parts)} phone(s), time zone {data['timezone']}, today {data['today']}: "
                   + "; ".join(parts) + ".", data)


@server.tool(annotations=READ_ONLY)
def daily_summary(from_date: Day, to_date: Day, phone: Phone = None) -> CallToolResult:
    """Totals per day, one row per phone and day: steps, distance_km, active_kcal,
    total_kcal, sleep_h and in_bed_h (the night that ended that morning), exercise_min and
    workouts, screen_min (Android only), resting_hr (bpm) and weight_kg. Steps, distance
    and calories are the phone's daily totals, counted once across phone and watch. null
    means nothing was recorded. complete=false marks today, a running total. Dates are
    inclusive, at most 92 days per call."""
    def run(reader):
        start, end = _range(from_date, to_date)
        p = reader.require_phone(phone)
        rows, truncated = reader.days(start, end, p, MAX_ROWS)
        return {"from": start.isoformat(), "to": end.isoformat(), "phone": p, "timezone": reader.tz,
                "rows": rows, "truncated": truncated}
    data = _read(run)
    lines = []
    for name, rows in _by_phone(data["rows"]).items():
        done = [r for r in rows if r["complete"]]
        bits = [f"{name}: {len(rows)} days"]
        steps = _mean([r["steps"] for r in done])
        if steps is not None:
            bits.append(f"steps mean {_num(steps)}/day")
        sleep = _mean([r["sleep_h"] for r in rows])
        if sleep is not None:
            bits.append(f"sleep mean {_num(sleep, 1)} h")
        exercise = sum(r["exercise_min"] or 0 for r in rows)
        if exercise:
            bits.append(f"exercise {_num(exercise)} min in total")
        screen = _mean([r["screen_min"] for r in done])
        if screen is not None:
            bits.append(f"screen time mean {_num(screen)} min/day")
        lines.append(", ".join(bits))
    summary = ("; ".join(lines) + " (means leave out today)." if lines
               else f"No data from {data['from']} to {data['to']}.")
    return _result(summary + _truncated(data["truncated"], MAX_ROWS), data)


@server.tool(annotations=READ_ONLY)
def sleep_nights(from_date: Day, to_date: Day, phone: Phone = None) -> CallToolResult:
    """One night per phone, on the date the person woke up: bedtime and wake_time (local
    time), asleep_h, in_bed_h, deep_h, light_h, rem_h and awake_h (stages are null when the
    night has none), hr_low (the lowest 10-minute average heart rate in the night, not the
    resting heart rate) and hr_avg. Dates are wake dates, inclusive, at most 92 days per
    call."""
    def run(reader):
        start, end = _range(from_date, to_date)
        p = reader.require_phone(phone)
        rows, truncated = reader.sleep(start, end, p, MAX_ROWS)
        return {"from": start.isoformat(), "to": end.isoformat(), "phone": p, "timezone": reader.tz,
                "rows": rows, "truncated": truncated}
    data = _read(run)
    lines = []
    for name, rows in _by_phone(data["rows"]).items():
        asleep = [r for r in rows if r["asleep_h"] is not None]
        line = f"{name}: {len(rows)} nights"
        if asleep:
            short = min(asleep, key=lambda r: r["asleep_h"])
            long_ = max(asleep, key=lambda r: r["asleep_h"])
            line += (f", asleep mean {_num(_mean([r['asleep_h'] for r in asleep]), 1)} h"
                     f" (shortest {_num(short['asleep_h'], 1)} h on {short['day']},"
                     f" longest {_num(long_['asleep_h'], 1)} h on {long_['day']})")
        deep = _mean([r["deep_h"] for r in rows])
        if deep is not None:
            line += f", deep mean {_num(deep, 1)} h"
        lines.append(line)
    summary = "; ".join(lines) + "." if lines else f"No nights from {data['from']} to {data['to']}."
    return _result(summary + _truncated(data["truncated"], MAX_ROWS), data)


@server.tool(annotations=READ_ONLY)
def workouts(from_date: Day, to_date: Day, phone: Phone = None) -> CallToolResult:
    """Every workout: day, start_time and end_time (local time), activity (such as running,
    walking or biking), minutes and the app that recorded it. A workout that a watch and a
    phone app both recorded is listed once. Dates are inclusive, at most 92 days and 100
    workouts per call."""
    def run(reader):
        start, end = _range(from_date, to_date)
        p = reader.require_phone(phone)
        rows, truncated = reader.workouts(start, end, p, MAX_WORKOUTS)
        return {"from": start.isoformat(), "to": end.isoformat(), "phone": p, "timezone": reader.tz,
                "rows": rows, "truncated": truncated}
    data = _read(run)
    rows = data["rows"]
    if not rows:
        return _result(f"No workouts from {data['from']} to {data['to']}.", data)
    kinds: dict[str, int] = {}
    for r in rows:
        kinds[r["activity"]] = kinds.get(r["activity"], 0) + 1
    top = ", ".join(f"{k} {n}" for k, n in sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0])))
    total = sum(r["minutes"] or 0 for r in rows)
    summary = f"{len(rows)} workouts, {_num(total)} minutes in total: {top}."
    return _result(summary + _truncated(data["truncated"], MAX_WORKOUTS), data)


@server.tool(annotations=READ_ONLY)
def trend(
    metric: Annotated[Metric, Field(description="What to measure: " + "; ".join(
        f"{k} ({v['unit']}): {v['about']}" for k, v in queries.METRICS.items()))],
    days: Annotated[int, Field(ge=7, le=365, description="Length of the period in days, 7 to 365")] = 28,
    phone: Phone = None,
) -> CallToolResult:
    """One metric over the last `days` complete days (up to yesterday) against the same
    number of days before: mean, lowest and highest day, days with data, and the change of
    the mean (absolute and in percent). For exercise_min a day without a workout counts as
    0 in the mean; for every other metric the mean is over days with data."""
    data = _read(lambda reader: {"metric": metric, "days": days, "timezone": reader.tz,
                                 "phones": reader.trend(metric, days, reader.require_phone(phone))})
    lines = []
    for t in data["phones"]:
        cur, prev = t["period"], t["previous"]
        if cur["mean"] is None:
            lines.append(f"{t['phone']}: no {metric} from {cur['from']} to {cur['to']}")
            continue
        digits = 0 if abs(cur["mean"]) >= 100 else 1
        line = (f"{t['phone']}: {metric} mean {_num(cur['mean'], digits)} {t['unit']} per day"
                f" from {cur['from']} to {cur['to']}")
        if "total" in cur:
            line += f" ({_num(cur['total'])} {t['unit']} in total on {cur['days_with_data']} days)"
        else:
            line += (f" ({cur['days_with_data']} days with data, lowest {_num(cur['min'], digits)}"
                     f" on {cur['min_day']}, highest {_num(cur['max'], digits)} on {cur['max_day']})")
        if t["change_pct"] is not None:
            direction = "up" if t["change"] >= 0 else "down"
            line += f", {direction} {_num(abs(t['change_pct']), 1)}% on the {days} days before"
        elif prev["mean"] is None:
            line += ", no data in the period before"
        lines.append(line)
    summary = "; ".join(lines) + "." if lines else f"No {metric} data."
    return _result(summary, data)


@server.tool(annotations=READ_ONLY)
def screen_time(date: Day, phone: Phone = None) -> CallToolResult:
    """Screen time on one day per phone: total_min, the number of apps, the last use, and
    the top 10 apps with their minutes. The day is the app's day, which runs from 04:00 to
    04:00 by default. Android phones only."""
    def run(reader):
        day = queries.parse_day(date, "date")
        p = reader.require_phone(phone)
        return {"date": day.isoformat(), "phone": p, "timezone": reader.tz,
                "phones": reader.screen_time(day, p, TOP_APPS)}
    data = _read(run)
    if not data["phones"]:
        return _result(f"No screen time on {data['date']}. Screen time comes from Android phones only.", data)
    lines = []
    for p in data["phones"]:
        apps = ", ".join(f"{a['app']} {a['minutes']} min" for a in p["top_apps"][:3])
        lines.append(f"{p['phone']}: {p['total_min']} min across {p['apps']} apps"
                     + (f", top {apps}" if apps else ""))
    return _result(f"Screen time on {data['date']}: " + "; ".join(lines) + ".", data)


# ------------------------------------------------------------------ HTTP

class BearerAuth:
    """ASGI wrapper: /health is open, everything else needs the bearer token."""

    def __init__(self, app, token: str):
        self.app = app
        self.digest = hashlib.sha256(token.encode()).digest()

    def _ok(self, scope) -> bool:
        header = dict(scope.get("headers") or []).get(b"authorization", b"").decode("latin-1")
        scheme, _, given = header.partition(" ")
        if scheme.lower() != "bearer":
            given = ""
        return hmac.compare_digest(hashlib.sha256(given.strip().encode()).digest(), self.digest)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            if scope["path"] == "/health":
                return await _send_json(send, 200, {"status": "ok"})
            if not self._ok(scope):
                return await _send_json(send, 401, {"error": "missing or wrong bearer token"},
                                        [(b"www-authenticate", b'Bearer realm="life-dashboard"')])
        await self.app(scope, receive, send)


async def _send_json(send, status: int, body: dict, headers=()) -> None:
    data = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(data)).encode()), *headers]})
    await send({"type": "http.response.body", "body": data})


def http_app(token: str):
    """The streamable HTTP app behind the bearer check. Stateless: no sessions to lose."""
    return BearerAuth(server.streamable_http_app(stateless_http=True, host="0.0.0.0"), token)


class _NoHealthLog(logging.Filter):
    """The healthcheck runs every few seconds; keep it out of the access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/health " not in record.getMessage()


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if "--stdio" in argv or os.environ.get("MCP_TRANSPORT", "").lower() == "stdio":
        server.run("stdio")
        return
    token = os.environ.get("MCP_TOKEN", "").strip()
    if len(token) < MIN_TOKEN_LENGTH:
        print(f"MCP server cannot start: set MCP_TOKEN to a random string of at least {MIN_TOKEN_LENGTH}"
              " characters, such as the output of openssl rand -hex 32.", file=sys.stderr, flush=True)
        sys.exit(2)
    import uvicorn

    port = int(os.environ.get("PORT", "8090"))
    print(f"MCP server on :{port}/mcp (streamable HTTP, bearer token required)", file=sys.stderr, flush=True)
    logging.getLogger("uvicorn.access").addFilter(_NoHealthLog())
    uvicorn.run(http_app(token), host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
