<p align="center">
  <img src="docs/readme-icon.png" alt="Life Dashboard icon" width="128" height="128">
</p>

<h1 align="center">Life Dashboard Stack</h1>

<p align="center">
  <b>Your phone's health data and screen time in your own Postgres and Grafana.</b><br>
  A Docker Compose backend for the Life Dashboard Companion apps, with six dashboards.
</p>

<p align="center">
  <a href="https://github.com/owen282000/life-dashboard-stack/releases/latest"><img src="https://img.shields.io/github/v/release/owen282000/life-dashboard-stack" alt="Latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue" alt="MIT license"></a>
  <a href="docker-compose.yml"><img src="https://img.shields.io/badge/Docker%20Compose-3%20services-2496ED" alt="Docker Compose with three services"></a>
  <a href="https://www.postgresql.org/"><img src="https://img.shields.io/badge/Postgres-18-4169E1" alt="Postgres 18"></a>
  <a href="https://grafana.com/"><img src="https://img.shields.io/badge/Grafana-13.2-F46800" alt="Grafana 13.2"></a>
</p>

<p align="center">
  <a href="#quick-start"><b>Quick start</b></a>
  &nbsp;·&nbsp;
  <a href="#try-it-without-a-phone">Try&nbsp;it&nbsp;without&nbsp;a&nbsp;phone</a>
  &nbsp;·&nbsp;
  <a href="#dashboards">Dashboards</a>
  &nbsp;·&nbsp;
  <a href="docs/views.md">SQL&nbsp;views</a>
  &nbsp;·&nbsp;
  <a href="https://github.com/owen282000/life-dashboard-companion-app">Android&nbsp;app</a>
  &nbsp;·&nbsp;
  <a href="https://github.com/owen282000/life-dashboard-companion-app-ios">iPhone&nbsp;app</a>
  &nbsp;·&nbsp;
  <a href="https://owen282000.com/life-dashboard/grafana/">Website</a>
</p>

<p align="center">
  <picture>
    <source media="(max-width: 600px)" srcset="docs/readme-hero-phone.png">
    <img src="docs/readme-hero.png" alt="The Life Dashboard overview in Grafana with sample data: tiles for steps today, last night's sleep, overnight low heart rate, weight, screen time and the last sync, a table of the last 7 days against the 28 before, steps per day with a goal line, sleep stages per night and an hourly heart rate band" width="900">
  </picture>
  <br><sub>Sample data</sub>
</p>

