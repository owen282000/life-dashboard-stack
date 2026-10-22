<p align="center">
  <img src="docs/readme-icon.png" alt="Life Dashboard icon" width="128" height="128">
</p>

<h1 align="center">Life Dashboard Stack</h1>

<p align="center">
  <b>Your phone's health data and screen time in your own Postgres and Grafana.</b><br>
  The backend for the Life Dashboard Companion apps: six Grafana dashboards and an MCP server with eight read-only tools for Claude or a local model. Docker Compose or Kubernetes, no cloud.
</p>

<p align="center">
  <a href="https://github.com/owen282000/life-dashboard-stack/releases/latest"><img src="https://img.shields.io/github/v/release/owen282000/life-dashboard-stack" alt="Latest release"></a>
  <a href="docker-compose.yml"><img src="https://img.shields.io/badge/Docker%20Compose-3%20services-2496ED" alt="Docker Compose with three services"></a>
  <a href="docs/kubernetes.md"><img src="https://img.shields.io/badge/Helm-chart-0F1689" alt="Helm chart for Kubernetes"></a>
  <a href="https://artifacthub.io/packages/search?repo=life-dashboard"><img src="https://img.shields.io/endpoint?url=https://artifacthub.io/badge/repository/life-dashboard" alt="Artifact Hub"></a>
  <a href="https://www.postgresql.org/"><img src="https://img.shields.io/badge/Postgres-18-4169E1" alt="Postgres 18"></a>
  <a href="https://grafana.com/"><img src="https://img.shields.io/badge/Grafana-13.2-F46800" alt="Grafana 13.2"></a>
  <a href="docs/mcp.md"><img src="https://img.shields.io/badge/MCP-server-6E56CF" alt="MCP server"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue" alt="MIT license"></a>
</p>

<p align="center">
  <a href="#quick-start"><b>Quick&nbsp;start</b></a>
  &nbsp;·&nbsp;
  <a href="docs/install.md">Setup&nbsp;guide</a>
  &nbsp;·&nbsp;
  <a href="#dashboards">Dashboards</a>
  &nbsp;·&nbsp;
  <a href="docs/views.md">SQL&nbsp;views</a>
  &nbsp;·&nbsp;
  <a href="#ask-your-own-ai-mcp">MCP&nbsp;server</a>
  &nbsp;·&nbsp;
  <a href="docs/kubernetes.md">Kubernetes</a>
  &nbsp;·&nbsp;
  <a href="https://owen282000.com/life-dashboard/grafana/">Website</a>
</p>

<p align="center">
  <sub>Life Dashboard: <a href="https://github.com/owen282000/life-dashboard-companion-app">Android app</a> &nbsp;·&nbsp; <a href="https://github.com/owen282000/life-dashboard-companion-app-ios">iPhone app</a> &nbsp;·&nbsp; <a href="https://github.com/owen282000/life-dashboard-ha">Home Assistant integration</a> &nbsp;·&nbsp; <b>Grafana stack</b></sub>
</p>

<p align="center">
  <picture>
    <source media="(max-width: 600px)" srcset="docs/readme-hero-phone.png">
    <img src="docs/readme-hero.png" alt="The Life Dashboard overview in Grafana with sample data: tiles for steps today, last night's sleep, overnight low heart rate, weight, screen time and the last sync, a table of the last 7 days against the 28 before, steps per day with a goal line, sleep stages per night and an hourly heart rate band" width="900">
  </picture>
  <br><sub>Sample data</sub>
</p>

