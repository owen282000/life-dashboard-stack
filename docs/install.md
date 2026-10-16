# Install with Docker Compose

This page sets up the stack with Docker Compose and connects your phones to it, step by step. The [README](../README.md#quick-start) has the short version. For a Kubernetes cluster, see [Kubernetes](kubernetes.md) instead.

## Contents

- [What you need](#what-you-need)
- [Install](#install)
- [Pair the app](#pair-the-app)
- [Connect a phone by hand](#connect-a-phone-by-hand)
- [Check that data arrives](#check-that-data-arrives)
- [Several phones](#several-phones)
- [Try it without a phone](#try-it-without-a-phone)
- [The dashboards](#the-dashboards)

## What you need

Docker with Compose, and a phone that can reach the machine that runs the stack (the same network, or a VPN). There's nothing to clone or build: two files from the latest release are enough.

On the phone: Life Dashboard Companion for [Android](https://github.com/owen282000/life-dashboard-companion-app) 1.23.0 or the [iPhone](https://github.com/owen282000/life-dashboard-companion-app-ios) version 1.6.0. Pairing with a QR code needs 1.16.0 or newer on Android, or 1.4.0 or newer on iPhone.

## Install

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

- `WEBHOOK_SECRET`: a long random string, for example the output of `openssl rand -hex 32`. You paste the same value into the app, or the pairing code carries it there.
- `TZ`: your time zone as an IANA name, such as `Europe/Amsterdam` or `America/New_York`. Days, nights and hours in the dashboards follow it. The receiver refuses to start with a name Postgres doesn't know.
- `GRAFANA_PASSWORD`: the password of Grafana's `admin` user.
- `GRAFANA_DB_PASSWORD`: the password of `grafana_ro`, the read-only database user Grafana connects with.
- `PUBLIC_URL`: the address your phone reaches this machine on, such as `http://192.168.1.10:8080`. It goes in the pairing code.

The other settings in [.env.example](../.env.example) switch on the [API](api.md) and the [MCP server](mcp.md), set the version of the images (see [Upgrading](operations.md#upgrading)) and control the pairing code. Each has a comment.

Ports 8080 or 3000 already taken? Change `RECEIVER_PORT` or `GRAFANA_PORT` in `.env` and use those ports below. After a change to `.env`, run `docker compose up -d` again.

The folder name is the Compose project name, which is part of the volume names that hold your data. Keep the folder, or set `COMPOSE_PROJECT_NAME` in `.env` when you move it.

## Pair the app

Like the Home Assistant integration, the receiver shows a QR code that the app scans, so you type neither the address nor the secret.

1. Show the code. With `PUBLIC_URL` set, it is in the receiver's log at every start: `docker compose logs receiver`. The log draws it for a dark terminal. Or open `http://<address of this machine>:8080/pair` in a browser on your own network; that page shows the same code, and without `PUBLIC_URL` it uses the address you opened it with.
2. In the app, open the **Webhook** card on the **Health** tab and tap **Scan a pairing code**, or point the phone's camera at the code.
3. Check the address the app shows and tap **Pair**. The app sends a test ping. On Android, leave **Health** and **Screen Time** both on, so screen time and health data land under one **Phone**, and leave plain HTTP allowed for an `http://` address.
4. Tap **Sync Now**, on Android on both tabs, and continue with **Backfill** as in the steps under [Connect a phone by hand](#connect-a-phone-by-hand).

The code holds the webhook URL with the label `phone`, the secret and the name Life Dashboard Stack, in the pairing format of the Home Assistant integration. For a second phone, open `/pair?label=<label>` or use the **Another phone** field on the page: every phone needs its own label, see [Several phones](#several-phones). With an empty `WEBHOOK_SECRET` there is no code, because the apps refuse one without a secret; the log and the page suggest a new random one to put in `.env`.

An iPhone refuses plain `http://` to a name like `nas.lan`. Use the IP address or an `https://` address in `PUBLIC_URL`; the page and the log point it out.

The page and the log show the signing secret. [Exposing it safely](operations.md#exposing-it-safely) says who can open the page; `PAIRING_LOG=0` keeps the secret out of the log, and `PAIRING_PAGE=0` switches the page off.

## Connect a phone by hand

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

## Check that data arrives

Open Grafana at `http://<address of this machine>:3000` and log in as `admin` with your `GRAFANA_PASSWORD`. The overview opens as the home page. If a panel stays empty, the **Data and sync** dashboard shows what arrived and from which phone.

To check that records arrive without Grafana:

```bash
docker compose exec db psql -U lifedash -c "SELECT install, type, count(*) FROM records GROUP BY 1, 2 ORDER BY 1, 2;"
```

## Several phones

Give every phone its own label: `/webhook/pixel`, `/webhook/iphone`, `/webhook/partner`. On an Android phone, use the same label on the **Health** tab and on the **Screen Time** tab. Each label is a **Phone** in the dashboards, so two phones never overwrite each other's day totals.

Two phones that share one label do get in each other's way: the daily totals of the one that syncs last replace the other's. Screen time is still kept per phone model.

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

## The dashboards

The six dashboards are provisioned in a **Life Dashboard** folder in Grafana, with the overview as the home page. Every dashboard has a **Phone** picker at the top and a bar of links to the other five, which keeps the time range and the phone when you switch. Each panel has an info icon that says where its figure comes from and what to watch out for.

A few choices hold on every dashboard:

- Days are local days in your `TZ`. A night belongs to the morning you wake up. Today is a running total, so averages and comparisons leave it out, and its tiles say "so far".
- Steps, distance and calories come from the phone's daily totals, which count a walk once even when a phone and a watch both record it.
- HRV from an Android watch (RMSSD) and from an Apple Watch (SDNN) are different measures, so they're never mixed or averaged.
- Goals are dashed lines you can change at the top of the dashboard. Nothing is colored as good or bad, and there are no scores. The only red is the time since the last sync, on the overview and on **Data and sync**, when a phone has sent nothing for 24 hours.
- A missing day is a gap, not a zero.

The dashboards are built into the Grafana image, so Grafana won't save changes over them and an upgrade brings the new versions along. To change one, use **Save as** to make a copy; the copy is yours and an upgrade leaves it alone. To change the originals, edit the JSON in `grafana/dashboards/life-dashboard/` and [build from source](operations.md#build-from-source).
