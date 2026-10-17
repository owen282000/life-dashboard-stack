# Install with Docker Compose or Podman

This page sets up the stack with Docker Compose or Podman and connects your phones to it, step by step. The [README](../README.md#quick-start) has the short version. For a Kubernetes cluster, see [Kubernetes](kubernetes.md) instead.

## Contents

- [What you need](#what-you-need)
- [Install](#install)
- [On a NAS or in Portainer](#on-a-nas-or-in-portainer)
- [Podman](#podman)
- [Pair the app](#pair-the-app)
- [Connect a phone by hand](#connect-a-phone-by-hand)
- [Check that data arrives](#check-that-data-arrives)
- [Several phones](#several-phones)
- [Try it without a phone](#try-it-without-a-phone)
- [The dashboards](#the-dashboards)

## What you need

A container engine, and a phone that can reach the machine that runs the stack (the same network, or a VPN). There's nothing to clone or build: the images come from GHCR, and a release has the files to start them.

- **Docker**: Docker Engine or Docker Desktop with Compose v2, 2.20 or newer (`docker compose version`).
- **Podman**: Podman 4.7 or newer with a Compose provider: podman-compose 1.5 or newer, or Docker Compose 2.20 or newer. See [Podman](#podman).

Linux and macOS both work; on Windows, run it in WSL2.

On the phone: Life Dashboard Companion for [Android](https://github.com/owen282000/life-dashboard-companion-app) 1.23.0 or the [iPhone](https://github.com/owen282000/life-dashboard-companion-app-ios) version 1.6.0. Pairing with a QR code needs 1.16.0 or newer on Android, or 1.4.0 or newer on iPhone.

## Install

### With one line

```bash
curl -fsSL https://github.com/owen282000/life-dashboard-stack/releases/latest/download/install.sh | sh
```

The installer is a plain `sh` script, attached to every release, and it installs that release. It:

1. checks that Docker with Compose, or Podman with a Compose provider, is there and running, and says what to do if not;
2. downloads `docker-compose.yml` and `env.example` of its own release into `./life-dashboard`;
3. writes `.env` with a random `WEBHOOK_SECRET`, random passwords, and tokens for the parts you switch on. Only your user can read it;
4. proposes this machine's address on your network as `PUBLIC_URL` and its time zone as `TZ`, and asks whether to switch on the [API](api.md) and the [MCP server](mcp.md) (both off by default);
5. starts the stack, waits until the receiver and Grafana answer, and shows the [pairing code](#pair-the-app) in the terminal.

It never prints the Grafana password: it's `GRAFANA_PASSWORD` in `.env`, or add `--show-password` to see it once at the end. Through a pipe it asks its questions on the terminal; without one, as in CI, it asks nothing and uses the defaults and the options. Options go after `sh -s --`:

```bash
curl -fsSL https://github.com/owen282000/life-dashboard-stack/releases/latest/download/install.sh | sh -s -- --with-mcp --dir ~/life-dashboard
```

| Option | What it does |
|---|---|
| `--dir DIR` | The folder. Default `./life-dashboard`, or the current folder when it holds an install |
| `--public-url URL` | The address your phone reaches this machine on, instead of the one it finds |
| `--tz NAME` | The time zone, such as `Europe/Amsterdam`, instead of the machine's |
| `--with-api`, `--with-mcp` | Switch on the API or the MCP server, each with its own random token |
| `--label LABEL` | The phone label in the pairing code (`phone`) |
| `--engine docker\|podman` | Pick the engine when both are there (`LIFEDASH_ENGINE`) |
| `--upgrade` | Move an existing install to this version, see below |
| `--no-pull`, `--no-start` | Use the images on this machine; only write the files |
| `--env-only` | Only print a `.env`, see [On a NAS or in Portainer](#on-a-nas-or-in-portainer) |
| `-y`, `--yes` | Ask nothing |

`RECEIVER_PORT`, `GRAFANA_PORT` and `MCP_PORT` in the environment go into the new `.env`, and `sh install.sh --help` lists the rest. Exit codes: 0 done, 1 failed, 2 wrong usage, 3 Docker, Podman or Compose missing or not running, 4 started but not healthy in time.

**Run it again** on the same folder and it keeps `.env` and `docker-compose.yml` as they are: it only starts the stack and shows the pairing code. It never overwrites a `.env`. **To upgrade**, run the line of a newer release with `--upgrade`: it keeps `.env`, saves the old compose file as `docker-compose.yml.<old version>.bak`, puts the new one in place, pulls the images and restarts. It refuses to go back to an older version, names settings the new version has that your `.env` doesn't (they all have defaults), and warns when `LIFEDASH_VERSION` in `.env` pins other images. A `docker-compose.override.yml` in the folder is yours; the installer leaves it alone and Compose uses it.

**From a clone**, `sh install.sh` uses the clone's own `docker-compose.yml` and `.env.example` and, without `--dir`, installs in the clone itself, like [Build from source](operations.md#build-from-source).

Something wrong afterwards? The same line with `-s -- doctor` [checks the install](operations.md#troubleshooting).

### By hand

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

### The settings

The installer fills these in; by hand, set them in `.env`:

- `WEBHOOK_SECRET`: a long random string, for example the output of `openssl rand -hex 32`. You paste the same value into the app, or the pairing code carries it there.
- `TZ`: your time zone as an IANA name, such as `Europe/Amsterdam` or `America/New_York`. Days, nights and hours in the dashboards follow it. The receiver refuses to start with a name Postgres doesn't know.
- `GRAFANA_PASSWORD`: the password of Grafana's `admin` user.
- `GRAFANA_DB_PASSWORD`: the password of `grafana_ro`, the read-only database user Grafana connects with.
- `PUBLIC_URL`: the address your phone reaches this machine on, such as `http://192.168.1.10:8080`. It goes in the pairing code.

The other settings in [.env.example](../.env.example) switch on the [API](api.md) and the [MCP server](mcp.md), set the version of the images (see [Upgrading](operations.md#upgrading)) and control the pairing code. Each has a comment.

Ports 8080 or 3000 already taken? Change `RECEIVER_PORT` or `GRAFANA_PORT` in `.env` and use those ports below. After a change to `.env`, run `docker compose up -d` again.

The folder name is the Compose project name, which is part of the volume names that hold your data. Keep the folder, or set `COMPOSE_PROJECT_NAME` in `.env` when you move it.

## On a NAS or in Portainer

Synology Container Manager, Unraid, TrueNAS SCALE, Portainer, CasaOS and similar platforms start a compose file you paste into their web interface, so there's nothing to run a script on. The installer can still make the `.env` for you, on any computer, without Docker or Podman:

```bash
curl -fsSL https://github.com/owen282000/life-dashboard-stack/releases/latest/download/install.sh | sh -s -- --env-only --public-url http://192.168.1.20:8080 > lifedash.env
```

It writes a `.env` with a random secret, passwords and tokens, by the same rules as a normal install (`--with-api` and `--with-mcp` work here too), and prints the address of the `docker-compose.yml` of the same release on the terminal. `--output FILE` writes the file instead, readable only by you, and never over an existing one. Use the address of the NAS for `--public-url`; without it, `PUBLIC_URL` stays empty under a `# CHANGE ME` line, rather than an address that would end up in the pairing code wrongly. `TZ` is this computer's time zone unless you give `--tz`. Nothing is started, and nothing else is downloaded.

Then, in the platform:

- **Portainer**: add a stack, paste `docker-compose.yml` in the web editor, and under **Environment variables** switch to the advanced mode and paste the `.env`, or load it as a file.
- **Synology Container Manager**: create a project in a folder of its own, put `docker-compose.yml` and the `.env` in that folder (File Station), and build the project from the existing compose file. Compose reads the `.env` next to it.
- **Unraid, TrueNAS SCALE, CasaOS and others** with a compose or custom app option: paste the compose file, and the `.env` where the platform takes environment variables or an env file.

The receiver prints the pairing code in its log at start: open the receiver container's log in the platform's interface. Or open its console and run `python pairing.py <label>`, or open `http://<address of the NAS>:8080/pair` from your own network. Then continue with [Pair the app](#pair-the-app).

## Podman

The installer and the doctor work with Podman as with Docker: they find it by themselves, or take `--engine podman`. They run Compose through `podman compose` when it has a provider, otherwise through `podman-compose`. What to know:

- **Versions**: Podman 4.7 or newer. As the Compose provider, podman-compose 1.5 or newer (older ones don't wait for the database to be healthy, or run the healthchecks wrongly; `pipx install podman-compose` gets the latest), or Docker Compose 2.20 or newer, which talks to Podman's socket (`systemctl --user enable --now podman.socket`). Ubuntu and Debian ship an older podman-compose.
- **Healthchecks need systemd.** Podman runs them as systemd timers, which every normal Linux login and Podman's machine on macOS have. Grafana and the MCP server wait for the receiver to be healthy, so without systemd they never start.
- **Rootless**: the ports 8080, 3000 and 8090 are fine. A port below 1024 needs `sysctl net.ipv4.ip_unprivileged_port_start`, and the installer says so. Rootless containers don't come back after a reboot until you run `systemctl --user enable podman-restart.service` and `loginctl enable-linger $USER`; the doctor checks it.
- **Rootless port forwarding hides where a request came from**: the receiver sees every request as coming from inside, so `/pair` would answer anyone who can reach the port. The installer therefore sets `PAIRING_PAGE=0` under rootless Podman (and rootless Docker) and shows the code itself; by hand, set it too, see [Exposing it safely](operations.md#exposing-it-safely).
- **macOS**: the containers run in Podman's machine, a VM, and Podman forwards the published ports to the Mac. `PUBLIC_URL` is the Mac's own address on your network, which is what the installer proposes. Start the machine first: `podman machine start`.
- The images have full names (`ghcr.io/...`), so Podman needs no registry settings. The stack has no bind mounts, so SELinux needs no volume labels, and the database's volume works under a rootless user namespace.
- An MCP client in a container reaches the server on `host.containers.internal` where Docker Desktop uses `host.docker.internal`.
- Commands in these docs say `docker compose`; with Podman, use `podman compose` or `podman-compose`. In Claude Desktop's settings for the [MCP server](mcp.md), `podman` takes the place of `docker`.

## Pair the app

Like the Home Assistant integration, the receiver shows a QR code that the app scans, so you type neither the address nor the secret.

1. Show the code. With `PUBLIC_URL` set, it is in the receiver's log at every start: `docker compose logs receiver`. The log draws it for a dark terminal. Or open `http://<address of this machine>:8080/pair` in a browser on your own network; that page shows the same code, and without `PUBLIC_URL` it uses the address you opened it with.
2. In the app, open the **Webhook** card on the **Health** tab and tap **Scan a pairing code**, or point the phone's camera at the code.
3. Check the address the app shows and tap **Pair**. The app sends a test ping, which the receiver answers signed with the secret, so the iPhone app reports the pairing as confirmed (in the words it uses for Home Assistant). On Android, leave **Health** and **Screen Time** both on, so screen time and health data land under one **Phone**, and leave plain HTTP allowed for an `http://` address.
4. Tap **Sync Now**, on Android on both tabs, and continue with **Backfill** as in the steps under [Connect a phone by hand](#connect-a-phone-by-hand).

The code holds the webhook URL with the label `phone`, the secret and the name Life Dashboard Stack, in the pairing format of the Home Assistant integration. For a second phone, open `/pair?label=<label>` or use the **Another phone** field on the page: every phone needs its own label, see [Several phones](#several-phones). The log and the installer name the page's full address, such as `http://192.168.1.10:8080/pair?label=<label>`; with `PAIRING_PAGE=0` they point to `python pairing.py <label>` in the receiver's container instead. With an empty `WEBHOOK_SECRET` there is no code, because the apps refuse one without a secret; the log and the page suggest a new random one to put in `.env`.

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

The doctor checks the containers, the database, the read-only users and when each phone last sent data, and says what to fix: `curl -fsSL https://github.com/owen282000/life-dashboard-stack/releases/latest/download/install.sh | sh -s -- doctor` in the install folder, see [Troubleshooting](operations.md#troubleshooting).

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
