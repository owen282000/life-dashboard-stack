# Ask your own AI (MCP)

The stack has an optional MCP server, so a model you choose can read your history and answer questions such as "how did my sleep change in the weeks I ran more?". [MCP](https://modelcontextprotocol.io) is the standard way for a model to call tools. This server gives it six read-only tools on the same queries as the [read-only API](api.md).

## Contents

- [What goes where](#what-goes-where)
- [The tools](#the-tools)
- [Start the server](#start-the-server)
- [Claude Desktop on the same machine](#claude-desktop-on-the-same-machine)
- [Any MCP client over HTTP](#any-mcp-client-over-http)
- [A local model](#a-local-model)
- [Security](#security)

## What goes where

The stack sends nothing to any AI by itself. The MCP server only answers a client that connects to it, and you pick the client and the model:

- **A local model** (Ollama with Open WebUI or LM Studio, say) keeps everything on your own hardware: the questions, the data the tools return and the answers.
- **A cloud model** such as Claude or ChatGPT gets your questions, every tool result it asks for and its own answers, on the provider's servers, under the provider's retention and training settings. That's true for Claude Desktop too: it runs the server on your computer, but the conversation, tool results included, goes to Anthropic.

The server reads the [`dash` views](views.md) as `dash_ro`, a database user that can only `SELECT` from them. There's no tool that writes and no tool that takes SQL, so a model can't change or delete anything, and it can't read the raw records or the payload ledger.

## The tools

Every tool returns a one-line summary for the model, then the data as compact JSON. Dates are `YYYY-MM-DD`, days are local days in the stack's `TZ`, and a night belongs to the date you woke up.

| Tool | Parameters | Returns |
|---|---|---|
| `list_phones` | none | The phones with their platforms, device and app version, the last sync, and the first and last day with data. Also the time zone and today's date. The model should call it first. |
| `daily_summary` | `from_date`, `to_date`, `phone` (optional) | One row per phone and day: steps, distance, calories, sleep, exercise minutes and workouts, screen time, resting heart rate and weight. At most 92 days and 200 rows. |
| `sleep_nights` | `from_date`, `to_date`, `phone` (optional) | One night per phone: bedtime, wake time, hours asleep and in bed, deep, light, REM and awake, and the overnight low heart rate. At most 92 days. |
| `workouts` | `from_date`, `to_date`, `phone` (optional) | Every workout with its activity, start, end and minutes. At most 92 days and 100 workouts. |
| `trend` | `metric`, `days` (7 to 365, default 28), `phone` (optional) | One metric over the last `days` complete days against the same number of days before: mean, lowest and highest day, days with data, and the change in percent. The metrics are those of [the API](api.md#metrics). |
| `screen_time` | `date`, `phone` (optional) | Screen time on one day per phone, with the top 10 apps. Android only. |

The tools are marked read-only in their MCP annotations. A wrong argument (a range over 92 days, a date that doesn't exist, a phone that never sent data) comes back as a tool error the model can read and correct.

A sample on the demo data, `trend` with `{"metric": "steps", "days": 28}`:

```
demo-android: steps mean 8,596 steps per day from 2026-09-18 to 2026-10-15 (28 days with data, lowest 5,280 on 2026-09-29, highest 14,084 on 2026-09-19), down 1.6% on the 28 days before; demo-iphone: steps mean 8,886 steps per day from 2026-09-18 to 2026-10-15 (28 days with data, lowest 4,860 on 2026-10-01, highest 12,014 on 2026-09-19), up 1.3% on the 28 days before.
```

```json
{"metric":"steps","days":28,"timezone":"Europe/Amsterdam","phones":[{"phone":"demo-android","metric":"steps","unit":"steps","period":{"from":"2026-09-18","to":"2026-10-15","days_with_data":28,"mean":8596.04,"min":5280.0,"min_day":"2026-09-29","max":14084.0,"max_day":"2026-09-19"},"previous":{"from":"2026-08-21","to":"2026-09-17","days_with_data":28,"mean":8736.11},"change":-140.07,"change_pct":-1.6}, ...]}
```

## Start the server

The server is the image `ghcr.io/owen282000/life-dashboard-mcp`, published with every release next to the other three, and the Compose service `mcp` in the `mcp` profile, so a plain `docker compose up` leaves it out. In `.env`, set a token of its own (at least 16 characters; the server refuses to start without one) and, if you like, another port:

```bash
echo "MCP_TOKEN=$(openssl rand -hex 32)" >> .env
docker compose --profile mcp up -d
```

It serves MCP over Streamable HTTP at `http://<address of this machine>:8090/mcp` (`MCP_PORT` in `.env` changes the port), and every request needs `Authorization: Bearer <MCP_TOKEN>`. `GET /health` answers without the token, for Docker's health check. To try it with the sample data, add `--profile demo`, as in [Try it without a phone](../README.md#try-it-without-a-phone).

To have every `docker compose` command include it, upgrades too, add `COMPOSE_PROFILES=mcp` to `.env`; otherwise keep `--profile mcp` on later commands, such as `docker compose --profile mcp pull` and `docker compose --profile mcp up -d` after you change `LIFEDASH_VERSION`, or the server keeps running the old version. From a clone, `docker-compose.build.yml` builds it as `life-dashboard-mcp:local`.

With the Helm chart, `mcp.enabled: true` runs it on port 8090, with `MCP_TOKEN` in the release's Secret; see [Kubernetes](kubernetes.md#the-api-and-the-mcp-server).

## Claude Desktop on the same machine

Claude Desktop can start the server itself and talk to it over stdio, without the HTTP port or the token. It runs a fresh container of the `mcp` service on the stack's network for each session. The stack must run on the same machine, and the image must be there: `docker compose --profile mcp pull mcp` fetches it once.

Open **Settings > Developer > Edit Config** in Claude Desktop and add, with the folder of your stack in place of the path:

```json
{
  "mcpServers": {
    "life-dashboard": {
      "command": "docker",
      "args": ["compose", "--project-directory", "/path/to/life-dashboard-stack", "--profile", "mcp",
               "run", "--rm", "--no-deps", "-T", "mcp", "python", "server.py", "--stdio"]
    }
  }
}
```

The path is the folder with your `docker-compose.yml` and `.env`. Restart Claude Desktop. The six tools show up under the tools button in a new chat. If Claude Desktop says it can't find `docker`, use its full path, such as `/usr/local/bin/docker`. If you started the stack with `-p <name>`, add `"-p", "<name>"` after `"compose"`.

Remember what goes where: the server runs on your machine, the conversation goes to Anthropic.

### Claude Desktop on another computer

Claude Desktop's config starts local commands. To reach the server over HTTP instead, a bridge such as the community project [mcp-remote](https://www.npmjs.com/package/mcp-remote) can run as that command (it needs Node.js):

```json
{
  "mcpServers": {
    "life-dashboard": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://192.168.1.20:8090/mcp", "--allow-http",
               "--header", "Authorization:${AUTH_HEADER}"],
      "env": {"AUTH_HEADER": "Bearer <your MCP_TOKEN>"}
    }
  }
}
```

`--allow-http` is needed for a plain `http://` address. Over plain HTTP the token and every answer cross your network readable, so keep this to a network you trust, or put TLS in front.

## Any MCP client over HTTP

Point a client that speaks Streamable HTTP at:

- URL: `http://<address of this machine>:8090/mcp`
- Header: `Authorization: Bearer <MCP_TOKEN>`

The server is stateless, so a restart doesn't break a client's session. With the official Python SDK (`pip install mcp==2.3.0`):

```python
import asyncio

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

URL = "http://localhost:8090/mcp"
TOKEN = "your MCP_TOKEN"


async def main():
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30)
    async with http, Client(streamable_http_client(URL, http_client=http)) as client:
        result = await client.call_tool("trend", {"metric": "sleep_h", "days": 28})
        print(result.content[0].text)


asyncio.run(main())
```

A client in a Docker container reaches the server under another address: on Docker Desktop `http://host.docker.internal:8090/mcp`; on Linux, attach the client's container to the stack's network (`<project>_default`) and use `http://mcp:8090/mcp`.

## A local model

A model on your own hardware keeps the whole conversation at home. [Ollama](https://ollama.com) runs the model, and a chat client makes the MCP connection; Ollama has no MCP client of its own. Pick a model with tool support and without a `-cloud` tag, such as `qwen3` (8B or 14B) or `gpt-oss:20b`. An 8B model wants about 16 GB of memory; 20 to 30B wants a 24 GB graphics card or a Mac with 32 GB or more.

- **[Open WebUI](https://docs.openwebui.com/features/extensibility/mcp/)**: under **Admin settings > Integrations > External tool servers**, add a server of the type MCP (Streamable HTTP) with the URL above and the token as bearer authentication. Then switch the tools on in a chat with your Ollama model.
- **[LM Studio](https://lmstudio.ai/docs/app/mcp)**: add the server to `mcp.json`:

  ```json
  {
    "mcpServers": {
      "life-dashboard": {
        "url": "http://localhost:8090/mcp",
        "headers": {"Authorization": "Bearer <your MCP_TOKEN>"}
      }
    }
  }
  ```

Small models do best with a direct question and a period: "Use trend for sleep_h over 28 days, then workouts for the same period, and tell me whether I slept longer on days I worked out." Six tools with fixed parameters are far easier for them than SQL.

## Security

- The token guards everything the dashboards show. Use a long random `MCP_TOKEN`, different from `API_TOKEN`, and treat it like a password. The server compares it in constant time and doesn't log it.
- Docker publishes the port on every network interface of the host. To keep the server to the machine itself, set `MCP_PORT=127.0.0.1:8090` in `.env`.
- Over plain HTTP the token and every answer cross the network readable. For anything beyond your own network, put TLS in front, or use a VPN.
- A web app's connector (such as a custom connector in a browser chat) calls the server from the provider's cloud, so the server would have to be reachable from the internet. That's not covered here.
- A model can only read: `dash_ro` has no write grants, its sessions are read-only, and a query stops after 15 seconds. Its password is `DASH_DB_PASSWORD` in `.env`.
- A model isn't a doctor. It can sum your steps correctly and still draw a wrong conclusion from them.
