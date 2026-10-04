<p align="center">
  <img src="docs/readme-icon.png" alt="Life Dashboard icon" width="128" height="128">
</p>

<h1 align="center">Life Dashboard Stack</h1>

<h3 align="center">From phone to Grafana in 10 minutes</h3>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="docker-compose.yml"><img src="https://img.shields.io/badge/Docker%20Compose-ready-2496ED.svg" alt="Docker Compose"></a>
  <a href="https://www.postgresql.org/"><img src="https://img.shields.io/badge/Postgres-JSONB-4169E1.svg" alt="Postgres"></a>
  <a href="https://grafana.com/"><img src="https://img.shields.io/badge/Grafana-dashboard-F46800.svg" alt="Grafana"></a>
</p>

A ready-made, self-hosted receiving stack for
[Life Dashboard Companion](https://github.com/owen282000/life-dashboard-companion-app)
(Android) and its [iOS companion](https://github.com/owen282000/life-dashboard-companion-app-ios).

One `docker compose up` gives you:

- **Receiver**: a small HTTP endpoint that verifies the app's HMAC signature and upserts
  every record into Postgres, deduplicated on the record `uuid` (re-sent edits update in place)
- **Postgres**: one generic `records` table with a JSONB column, so all 33 data types work
  without schema changes
- **Grafana**: pre-provisioned dashboard with steps, heart rate, sleep, weight, and screen time

## Quickstart

```bash
git clone https://github.com/owen282000/life-dashboard-stack
cd life-dashboard-stack

# Optional but recommended: require signed payloads
export WEBHOOK_SECRET="pick-a-strong-secret"

docker compose up -d
```

Then in the app on your phone:

1. Add webhook URL: `http://<ip-of-this-machine>:8080/webhook`
2. Set the same value as HMAC signing secret (under Webhook Headers)
3. Tap **Sync Now**

Open Grafana at `http://<ip-of-this-machine>:3000` (login `admin` / `lifedash`, change it
via `GRAFANA_PASSWORD`). The "Life Dashboard" dashboard fills up as syncs arrive.

## How data is stored

Every record lands in one table:

```sql
records(type text, ts timestamptz, uuid text UNIQUE, source_app text,
        payload_source text, received_at timestamptz, data jsonb)
```

- `type` is the payload array name (`steps`, `heart_rate`, ...)
- `data` holds the full record JSON; query fields with `data->>'bpm'` etc.
- `uuid` deduplicates: the apps re-send records when the source app modifies them, and the
  upsert keeps the latest version
- `source_app` tells you which app wrote the record to Health Connect (phone vs watch)

Add your own panels with plain SQL; the payload format is documented in the app's
[README](https://github.com/owen282000/life-dashboard-companion-app#webhook-payload-format)
and machine-readable [JSON Schema](https://github.com/owen282000/life-dashboard-companion-app/blob/main/docs/webhook-schema.json).

## Security notes

- Set `WEBHOOK_SECRET`; without it the receiver accepts unsigned posts (fine on a trusted LAN)
- The receiver listens on port 8080 over plain HTTP; if you expose it beyond your LAN, put a
  TLS reverse proxy (Caddy, Traefik, nginx) in front and use `https://` in the app
- Change the Grafana admin password (`GRAFANA_PASSWORD`) and the Postgres credentials in
  `docker-compose.yml` if the stack is reachable by others

## License

MIT