[Life Dashboard Companion](https://github.com/owen282000/life-dashboard-companion-app) sends Health Connect data and per-app screen time from an Android phone to a webhook, and its [iOS version](https://github.com/owen282000/life-dashboard-companion-app-ios) does the same with Apple Health. This repository is the other end. One `docker compose up` starts a receiver, a Postgres database and Grafana with six dashboards on your own machine.

The phone pushes signed JSON to you. There's no cloud account in between, so there's no login that expires, no token to refresh and no API that changes under you. An Android phone and an iPhone can send to the same database, and each shows up as its own **Phone** in the dashboards. Screen time arrives per app and per day, next to your sleep and steps.

It's for people who want their records in SQL rather than in Home Assistant. Treat it as a starting point you can change: it's offered as-is, not as a finished product.

<p align="center">
  <picture>
    <source media="(max-width: 600px)" srcset="docs/readme-screen-time-phone.png">
    <img src="docs/readme-screen-time.png" alt="The Screen time dashboard with sample data: tiles for today, yesterday, the daily average, the top app, apps used and the last use, minutes per day stacked by app, the top apps, a table per app, minutes per app per day as a heatmap, and how long before sleep the phone was last used" width="900">
  </picture>
  <br><sub>Sample data. Minutes per app, per day, in a table you own.</sub>
</p>

## Contents

- [What you get](#what-you-get)
- [Try it without a phone](#try-it-without-a-phone)
- [Part of Life Dashboard](#part-of-life-dashboard)
- [Quick start](#quick-start)
- [Dashboards](#dashboards)
- [Several phones](#several-phones)
- [Upgrading](#upgrading)
- [Upgrading from a version before 1.0.0](#upgrading-from-a-version-before-100)
- [Backups](#backups)
- [Exposing it safely](#exposing-it-safely)
- [Write your own query](#write-your-own-query)
- [Read your data back](#read-your-data-back)
- [Ask your own AI (MCP)](#ask-your-own-ai-mcp)
- [Kubernetes](#kubernetes)
- [Build from source](#build-from-source)
- [Known gaps](#known-gaps)

## What you get

- **Receiver.** A small Python service on port 8080. It checks the `X-Signature` HMAC, stores every record in Postgres and keeps each payload in a ledger. A payload that arrives twice is applied once. A late payload can't overwrite a newer one, because every write is compared on the payload's `sequence` and build time. Records you delete on the phone are deleted here too.
- **Postgres 18.** One `records` table with a JSONB column, so all 33 record types of the Android app and the 28 of the iPhone app fit without schema changes. On top of it sits a schema of plain SQL views, `dash`, that turns raw records into days, nights, hours and apps in your time zone. [docs/views.md](docs/views.md) documents them.
- **Grafana 13.2.** Six provisioned dashboards in a **Life Dashboard** folder, with the overview as the home page. Grafana reads the database as `grafana_ro`, a user that can only `SELECT`.

Each part is an image on `ghcr.io` for amd64 and arm64 (`life-dashboard-receiver`, `life-dashboard-db` and `life-dashboard-grafana`), so you don't need a clone or a build. Releases have version numbers, and within one major version nothing you build on changes: [docs/compatibility.md](docs/compatibility.md) lists what stays the same.

## Try it without a phone

The `demo` profile adds a one-off service that posts 60 days of made-up data for two phones, `demo-android` and `demo-iphone`, through the real receiver. All it needs is the compose file. Run it under its own project name and ports, so it never touches your own data:

```bash
mkdir lifedash-demo && cd lifedash-demo
curl -fsSLO https://github.com/owen282000/life-dashboard-stack/releases/latest/download/docker-compose.yml
RECEIVER_PORT=18080 GRAFANA_PORT=13000 docker compose -p lifedash-demo --profile demo up -d
```

Give it a minute, then open `http://localhost:13000` and log in as `admin` with the password `lifedash` (or your `GRAFANA_PASSWORD`, if the folder already has a `.env`). Pick a **Phone** at the top of a dashboard to switch between the two. When you're done, remove all of it, volumes included:

```bash
docker compose -p lifedash-demo --profile demo down -v
```

If you ran the demo profile in your real stack by mistake, this removes the sample phones and keeps everything else:

```bash
docker compose exec db psql -U lifedash -c "DELETE FROM records WHERE install LIKE 'demo-%'; DELETE FROM payloads WHERE install LIKE 'demo-%';"
```

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

You need Docker with Compose, and a phone that can reach the machine that runs the stack (the same network, or a VPN). There's nothing to clone or build: two files from the latest release are enough.

1. Make a folder and download `docker-compose.yml` and the example settings into it:

   ```bash
   mkdir life-dashboard && cd life-dashboard
   curl -fsSLO https://github.com/owen282000/life-dashboard-stack/releases/latest/download/docker-compose.yml
   curl -fsSL -o .env https://github.com/owen282000/life-dashboard-stack/releases/latest/download/env.example
   ```

2. Open `.env` and set the secret, your time zone and the two passwords, as described below.
3. Start it:

   ```bash
   docker compose up -d
   ```

The settings in `.env`:

- `WEBHOOK_SECRET`: a long random string, for example the output of `openssl rand -hex 32`. You paste the same value into the app.
- `TZ`: your time zone as an IANA name, such as `Europe/Amsterdam` or `America/New_York`. Days, nights and hours in the dashboards follow it. The receiver refuses to start with a name Postgres doesn't know.
- `GRAFANA_PASSWORD`: the password of Grafana's `admin` user.
- `GRAFANA_DB_PASSWORD`: the password of `grafana_ro`, the read-only database user Grafana connects with.
- `PUBLIC_URL`: the address your phone reaches this machine on, such as `http://192.168.1.10:8080`. It goes in the pairing code.

Ports 8080 or 3000 already taken? Change `RECEIVER_PORT` or `GRAFANA_PORT` in `.env` and use those ports below. After a change to `.env`, run `docker compose up -d` again.

The folder name is the Compose project name, which is part of the volume names that hold your data. Keep the folder, or set `COMPOSE_PROJECT_NAME` in `.env` when you move it.

### Pair the app

Like the Home Assistant integration, the receiver shows a QR code that the app scans, so you type neither the address nor the secret. You need Life Dashboard Companion 1.16.0 or newer on Android, or 1.4.0 or newer on iPhone.

1. Show the code. With `PUBLIC_URL` set, it is in the receiver's log at every start: `docker compose logs receiver`. The log draws it for a dark terminal. Or open `http://<address of this machine>:8080/pair` in a browser on your own network; that page shows the same code, and without `PUBLIC_URL` it uses the address you opened it with.
2. In the app, open the **Webhook** card on the **Health** tab and tap **Scan a pairing code**, or point the phone's camera at the code.
3. Check the address the app shows and tap **Pair**. The app sends a test ping. On Android, leave **Health** and **Screen Time** both on, so screen time and health data land under one **Phone**, and leave plain HTTP allowed for an `http://` address.
4. Tap **Sync Now**, on Android on both tabs, and continue with **Backfill** as in the steps below.

The code holds the webhook URL with the label `phone`, the secret and the name Life Dashboard Stack, in the pairing format of the Home Assistant integration. For a second phone, open `/pair?label=<label>` or use the **Another phone** field on the page: every phone needs its own label, see [Several phones](#several-phones). With an empty `WEBHOOK_SECRET` there is no code, because the apps refuse one without a secret; the log and the page suggest a new random one to put in `.env`.

An iPhone refuses plain `http://` to a name like `nas.lan`. Use the IP address or an `https://` address in `PUBLIC_URL`; the page and the log point it out.

The page and the log show the signing secret. [Exposing it safely](#exposing-it-safely) says who can open the page; `PAIRING_LOG=0` keeps the secret out of the log, and `PAIRING_PAGE=0` switches the page off.

### Connect a phone

To set it up by hand instead, pick a short label for the phone: lowercase letters, digits, `-` and `_`, at most 32 characters, such as `pixel` or `iphone`. The webhook URL is:

```
http://<address of this machine>:8080/webhook/<label>
```

The label is how the dashboards tell phones apart. Plain `/webhook` works too and stores the phone as `default`.

**Android** (Life Dashboard Companion 1.23.0):

1. On the **Health** tab, open **Webhook**. Add the URL under **Webhook URLs**, and paste the `WEBHOOK_SECRET` value into **HMAC signing secret (optional)**.
2. Under **Advanced**, switch on **Allow plain HTTP webhooks**. The app refuses `http://` addresses without it.
3. Tap **Test ping**, then **Save Changes**, then **Sync Now**.
4. On the **Screen Time** tab, add the same URL, with the same label, and the same secret. That tab has its own URL list and secret. With one label on both tabs, screen time and health data land under one **Phone**, which the screen time tile on the overview and the sleep panel on the Screen time dashboard need. Tap **Save Changes**, then **Sync Now**.
5. Back on the **Health** tab, tap **Backfill** and pick **90d** to fill the dashboards with the last three months. If the dialog shows **Grant history access**, tap it first: without it Health Connect only hands out the last 30 days. Screen time starts with the last 7 days and grows from there.

**iPhone** (Life Dashboard Companion for iOS 1.6.0):

1. On the **Health** tab, open **Webhook**. Type the URL in the **Webhook URL** field and tap the plus button next to it.
2. Paste the `WEBHOOK_SECRET` value under **HMAC Signing Secret**.
3. Tap **Test ping**, then **Sync Now**. iOS asks once for access to the local network; allow it. Plain HTTP to an IP address works without a setting.
4. Tap **Backfill** and pick **Last 90 days**. Keep the iPhone unlocked while it runs: iOS doesn't hand out Health data while it's locked.

Then open Grafana at `http://<address of this machine>:3000` and log in as `admin` with your `GRAFANA_PASSWORD`. The overview opens as the home page. If a panel stays empty, the **Data and sync** dashboard shows what arrived and from which phone.

To check that records arrive without Grafana:

```bash
docker compose exec db psql -U lifedash -c "SELECT install, type, count(*) FROM records GROUP BY 1, 2 ORDER BY 1, 2;"
```

## Dashboards

Every dashboard has a **Phone** picker at the top and a bar of links to the other five, which keeps the time range and the phone when you switch. Each panel has an info icon that says where its figure comes from and what to watch out for.

| | Dashboard | What it answers |
|---|---|---|
| <img src="docs/screenshots/overview.png" width="240" alt="Overview dashboard"> | **Life Dashboard** (overview, home) | How were today and last night, and how does this week compare with the four before it? |
| <img src="docs/screenshots/activity.png" width="240" alt="Activity dashboard"> | **Activity** | Steps, distance and calories per day, steps by hour, and workout minutes per week next to the WHO guideline of 150. |
| <img src="docs/screenshots/sleep.png" width="240" alt="Sleep dashboard"> | **Sleep** | Last night as a hypnogram, stages per night, bedtime and wake time, and the overnight low heart rate. |
| <img src="docs/screenshots/heart-body.png" width="240" alt="Heart and body dashboard"> | **Heart and body** | Heart rate per hour, resting heart rate, HRV, SpO2, breathing rate and weight with its 7-day mean. Blood pressure, glucose, body composition and temperature sit in folded rows. |
| <img src="docs/screenshots/screen-time.png" width="240" alt="Screen time dashboard"> | **Screen time** | Minutes per day and per app, the top apps, and how long before sleep the phone was last used. Android only. |
| <img src="docs/screenshots/data.png" width="240" alt="Data and sync dashboard"> | **Data and sync** | When each phone last sent data, which types you have, how many records arrive per day, and why raw step records count a walk twice. |

A few choices hold on every dashboard:

- Days are local days in your `TZ`. A night belongs to the morning you wake up. Today is a running total, so averages and comparisons leave it out, and its tiles say "so far".
- Steps, distance and calories come from the phone's daily totals, which count a walk once even when a phone and a watch both record it.
- HRV from an Android watch (RMSSD) and from an Apple Watch (SDNN) are different measures, so they're never mixed or averaged.
- Goals are dashed lines you can change at the top of the dashboard. Nothing is colored as good or bad, and there are no scores. The only red is the time since the last sync, on the overview and on **Data and sync**, when a phone has sent nothing for 24 hours.
- A missing day is a gap, not a zero.

The dashboards are built into the Grafana image, so Grafana won't save changes over them and an upgrade brings the new versions along. To change one, use **Save as** to make a copy; the copy is yours and an upgrade leaves it alone. To change the originals, edit the JSON in `grafana/dashboards/life-dashboard/` and [build from source](#build-from-source).

## Several phones

Give every phone its own label: `/webhook/pixel`, `/webhook/iphone`, `/webhook/partner`. On an Android phone, use the same label on the **Health** tab and on the **Screen Time** tab. Each label is a **Phone** in the dashboards, so two phones never overwrite each other's day totals.

Two phones that share one label do get in each other's way: the daily totals of the one that syncs last replace the other's. Screen time is still kept per phone model.

## Upgrading

The `docker-compose.yml` of a release starts the images of that release. To move to a newer one, set `LIFEDASH_VERSION` in `.env` to its number from the [releases](https://github.com/owen282000/life-dashboard-stack/releases), then:

```bash
docker compose pull && docker compose up -d
```

To follow every 1.x release, set `LIFEDASH_VERSION=1` once; from then on those two commands are the whole upgrade. A 1.x release never breaks what you built on the stack, see [docs/compatibility.md](docs/compatibility.md), and the [changelog](CHANGELOG.md) says what's new. When a release adds something to `docker-compose.yml` itself, its notes say so; download that file again then, and keep your `.env`.

## Upgrading from a version before 1.0.0

Versions before 1.0.0 were built from a clone and ran Postgres 16. Postgres 18 can't open that data directory, so the data moves over once with a dump and a restore. Until then the database refuses to start, and `docker compose logs db` shows:

```
Life Dashboard: your data is still in the Postgres 16 volume.
```

To upgrade in your clone:

```bash
git pull
tools/upgrade-postgres.sh life-dashboard-stack
```

The argument is the Compose project name: the folder name, unless you started the stack with `-p` or set `COMPOSE_PROJECT_NAME`. Without an argument the script uses the same default. The script checks that the new volume is still empty, stops the stack, dumps the old database to `backups/`, restores it into a new volume, compares the row count of every table, and only then marks the new volume as ready. It starts the stack again at the end. On its first start the receiver brings the old rows up to date: they become the phone `default`, and a day that older versions stored several times keeps only its latest figure. If an Android phone and an iPhone both sent to the old stack, they now share that label, so on days that both of them sent, the dashboards show the daily totals of one of them. Give each phone its own label from then on, as in [Several phones](#several-phones).

You can also leave the clone behind and use the release files from the [Quick start](#quick-start). Put them in a new folder, copy your settings from the old `.env`, and add `COMPOSE_PROJECT_NAME=life-dashboard-stack` (the clone's folder name), so Compose finds the volumes that hold your data. The db image has a copy of the upgrade script. In the new folder:

```bash
docker compose run --rm --no-deps -T --entrypoint cat db \
    /usr/local/share/life-dashboard/upgrade-postgres.sh > upgrade-postgres.sh
sh upgrade-postgres.sh
```

The old volume is never written to. To go back, check out the last version on Postgres 16 in the clone and start it. Keep `--build`, so the receiver of that version is built and used:

```bash
git checkout f4e9795
docker compose up -d --build
```

Once you're happy with the new version, stop the stack with `docker compose down` (without `-v`) and remove the old volume with `docker volume rm life-dashboard-stack_db-data`. The next start creates an empty one in its place, and the guard lets an empty volume through. To start with an empty database instead and leave the old volume as it is, set `IGNORE_OLD_VOLUME=1` in `.env`.

## Backups

A year of health data deserves a copy somewhere else. One line writes a dump:

```bash
docker compose exec -T db pg_dump -U lifedash -Fc lifedash > lifedash-$(date +%F).dump
```

And one line restores it into a fresh stack:

```bash
docker compose exec -T db pg_restore -U lifedash -d lifedash --clean --if-exists < lifedash-2026-10-04.dump
```

Restart the receiver afterward (`docker compose restart receiver`) so it rebuilds the views and the `grafana_ro` grants.

## Exposing it safely

- Set `WEBHOOK_SECRET`. With it, a request with a missing or wrong signature gets a 401 and nothing is stored; the app keeps that payload and sends it again once the secrets match. Without it, the receiver stores unsigned posts from anyone who can reach it, and says so in its log at every start.
- `/pair` shows the signing secret, and with it anyone can send data to the receiver or delete records in it. The receiver answers it only for a request from a private or loopback address (your own network, a VPN such as Tailscale, the machine itself) that carries no proxy header such as `X-Forwarded-For`, `Forwarded` or `X-Real-IP`; everything else gets a 404. Those headers are never believed, only taken as a sign that a proxy sits in between. Two setups make a request from the internet look like one from your network: a reverse proxy that sends no forwarding header, and Docker's own port forwarding where it hides the source address (Docker Desktop, rootless Docker). With a port open to the internet in such a setup, block `/pair` there or set `PAIRING_PAGE=0`. To pair through a reverse proxy anyway, set `PAIRING_TOKEN` to a long random string and open `/pair?token=<it>`; the token ends up in browser history and proxy logs, so remove it once the phones are paired. The receiver's own log hides it.
- The receiver speaks plain HTTP, and Docker publishes the receiver and Grafana on every network interface of the host. To reach the receiver from outside, put a reverse proxy with TLS (Caddy, Traefik, nginx) in front of the receiver only, use the `https://` address in the app, and allow request bodies of 32 MB. The receiver turns away anything larger with a 413.
- Keep Grafana on your own network or behind a VPN. Anyone who can view a dashboard can read every health record through the data source, so don't switch on anonymous access for a Grafana that others can reach.
- Grafana only reads `GRAFANA_PASSWORD` when it creates its admin user on the first start; change the password in Grafana after that. `GRAFANA_DB_PASSWORD` is read at every start, by the receiver and by Grafana.
- `grafana_ro` can read the `records` and `buckets` tables and the `dash` views, and nothing else. It can't write or read the payload ledger, and its queries stop after 30 seconds.
- Postgres isn't published on a port. Its own user and password are `lifedash`, set in `docker-compose.yml` for the database and the receiver. If other people can reach the Docker host, change both before the first start.

## Write your own query

The `dash` views are the easiest place to start. They hold one row per day, night, hour or app, with local days already worked out:

```sql
-- Steps per day, counted once, for the phone labeled "pixel"
SELECT day, steps FROM dash.activity_days WHERE install = 'pixel' ORDER BY day DESC LIMIT 7;

-- Minutes per day in one app
SELECT day, minutes FROM dash.screen_apps WHERE app = 'YouTube' ORDER BY day DESC;

-- Last night's sleep
SELECT day, asleep_h, deep_h, rem_h FROM dash.sleep_nights ORDER BY day DESC LIMIT 1;
```

Run them with `docker compose exec db psql -U lifedash`, or in a Grafana panel of your own with the **LifeDashboard Postgres** data source. [docs/views.md](docs/views.md) lists every view, column and unit, and the rules behind them.

The receiver drops and rebuilds the `dash` schema at every start, so keep views of your own in the `public` schema. The raw records stay in `records`, one row per record with the whole record as JSON in `data`. Every field of every type is in the Android app's [payload reference](https://github.com/owen282000/life-dashboard-companion-app/blob/main/docs/webhook.md), and the [iOS payload page](https://github.com/owen282000/life-dashboard-companion-app-ios/blob/main/docs/webhook.md) lists where an iPhone differs.

## Read your data back

The receiver can also answer: a read-only JSON API under `/api/`, for a script, a widget or a spreadsheet of your own. It's off until you set `API_TOKEN` in `.env` to a long random string (`openssl rand -hex 32`) and restart. Then:

```bash
curl -H "Authorization: Bearer $API_TOKEN" "http://localhost:8080/api/days?from=2026-10-01&to=2026-10-07&phone=pixel"
```

There are endpoints for the phones, totals per day, nights, workouts, screen time, one metric per day, and a trend against the period before. Days are local days in your `TZ`, as in the dashboards. The API reads only the `dash` views, as `dash_ro`, a database user that can't write and can't see the raw payloads. [docs/api.md](docs/api.md) lists every endpoint with an example.

The API shares the receiver's port. If a reverse proxy makes the receiver reachable from the internet, the API is reachable there too, behind its token; forward only `/webhook` if you don't need it outside.

## Ask your own AI (MCP)

An optional MCP server, a fourth image, lets a model you choose read your history and answer questions like "did I sleep longer in the weeks I ran more?". It has six read-only tools (phones, days, nights, workouts, a trend, screen time) on the same queries as the API, and no tool that writes or takes SQL. Set `MCP_TOKEN` in `.env` and start it with its profile:

```bash
docker compose --profile mcp up -d
```

It serves Streamable HTTP at `http://<address of this machine>:8090/mcp` with the token as a bearer token, for Open WebUI, LM Studio or any MCP client, and stdio for Claude Desktop on the same machine. [docs/mcp.md](docs/mcp.md) has the setup for each, including a local model through Ollama.

The stack sends nothing to any AI by itself. With a local model everything stays on your hardware. With a cloud model, Claude Desktop included, your questions and every result the model asks for go to that provider.

## Kubernetes

For a Kubernetes cluster of your own there's a Helm chart, `charts/life-dashboard`, with the same four images. It runs Postgres through [CloudNativePG](https://cloudnative-pg.io), with replicas, failover and backups to object storage, or as one bundled pod, or uses a Postgres you already have. Secrets are generated once and kept, the pods meet the `restricted` Pod Security Standard, and the receiver can get an Ingress or a Gateway API route that opens only `/webhook`:

```bash
kubectl apply --server-side -f https://github.com/cloudnative-pg/cloudnative-pg/releases/download/v1.30.1/cnpg-1.30.1.yaml
helm install lifedash charts/life-dashboard -n life-dashboard --create-namespace \
  --set timezone=Europe/Amsterdam --set receiver.publicUrl=https://lifedash.example.com --wait --timeout 10m
helm test lifedash -n life-dashboard --logs
```

[docs/kubernetes.md](docs/kubernetes.md) covers the three Postgres modes, secrets, pairing, routes and TLS, backups and restores, upgrades, what an uninstall keeps, and Argo CD and Flux.

## Build from source

To change the receiver, the views or the dashboards, clone the repository and let Compose build the images from it:

```bash
git clone https://github.com/owen282000/life-dashboard-stack
cd life-dashboard-stack
cp .env.example .env
```

In `.env`, set `WEBHOOK_SECRET` and the rest as in the [Quick start](#quick-start), and remove the `#` in front of `COMPOSE_FILE=docker-compose.yml:docker-compose.build.yml`. Every `docker compose` command in that folder then builds the images from your clone, tagged `:local`, instead of pulling them:

```bash
docker compose up -d --build
```

Run the same command after every change. Without the `COMPOSE_FILE` line, `docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build` does the same. The tests of the receiver and the MCP server start their own Postgres in Docker:

```bash
pip install -r receiver/requirements-dev.txt -r mcp/requirements-dev.txt
python -m pytest -q receiver/tests
python -m pytest -q mcp/tests
```

## Known gaps

- Series the Android app sends per time window (**Data Resolution** set to anything but **Every record**) are stored in a `buckets` table, but only the heart rate panels read them so far. For steps, HRV, SpO2 and the other series, leave **Data Resolution** on **Every record**, the default. Steps, distance and calories per day come from the daily totals and aren't affected.
- A backfill doesn't remove records that the phone no longer has. Deletions are applied when the app reports them, which it does for changes since the last sync.
- When you delete a single sleep stage in Apple Health, the stored night keeps it until the whole night is sent again.
- Days follow the one `TZ` of the stack. A record made while you traveled lands on the day it was in the stack's time zone, not on the day it was where you were.
- Two phones on one label overwrite each other's daily totals. Give each phone its own label, see [Several phones](#several-phones).

## Works with

- [Life Dashboard Companion](https://github.com/owen282000/life-dashboard-companion-app) for Android 1.23.0: Health Connect and Screen Time payloads.
- [Life Dashboard Companion for iOS](https://github.com/owen282000/life-dashboard-companion-app-ios) 1.6.0: Apple Health payloads.

Tested on Docker 29 with Postgres 18.6 and Grafana 13.2.3. The versions are pinned in the Dockerfiles of the images and in the `requirements.txt` of the receiver and the MCP server, and Dependabot proposes updates. Each release publishes them as new images.

## Help

Found a bug in the stack? Open an [issue](https://github.com/owen282000/life-dashboard-stack/issues) here. Questions about the apps go to the Android app's [Discussions](https://github.com/owen282000/life-dashboard-companion-app/discussions).

## License

MIT. See [LICENSE](LICENSE).
