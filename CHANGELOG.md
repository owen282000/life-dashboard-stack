# Changelog

Every release of the Life Dashboard Stack and what changed in it. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). What a major version keeps stable is in [docs/compatibility.md](docs/compatibility.md).

## [Unreleased]

### Added

- The receiver prints a pairing QR code in its log at startup, with the link and the URL and secret to type by hand. Life Dashboard Companion scans it like the Home Assistant integration's code. Set `PUBLIC_URL` to the address your phone reaches the receiver on; `PAIRING_LOG=0` hides the block.
- `/pair` shows the same code as a page, only to your own network and not through a reverse proxy, or with `?token=` matching `PAIRING_TOKEN`. `?label=` gives each phone its own code. `PAIRING_PAGE=0` switches it off.
- Without `WEBHOOK_SECRET` there is no pairing code, because the apps refuse one; the log and the page suggest a new random secret.
- A read-only JSON API under `/api/` on the receiver's port, off until `API_TOKEN` is set: `phones`, `days`, `sleep`, `workouts`, `screen-time`, `series` and `trend`, with the token as a bearer token, ranges of up to 400 days and at most 5,000 rows per answer. [docs/api.md](docs/api.md) describes every endpoint.
- An optional MCP server, so a model you choose can read your history: a fourth image, `life-dashboard-mcp`, started with `--profile mcp` on port 8090 (`MCP_PORT`). It has six read-only tools (`list_phones`, `daily_summary`, `sleep_nights`, `workouts`, `trend`, `screen_time`) on the same queries as the API, and no tool that writes or takes SQL. Streamable HTTP needs `MCP_TOKEN` as a bearer token; stdio serves Claude Desktop on the same machine. [docs/mcp.md](docs/mcp.md) has the setup, also for a local model through Ollama.
- `dash_ro`, a database user that can only read the `dash` views, with read-only sessions and a 15-second statement timeout. The API and the MCP server read through it, never as the database owner. Its password is `DASH_DB_PASSWORD`.
- A Helm chart for Kubernetes, `charts/life-dashboard`, for Helm 3 and 4 on Kubernetes 1.29 or later: the receiver, Grafana with the six dashboards and the optional MCP server, with Postgres as a CloudNativePG `Cluster` (the default, with managed roles for `grafana_ro` and `dash_ro`, optional synchronous replication, backups to object storage through the Barman Cloud plugin, and restores), a bundled StatefulSet, or an external database. Secrets come from an existing Secret or are generated once and kept, each pod gets only the ones it uses, and the pods restart only when one really changes. The pods meet the `restricted` Pod Security Standard. The pairing block and `/pair` are off, Ingress and Gateway API routes send only `/webhook` to the receiver unless you open more, and the optional NetworkPolicies cover the database pods too, with DNS limited to the cluster DNS. `helm test` checks the receiver, the dashboards, the data source, the API and the MCP server. [docs/kubernetes.md](docs/kubernetes.md) explains it. Each release publishes the chart, with the release's version, to `oci://ghcr.io/owen282000/charts/life-dashboard`.
- `python pairing.py [label]` in the receiver's container prints the pairing block for one phone on demand, such as through `kubectl exec`, with the log block and the page both off.

### Changed

- On a Postgres where the receiver's user may not create or alter roles, such as CloudNativePG's app user, the receiver uses `grafana_ro` and `dash_ro` as they exist, says so in its log, and still grants them their reads. A user with `CREATEROLE` but no superuser, as on most managed Postgres services, creates them and sets their passwords as long as it created them itself. With Docker Compose nothing changes: it creates them and sets their passwords as before.
- The pairing block, the `/pair` page and the receiver's warnings name the setting to change (`WEBHOOK_SECRET`, `PUBLIC_URL`, `PAIRING_LOG`, `PAIRING_PAGE`, `TZ`) and say to restart the receiver, instead of pointing at `.env` and `docker compose`, so they fit Docker Compose and Kubernetes alike.
- The receiver image runs as user 65534 instead of root, like the MCP image, and both name the user by number. Docker Compose needs no change.

