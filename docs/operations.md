# Running the stack

How to keep a Docker Compose install up to date, find what's wrong with it, back it up and reach it safely, how to build it from source, and what it doesn't do yet. Installing it is in [install.md](install.md); on Kubernetes, [kubernetes.md](kubernetes.md) covers the same ground for the Helm chart.

## Contents

- [Upgrading](#upgrading)
- [Upgrading from a version before 1.0.0](#upgrading-from-a-version-before-100)
- [Troubleshooting](#troubleshooting)
- [Backups](#backups)
- [Exposing it safely](#exposing-it-safely)
- [Another data source in Grafana](#another-data-source-in-grafana)
- [Build from source](#build-from-source)
- [Known gaps](#known-gaps)

## Upgrading

The `docker-compose.yml` of a release starts the images of that release. To move to a newer one, set `LIFEDASH_VERSION` in `.env` to its number from the [releases](https://github.com/owen282000/life-dashboard-stack/releases), then:

```bash
docker compose pull && docker compose up -d
```

Installed with the [one-line installer](install.md#with-one-line)? Run the line of the new release with `--upgrade` in the install folder instead: it fetches that release's `docker-compose.yml`, keeps `.env`, pulls and restarts.

```bash
curl -fsSL https://github.com/owen282000/life-dashboard-stack/releases/latest/download/install.sh | sh -s -- --upgrade
```

To follow every 1.x release, set `LIFEDASH_VERSION=1` once; from then on those two commands are the whole upgrade. A 1.x release never breaks what you built on the stack, see [compatibility.md](compatibility.md), and the [changelog](../CHANGELOG.md) says what's new. When a release adds something to `docker-compose.yml` itself, its notes say so; download that file again then, and keep your `.env`. If you run the MCP server, keep `--profile mcp` on both commands, or set `COMPOSE_PROFILES=mcp` in `.env`, as [mcp.md](mcp.md#start-the-server) explains.

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

The argument is the Compose project name: the folder name, unless you started the stack with `-p` or set `COMPOSE_PROJECT_NAME`. Without an argument the script uses the same default. The script checks that the new volume is still empty, stops the stack, dumps the old database to `backups/`, restores it into a new volume, compares the row count of every table, and only then marks the new volume as ready. It starts the stack again at the end. On its first start the receiver brings the old rows up to date: they become the phone `default`, and a day that older versions stored several times keeps only its latest figure. If an Android phone and an iPhone both sent to the old stack, they now share that label, so on days that both of them sent, the dashboards show the daily totals of one of them. Give each phone its own label from then on, as in [Several phones](install.md#several-phones).

You can also leave the clone behind and use the release files from [install.md](install.md#install). Put them in a new folder, copy your settings from the old `.env`, and add `COMPOSE_PROJECT_NAME=life-dashboard-stack` (the clone's folder name), so Compose finds the volumes that hold your data. The db image has a copy of the upgrade script. In the new folder:

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

## Troubleshooting

The doctor checks a running stack and prints one line per check, `PASS`, `WARN`, `FAIL` or `INFO`, with the fix under every warning and failure. In the install folder:

```bash
curl -fsSL https://github.com/owen282000/life-dashboard-stack/releases/latest/download/install.sh | sh -s -- doctor
```

From a clone, `sh install.sh doctor`; elsewhere, add `--dir <install folder>`. It works with Docker and with Podman, and exits with 1 when a check failed. It changes nothing and never prints a secret or a password.

On the machine, it checks:

- the engine and Compose, and that `docker-compose.yml` and `.env` read fine, with the version of the images;
- that `.env` isn't readable by every user;
- each container (`db`, `receiver`, `grafana`, and `mcp` when it's on): running, and healthy;
- the ports: the receiver published on every interface (on `127.0.0.1` only, a phone can't reach it), and Grafana's;
- the receiver's `/health` from this machine, and `PUBLIC_URL/health` from this machine;
- Grafana's health, and whether `GRAFANA_PASSWORD` from `.env` still logs in `admin` (Grafana reads it only on its first start, so after a change in Grafana itself this is a warning, not a failure);
- with the MCP server on: its token (at least 16 characters, not the same as `API_TOKEN`), `/health`, and that `/mcp` answers with the token and refuses without it;
- how full the database's volume is;
- with rootless Podman: whether the containers come back after a reboot.

Then it runs `python doctor.py` in the receiver's container, which checks from the inside:

- `WEBHOOK_SECRET` set and usable in a pairing code, `PUBLIC_URL` set, valid, and not `localhost` or `127.0.0.1`, which a phone can't reach, `TZ` set and known to Postgres, and `PAIRING_TOKEN` long enough when set;
- the database: the connection, every migration applied, the `dash` views there, and its size;
- `grafana_ro` and `dash_ro`: each logs in with its password and reads a view, in read-only sessions. Where the roles are managed elsewhere, as on CloudNativePG, it checks that they exist and may read;
- every phone label with the time of its last data and its last ping, as a warning when it sent nothing for 24 hours (`--quiet-hours` changes that) and when only test pings arrived;
- the receiver's `/health` on its own port, `PUBLIC_URL/health` from inside the container, and with `API_TOKEN` set, that `/api/phones` answers with the token and refuses without it.

A warning for `PUBLIC_URL` from inside the container alone is often fine: many routers and rootless Podman don't let a container reach its own host's address (no hairpin NAT). Whether the phone reaches the receiver is the one thing no check on the server can tell. Test it on the phone itself, on the network it syncs from: open `PUBLIC_URL/health`, such as `http://192.168.1.10:8080/health`, in its browser. `{"status": "ok"}` means the phone gets there; the app's **Test ping** in the **Webhook** card tests the same, with the secret. If the browser can't open it, check the address, the phone's Wi-Fi or VPN, and the machine's firewall (ufw or firewalld on Linux, the firewall in System Settings on macOS).

`doctor.py` is in the receiver image from 1.1.0 on, so it also runs on its own, with Docker Compose or on [Kubernetes](kubernetes.md):

```bash
docker compose exec receiver python doctor.py
kubectl -n life-dashboard exec deploy/lifedash-life-dashboard-receiver -- python doctor.py
```

`--mcp-url http://<mcp service>:8090` adds the MCP server's `/health` and its refusal without a token, `--json` prints the results as JSON.

To look around inside the receiver or the MCP server yourself, open `sh`, not `bash`: the images are built on Alpine, which has no `bash`, and they have no `pip` either. For example `docker compose exec receiver sh`, or `kubectl -n life-dashboard exec -it deploy/lifedash-life-dashboard-receiver -- sh`.

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
- `/pair` shows the signing secret, and with it anyone can send data to the receiver or delete records in it. The receiver answers it only for a request from a private or loopback address (your own network, a VPN such as Tailscale, the machine itself) that carries no proxy header such as `X-Forwarded-For`, `Forwarded` or `X-Real-IP`; everything else gets a 404. Those headers are never believed, only taken as a sign that a proxy sits in between. Two setups make a request from the internet look like one from your network: a reverse proxy that sends no forwarding header, and port forwarding that hides the source address (Docker Desktop, rootless Docker, rootless Podman). With a port open to the internet in such a setup, block `/pair` there or set `PAIRING_PAGE=0`. To pair through a reverse proxy anyway, set `PAIRING_TOKEN` to a long random string and open `/pair?token=<it>`; the token ends up in browser history and proxy logs, so remove it once the phones are paired. The receiver's own log hides it.
- The receiver speaks plain HTTP, and Docker publishes the receiver and Grafana on every network interface of the host. To reach the receiver from outside, put a reverse proxy with TLS (Caddy, Traefik, nginx) in front of the receiver only, use the `https://` address in the app, and allow request bodies of 32 MB. The receiver turns away anything larger with a 413.
- The [API](api.md) shares the receiver's port. If a reverse proxy makes the receiver reachable from the internet, the API is reachable there too, behind its token; forward only `/webhook` if you don't need it outside.
- Keep Grafana on your own network or behind a VPN. Anyone who can view a dashboard can read every health record through the data source, so don't switch on anonymous access for a Grafana that others can reach.
- Grafana only reads `GRAFANA_PASSWORD` when it creates its admin user on the first start; change the password in Grafana after that. `GRAFANA_DB_PASSWORD` is read at every start, by the receiver and by Grafana.
- `grafana_ro` can read the `records` and `buckets` tables and the `dash` views, and nothing else. It can't write or read the payload ledger, and its queries stop after 30 seconds.
- Postgres isn't published on a port. Its own user and password are `lifedash`, set in `docker-compose.yml` for the database and the receiver. If other people can reach the Docker host, change both before the first start.

## Another data source in Grafana

The Grafana image keeps only the data source plugin the dashboards use, PostgreSQL. The other data sources that Grafana ships as separate plugins are left out, because each brings known vulnerabilities of its own: Prometheus, Loki, Tempo, Jaeger, Zipkin, Pyroscope, Elasticsearch, InfluxDB, MySQL, Microsoft SQL Server, OpenTSDB and Google Cloud Monitoring. The ones built into Grafana itself, such as Graphite, CloudWatch, Azure Monitor and TestData, are still there, and so is every panel.

If you added one of those data sources by hand, Grafana shows it as a missing plugin after the upgrade, and an alert rule that queries it goes to the Error state and can send a DatasourceError notification. Install it from the Grafana catalog into Grafana's volume, where it stays across upgrades, and restart Grafana:

```bash
docker compose exec grafana grafana cli plugins install prometheus
docker compose restart grafana
```

The plugin IDs are `prometheus`, `loki`, `tempo`, `jaeger`, `zipkin`, `grafana-pyroscope-datasource`, `elasticsearch`, `influxdb`, `mysql`, `mssql`, `opentsdb` and `stackdriver` (Google Cloud Monitoring). A plugin installed this way doesn't change with the image; `grafana cli plugins update-all` updates it. On Kubernetes, run the same command with `kubectl -n life-dashboard exec deploy/lifedash-life-dashboard-grafana --` and restart with `kubectl -n life-dashboard rollout restart deployment/lifedash-life-dashboard-grafana`; it needs `grafana.persistence.enabled`, which is on by default, and with `networkPolicy.enabled` Grafana needs `networkPolicy.grafanaEgress` to reach grafana.com. The image also switches off Grafana's `prometheusTypeMigration` feature toggle, which needs the Prometheus plugin; `GF_FEATURE_TOGGLES_PROMETHEUSTYPEMIGRATION=true` switches it on again.

## Build from source

To change the receiver, the views or the dashboards, clone the repository and let Compose build the images from it:

```bash
git clone https://github.com/owen282000/life-dashboard-stack
cd life-dashboard-stack
cp .env.example .env
```

In `.env`, set `WEBHOOK_SECRET` and the rest as in [install.md](install.md#install), and remove the `#` in front of `COMPOSE_FILE=docker-compose.yml:docker-compose.build.yml`. Every `docker compose` command in that folder then builds the images from your clone, tagged `:local`, instead of pulling them:

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
- Two phones on one label overwrite each other's daily totals. Give each phone its own label, see [Several phones](install.md#several-phones).