The [Life Dashboard Companion](https://github.com/owen282000/life-dashboard-companion-app) apps send Health Connect data and screen time per app from an Android phone, or Apple Health from an [iPhone](https://github.com/owen282000/life-dashboard-companion-app-ios), to a webhook. This repository is the other end. One command starts a receiver that checks every signature, Postgres with your records in plain SQL, Grafana with six dashboards, and an optional MCP server so the AI you choose can answer questions about your own history.

It's for people who want their records in a database they can query, next to Home Assistant or instead of it. With a local model nothing leaves your machine. It's a one-person project offered as-is and built to be changed: the views, the dashboards and the receiver are yours to adapt.

## What you get

- **Every record, checked and kept once.** The receiver checks the HMAC signature and stores every record in Postgres. A payload that arrives twice is applied once, a late one can't overwrite a newer one, and records you delete on the phone are deleted here too.
- **One table, plain SQL views.** All 33 record types of the Android app and the 28 of the iPhone app fit in one `records` table. The `dash` views turn them into days, nights, hours and apps in your time zone.
- **Six dashboards.** An overview as the home page, then Activity, Sleep, Heart and body, Screen time, and Data and sync. Grafana reads as a user that can only `SELECT`.
- **Ask your own AI.** An optional MCP server lets Claude, a local model in Ollama or any other MCP client answer questions such as "did I sleep better in the weeks I walked more?" from your own history, with read-only tools only. It stays off until you set a token.
- **Pairing with a QR code.** The receiver shows a code that the app scans, so you type neither the address nor the secret.
- **Every phone in your household.** Android phones and iPhones send to the same database, and each shows up as its own **Phone** in the dashboards.
- **Your data back out.** A read-only JSON API for scripts, widgets and home screens, off until you set a token.
- **Images, not a build.** Every part is an image on `ghcr.io` for amd64 and arm64 (so a Raspberry Pi with a 64-bit OS can run them), for Docker Compose or the Helm chart. Within a major version, nothing you build on changes.

<p align="center">
  <picture>
    <source media="(max-width: 600px)" srcset="docs/readme-screen-time-phone.png">
    <img src="docs/readme-screen-time.png" alt="The Screen time dashboard with sample data: tiles for today, yesterday, the daily average, the top app, apps used and the last use, minutes per day stacked by app, the top apps, a table per app, minutes per app per day as a heatmap, and how long before sleep the phone was last used" width="900">
  </picture>
  <br><sub>Sample data. Minutes per app, per day, in a table you own.</sub>
</p>

## How it works

The phone pushes signed JSON to you. There's no cloud account in between, so there's no login that expires, no token to refresh and no API that changes under you.

<p align="center">
  <picture>
    <source media="(max-width: 600px)" srcset="docs/how-it-works-phone.png">
    <img src="docs/how-it-works.png" alt="Diagram: the Android app (Health Connect and screen time per app) and the iPhone app (Apple Health) send HMAC-signed JSON to the receiver, a webhook that checks every signature. The receiver writes every record once to Postgres, with the dash views. Grafana reads them as grafana_ro and shows six dashboards. The optional read-only API and the optional MCP server read them as dash_ro, and the MCP server gives the AI model you choose eight read-only tools through any MCP client, with a local model or one in the cloud." width="900">
  </picture>
</p>

[views.md](docs/views.md) explains how records become days and nights, and [compatibility.md](docs/compatibility.md) lists what stays the same within a major version.

## Part of Life Dashboard

Life Dashboard is four projects, and you only install the parts you need. Put the app on each phone, then pick where the data goes. Both apps send the same payload, so an Android phone and an iPhone can share one Home Assistant or one stack.

<p align="center">
  <a href="https://github.com/owen282000/life-dashboard-companion-app"><img src="docs/family/android.png" alt="Android app: Health Connect and screen time" width="400"></a>
  <a href="https://github.com/owen282000/life-dashboard-companion-app-ios"><img src="docs/family/ios.png" alt="iPhone app: Apple Health" width="400"></a>
  <a href="https://github.com/owen282000/life-dashboard-ha"><img src="docs/family/ha.png" alt="Home Assistant integration: sensors and a year of history" width="400"></a>
  <picture><img src="docs/family/stack-here.png" alt="Grafana stack: Postgres and Grafana dashboards (this repository)" width="400"></picture>
</p>

The data can also go to an MQTT broker or a webhook of your own instead. Both apps have that built in, for n8n, Node-RED or a script.

## Quick start

You need Docker with Compose, or Podman, and a phone that can reach this machine (the same network, or a VPN). With a year of sample data for two phones (235,000 records), the stack used about 450 MB of memory while every dashboard loaded, 520 MB with the MCP server, and 530 MB on disk: 210 MB of data and 320 MB of Postgres write-ahead log. The images are about 650 MB to download, most of it Grafana. Pairing with a QR code needs the Android app 1.16.0 or the iPhone app 1.4.0 or newer.

<a id="pair-the-app"></a>

1. Run the installer. It puts the release files in `./life-dashboard`, writes `.env` with a random secret and passwords, starts the stack and shows the pairing code:

   ```bash
   curl -fsSL https://github.com/owen282000/life-dashboard-stack/releases/latest/download/install.sh | sh
   ```

   Prefer to read it first? Download it, read it, then run it:

   ```bash
   curl -fsSLO https://github.com/owen282000/life-dashboard-stack/releases/latest/download/install.sh
   less install.sh
   sh install.sh
   ```

2. In the app, open the **Webhook** card on the **Health** tab, tap **Scan a pairing code** and scan the code in your terminal. Then tap **Sync Now** (on Android, on both tabs) and **Backfill** the last 90 days.
3. Open Grafana on port 3000 and log in as `admin` with `GRAFANA_PASSWORD` from `life-dashboard/.env`. The overview is the home page.

Something wrong? The same line with `-s -- doctor` checks the install and says what to fix. The [setup guide](docs/install.md) has the installer's options, the [install by hand](docs/install.md#by-hand), [a NAS or Portainer](docs/install.md#on-a-nas-or-in-portainer), [Podman](docs/install.md#podman), the [pairing code](docs/install.md#pair-the-app), [setting a phone up by hand](docs/install.md#connect-a-phone-by-hand) and [several phones](docs/install.md#several-phones).

## Try it without a phone

The `demo` profile posts 60 days of made-up data for an Android phone and an iPhone through the real receiver. Run it in a folder of its own, under its own project name and ports, so it never touches your own data:

```bash
mkdir lifedash-demo && cd lifedash-demo
curl -fsSLO https://github.com/owen282000/life-dashboard-stack/releases/latest/download/docker-compose.yml
printf '%s\n' "WEBHOOK_SECRET=$(openssl rand -hex 32)" GRAFANA_PASSWORD=demo-admin \
  GRAFANA_DB_PASSWORD=demo-ro DASH_DB_PASSWORD=demo-dash RECEIVER_PORT=18080 GRAFANA_PORT=13000 > .env
docker compose -p lifedash-demo --profile demo up -d
```

Then open `http://localhost:13000` and log in as `admin` with the password `demo-admin`. A new stack starts only with a secret and passwords of its own, so the `.env` line gives the demo its own. [Try it without a phone](docs/install.md#try-it-without-a-phone) has the full steps and how to remove it again. To try the MCP server on the same made-up data, put `MCP_TOKEN=$(openssl rand -hex 32)` in front of the command and add `--profile mcp`, then connect your MCP client to `http://localhost:8090/mcp` with that token.

## Dashboards

Every dashboard has a **Phone** picker at the top, and every panel an info icon that says where its figure comes from.

| | Dashboard | What it answers |
|---|---|---|
| <img src="docs/screenshots/overview.png" width="240" alt="Overview dashboard"> | **Life Dashboard** (overview, home) | How were today and last night, and how does this week compare with the four before it? |
| <img src="docs/screenshots/activity.png" width="240" alt="Activity dashboard"> | **Activity** | Steps, distance and calories per day, steps by hour, and workout minutes per week next to the WHO guideline of 150. |
| <img src="docs/screenshots/sleep.png" width="240" alt="Sleep dashboard"> | **Sleep** | Last night as a hypnogram, stages per night, bedtime and wake time, and the overnight low heart rate. |
| <img src="docs/screenshots/heart-body.png" width="240" alt="Heart and body dashboard"> | **Heart and body** | Heart rate per hour, resting heart rate, HRV, SpO2, breathing rate and weight with its 7-day mean. Blood pressure, glucose, body composition and temperature sit in folded rows. |
| <img src="docs/screenshots/screen-time.png" width="240" alt="Screen time dashboard"> | **Screen time** | Minutes per day and per app, the top apps, and how long before sleep the phone was last used. Android only. |
| <img src="docs/screenshots/data.png" width="240" alt="Data and sync dashboard"> | **Data and sync** | When each phone last sent data, which types you have, how many records arrive per day, and why raw step records count a walk twice. |

Days are local days in your time zone, a missing day is a gap rather than a zero, and nothing is scored as good or bad. [The dashboards](docs/install.md#the-dashboards) lists the rules behind every panel and how to make a copy of your own.

## Ask your own AI (MCP)

[MCP](https://modelcontextprotocol.io) is the standard way for an AI model to call tools. The stack's optional MCP server gives the model you choose eight read-only tools on your history: `list_phones`, `data_catalog`, `sync_status`, `daily_summary`, `sleep_nights`, `workouts`, `trend` and `screen_time`. None of them writes or takes SQL, and the server reads as `dash_ro`, a user that can only read the `dash` views.

```bash
echo "MCP_TOKEN=$(openssl rand -hex 32)" >> .env
echo "COMPOSE_PROFILES=mcp" >> .env
docker compose up -d
```

Connect any MCP client to `http://<this machine>:8090/mcp` with the token, or let Claude Desktop start it over stdio. Then ask things like "how did my sleep change in the weeks I ran more?" or "which apps took most of my screen time last Sunday?".

The stack sends nothing to any AI by itself: with a local model everything stays on your hardware, and with a cloud model your questions and the results go to that provider. [mcp.md](docs/mcp.md) has the setup for Claude Desktop, Open WebUI, LM Studio and a local model through Ollama, and the Helm chart runs the same server with `mcp.enabled: true`.

## Write your own query

The `dash` views hold one row per day, night, hour or app, with local days already worked out:

```sql
-- Steps per day, counted once, for the phone labeled "pixel"
SELECT day, steps FROM dash.activity_days WHERE install = 'pixel' ORDER BY day DESC LIMIT 7;

-- Minutes per day in one app
SELECT day, minutes FROM dash.screen_apps WHERE app = 'YouTube' ORDER BY day DESC;

-- Last night's sleep
SELECT day, asleep_h, deep_h, rem_h FROM dash.sleep_nights ORDER BY day DESC LIMIT 1;
```

Run them with `docker compose exec db psql -U lifedash`, or in a Grafana panel of your own with the **LifeDashboard Postgres** data source. [views.md](docs/views.md) lists every view, column and unit, and where to keep views of your own. The raw records are in `records`, as sent: the Android app's [payload reference](https://github.com/owen282000/life-dashboard-companion-app/blob/main/docs/webhook.md) has every field, and the [iOS payload page](https://github.com/owen282000/life-dashboard-companion-app-ios/blob/main/docs/webhook.md) lists where an iPhone differs.

## Read your data back

Set `API_TOKEN` and the receiver also answers a read-only JSON API under `/api/`: the phones, totals per day, nights, workouts, screen time, one metric per day, and a trend. [api.md](docs/api.md) lists every endpoint with an example.

## Kubernetes

A Helm chart runs the same images on a cluster of your own, with Postgres through [CloudNativePG](https://cloudnative-pg.io) for replicas, failover and backups, as one bundled pod, or on a Postgres you already have. [kubernetes.md](docs/kubernetes.md) covers installing, pairing, routes, backups, upgrades and GitOps.

## Upgrading

Set `LIFEDASH_VERSION` in `.env` to a newer release, or to `1` to follow every 1.x release, then run `docker compose pull && docker compose up -d`. Coming from a version before 1.0.0, which ran Postgres 16? The database won't start until one script moves your data over: see [Upgrading from a version before 1.0.0](docs/operations.md#upgrading-from-a-version-before-100). A backup is one line: `docker compose exec -T db pg_dump -U lifedash -Fc lifedash > lifedash-$(date +%F).dump`. [Backups](docs/operations.md#backups) covers restoring.

Tested on Docker 29 with Postgres 18.6 and Grafana 13.2.4. Versions are pinned in the images, Dependabot proposes updates, and each release publishes them as new images.

## Exposing it safely

Set `WEBHOOK_SECRET`, so only your phones can send data. The receiver speaks plain HTTP, `/pair` shows the secret, and anyone who can open Grafana can read every record, so put a reverse proxy with TLS in front of the receiver only and keep Grafana on your own network or a VPN. [Exposing it safely](docs/operations.md#exposing-it-safely) covers every point, from the pairing page behind a proxy to the database passwords.

## Known gaps

Series the Android app sends per time window are stored, but only the heart rate panels read them so far, so leave **Data Resolution** on **Every record**, the default. [Known gaps](docs/operations.md#known-gaps) lists the rest.

## Documentation

**Get started:** [Setup guide](docs/install.md) · [Try it without a phone](docs/install.md#try-it-without-a-phone) · [Kubernetes](docs/kubernetes.md)

**Reference:** [SQL views](docs/views.md) · [API](docs/api.md) · [MCP server](docs/mcp.md) · [What a version keeps stable](docs/compatibility.md) · [Helm chart](charts/life-dashboard/README.md)

**Project:** [Upgrades, backups and security](docs/operations.md) · [Build from source](docs/operations.md#build-from-source) · [Changelog](CHANGELOG.md) · [Releasing](docs/releasing.md) · [Security policy](SECURITY.md)

<a id="build-from-source"></a>

## Help and contributing

Found a bug in the stack? Open an [issue](https://github.com/owen282000/life-dashboard-stack/issues) here. Questions about the apps go to the Android app's [Discussions](https://github.com/owen282000/life-dashboard-companion-app/discussions).

Pull requests are welcome. The receiver and the MCP server are Python, the views SQL, and [Build from source](docs/operations.md#build-from-source) builds the images from a clone and runs the tests.

## Support the project

Stars, shares and good bug reports all help. This is a one-person project, but your setup doesn't depend on that person: there's no server of mine to switch off, your history lives in your own database, and the code is MIT. If you want to chip in for the evenings that go into it, there's [Ko-fi](https://ko-fi.com/owen282000). The stack stays free and open source either way.

MIT licensed. See [LICENSE](LICENSE).

<p align="center">
  <sub>Made by <a href="https://github.com/owen282000">Owen Vogelaar</a> for the self-hosting and quantified self crowd.</sub>
</p>