### Fixed

- A wrong password, a user that may not log in or a database that doesn't exist stops the receiver at once with the server's own message, instead of two minutes of waiting and `database unreachable`. While the database is out of reach, the log says why, once per reason.
- Each payload connects to Postgres with a 5-second timeout, so during a failover a request fails fast and the app sends it again, instead of hanging for half a minute.
- On `SIGTERM`, as in `docker stop` or a rollout, the receiver stops taking connections and gives the requests in flight up to 10 seconds to finish, so no payload is cut off halfway.
- On Postgres 16 and later, a database owner with `CREATEROLE` but no superuser, as on most managed Postgres services, can now create `grafana_ro` and `dash_ro`: the receiver names only the role attributes that user holds itself, which is all Postgres lets it name.

## [1.0.0]

The first release with a version number. Until now you installed the stack from a clone of the repository and Docker built it on your machine. From this release on, the three parts are published as images for amd64 and arm64, so installing and upgrading needs one `docker-compose.yml` and a `.env`.

### Added

- Three images on GHCR, each for amd64 and arm64: `life-dashboard-receiver` (with the sample data script for the demo), `life-dashboard-grafana` (with the data source and the six dashboards built in) and `life-dashboard-db` (Postgres 18 with the guard for an old Postgres 16 volume, and the upgrade script). Every image carries build provenance and an SBOM.
- `LIFEDASH_VERSION` in `.env` picks the version of the images. Empty means the version of your `docker-compose.yml`; `1` follows every 1.x release.
- `docker-compose.build.yml` builds the images from a clone instead of pulling them.
- [docs/compatibility.md](docs/compatibility.md) says what stays the same within a major version: the payloads, the database, the `dash` views, `grafana_ro`, the settings and the ports.
- Each release has `docker-compose.yml` and `env.example` attached.

### Changed

- `docker-compose.yml` uses the published images and no longer mounts files from the repository. The dashboards now live in the Grafana image under `/etc/grafana/dashboards`, so an upgrade brings the new ones along.
- `tools/upgrade-postgres.sh` also runs without a clone, from the folder with `docker-compose.yml`. The db image has a copy, and the guard's message says how to get it.

### Already in the stack

Everything the stack did before it had version numbers, now part of 1.0.0:

- A receiver that takes signed payloads (`X-Signature`, HMAC-SHA256) from the Android app 1.23.0 and the iOS app 1.6.0 at `/webhook/<label>`, one label per phone. It keeps every payload in a ledger, applies a payload that arrives twice once, never lets a late payload overwrite a newer one, applies the deletions the app reports, stores series sent per time window, and turns away bodies over 32 MB.
- Postgres 18 with one `records` table for every record type, and the `dash` schema of views that turns records into days, nights, hours and apps in your `TZ`, documented in [docs/views.md](docs/views.md). Numbered migrations run at every start.
- Grafana 13.2 with six dashboards in a **Life Dashboard** folder: the overview as the home page, Activity, Sleep, Heart and body, Screen time and Data and sync, with a **Phone** picker on each. Grafana reads through `grafana_ro`, a user that can only `SELECT`.
- `--profile demo`, which posts 60 days of made-up data for an Android phone and an iPhone through the real receiver.
- A guard that refuses to start Postgres 18 while the data is still in the Postgres 16 volume of earlier versions, and `tools/upgrade-postgres.sh`, which moves it over with a dump and a restore and leaves the old volume untouched.
- A day's totals and screen time replace the older figure for that day instead of adding a row.
- The pinned versions of Postgres, Grafana, Python and the receiver's packages are kept current by Dependabot, and CI runs the receiver's tests against a real Postgres.

[Unreleased]: https://github.com/owen282000/life-dashboard-stack/compare/1.0.0...HEAD
[1.0.0]: https://github.com/owen282000/life-dashboard-stack/releases/tag/1.0.0
