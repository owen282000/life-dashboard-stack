# What a version keeps stable

The stack has version numbers from 1.0.0 on, in the form `major.minor.patch`. Within one major version, so for all of 1.x, an upgrade is `docker compose pull && docker compose up -d`, or `helm upgrade` on Kubernetes, and nothing you built on top of the stack breaks: the phones keep sending, your own queries and panels keep working, and your `.env` or values file stays valid. A change to anything on this page is a new major version, with the steps for it in [CHANGELOG.md](../CHANGELOG.md) and the README.

A minor version adds things: a view, a column, a dashboard, a setting with a default. A patch version fixes things.

## The payloads the receiver accepts

- `POST /webhook/<label>` and `POST /webhook` (the label `default`). A label is 1 to 32 characters of lowercase letters, digits, `-` and `_`.
- The JSON payloads of Life Dashboard Companion for Android (1.23.0) and for iOS (1.6.0), as described in the Android app's [payload reference](https://github.com/owen282000/life-dashboard-companion-app/blob/main/docs/webhook.md) and the [iOS payload page](https://github.com/owen282000/life-dashboard-companion-app-ios/blob/main/docs/webhook.md). Later app versions that add fields or record types keep working; a new type is stored before any view reads it.
- The signature: `X-Signature: sha256=<hex HMAC-SHA256 of the body>` with `WEBHOOK_SECRET` as the key, and a 401 when it doesn't match.
- Bodies up to 32 MB, and a 413 above that.
- `GET /health`: 200 once the database is set up, 503 before.
- The rules for what is stored: a body that arrives twice is applied once, a late payload can't overwrite a newer one (by `sequence` and build time), deletions the app reports are applied, and a day's totals replace the earlier figure for that day.

## The database

- The tables `records` and `buckets`, with the columns listed under [Storage underneath](views.md#storage-underneath). Columns may be added; none are renamed, removed or given another meaning.
- The ledger tables (`payloads`, `tombstones`, `schema_migrations`) belong to the receiver. They are kept, but they're not meant for your own queries.
- Migrations only move forward and run when the receiver starts. A version that needs a new Postgres major (and so a dump and restore, like the move from 16 to 18) is a new major version of the stack.
- The database, user and password: `lifedash`, set in `docker-compose.yml`.

## The `dash` views

Every view, column, type and unit in [views.md](views.md), and the helper functions listed there. Views and columns may be added. A column keeps its name, its type and its unit, and a day keeps meaning a local day in `TZ`. The views are rebuilt at every start, so keep views of your own in the `public` schema.

## The `grafana_ro` role

- The name `grafana_ro`, with its password from `GRAFANA_DB_PASSWORD`. Where the receiver's user may not alter the role, as on CloudNativePG (where the role and its password come from the cluster's managed roles) or on a managed Postgres where someone else created it, the role keeps the password it has, and the receiver grants it the same reads.
- `SELECT` on `records`, `buckets` and every view in `dash`, `USAGE` on the `dash` and `public` schemas, and nothing else: no access to the ledger tables, read-only transactions and a 30-second statement timeout.
- The Grafana data source **LifeDashboard Postgres** with the uid `lifedash-postgres`, which panels of your own can point at.

## The read-only API and `dash_ro`

- With `API_TOKEN` set, the receiver answers `GET` requests under `/api/` on its own port: `/api/phones`, `/api/days`, `/api/sleep`, `/api/workouts`, `/api/screen-time`, `/api/series` and `/api/trend`, with the parameters, fields, metric names and units in [api.md](api.md). Fields and metrics may be added; none are renamed, removed or given another unit. Without a token every `/api/` path is a 404.
- `Authorization: Bearer <API_TOKEN>`, a 401 without it, a 400 for bad input, a 404 for an unknown phone, and the limits of 400 days per range and 5,000 rows per answer. A limit may be raised, never lowered.
- The role `dash_ro`, with its password from `DASH_DB_PASSWORD`: `USAGE` on `dash`, `SELECT` on every view in it, and nothing else, with read-only transactions and a 15-second statement timeout.

## The MCP server

- The image `ghcr.io/owen282000/life-dashboard-mcp` and the Compose service `mcp` in the profile `mcp`, listening on 8090 inside its container and as the default of `MCP_PORT`.
- Streamable HTTP at `/mcp` with `Authorization: Bearer <MCP_TOKEN>`, `GET /health` without it, and stdio with `python server.py --stdio`.
- The tools `list_phones`, `daily_summary`, `sleep_nights`, `workouts`, `trend` and `screen_time`, with the parameters and result fields in [mcp.md](mcp.md). Tools, parameters with a default, and fields may be added; none are renamed or removed. Every tool stays read-only.

## The environment variables

The settings in [.env.example](../.env.example) keep their names, meaning and defaults: `WEBHOOK_SECRET`, `TZ`, `GRAFANA_DB_PASSWORD`, `GRAFANA_PASSWORD`, `RECEIVER_PORT`, `GRAFANA_PORT`, `LIFEDASH_VERSION`, `IGNORE_OLD_VOLUME`, `API_TOKEN`, `DASH_DB_PASSWORD`, `MCP_TOKEN` and `MCP_PORT`, and from 1.1 the pairing settings `PUBLIC_URL`, `PAIRING_LOG`, `PAIRING_PAGE` and `PAIRING_TOKEN`. The pairing code keeps the apps' link format (`v=1` with `url`, `secret` and `name`), and `/pair` stays limited to your own network or the token. From 1.3, `python pairing.py [label]` in the receiver image prints the pairing block on demand, from `PUBLIC_URL` and `WEBHOOK_SECRET`. The receiver's own settings (`DATABASE_URL`, `WEBHOOK_SECRET`, `TZ`, `GRAFANA_DB_PASSWORD`, `PORT`) stay the same too. New settings come with a default, so an older `.env` keeps working.

## The ports, services and volumes

- The receiver listens on 8080 and Grafana on 3000, inside their containers and as the defaults of `RECEIVER_PORT` and `GRAFANA_PORT`. Postgres isn't published.
- The Compose services `db`, `receiver` and `grafana`, `mcp` in the profile `mcp`, and the volumes `pgdata` (Postgres), `grafana-data` (Grafana) and `db-data` (the old Postgres 16 volume). Renaming a volume would leave your data behind, so that never happens within a major version.
- The images `ghcr.io/owen282000/life-dashboard-receiver`, `-grafana`, `-db` and `-mcp`, tagged `1.2.3`, `1.2`, `1` and `latest`. `LIFEDASH_VERSION=1` follows every 1.x release. The receiver and MCP images run as user and group 65534, Grafana's as 472 and the db image's Postgres as 70.
- The chart at `oci://ghcr.io/owen282000/charts/life-dashboard`, published with every release from 1.3.0 on, under the same version as the images.

## The Helm chart

The chart `life-dashboard` has no version of its own: its `version` and `appVersion` are the version of the stack, so chart `1.3.0` runs the images `1.3.0`, and the one version rule above covers the chart too. Its public values and the names of what it creates are part of the stable surface. Within a major version, an upgrade is `helm upgrade` with the values file you have, and these keep their names, meaning and defaults:

- `image.registry`, `image.tag`, `image.pullPolicy`, `imagePullSecrets`, and `repository`, `tag` and `digest` under each component's `image`.
- `timezone`, and everything under `secrets`: `existingSecret` with its keys (`WEBHOOK_SECRET`, `GRAFANA_PASSWORD`, `GRAFANA_DB_PASSWORD`, `DASH_DB_PASSWORD`, `API_TOKEN`, `MCP_TOKEN`, `POSTGRES_PASSWORD`, `PAIRING_TOKEN`), the values that replace generated ones, and `keepOnUninstall`. A generated secret stays the same on every upgrade.
- `nameOverride`, `fullnameOverride` and `serviceAccount`.
- `receiver.publicUrl`, `receiver.pairing.log` and `.page` (both off by default), `receiver.api.enabled`, `receiver.expose.webhook`, `.api` and `.pair` (only `/webhook` by default), `receiver.replicas`, `receiver.topologySpreadConstraints`, and `mcp.enabled` and `mcp.replicas`.
- `postgres.mode` (`cnpg`, `bundled`, `external`), `postgres.database` and `postgres.owner`, and under `postgres.cnpg`: `clusterName`, `instances`, `imageName`, `storage`, `walStorage`, `resources`, `parameters`, `synchronous` (asynchronous by default), `affinity`, `keepOnUninstall`, `annotations`, `roles`, `backup` and `recovery`; under `postgres.bundled`: `image`, `storage` and `resources`; under `postgres.external`: `existingSecret`, `urlKey`, `host`, `port`, `database` and `sslmode`.
- `grafana.adminUser`, `grafana.rootUrl` and `grafana.persistence`.
- For every component: `service`, `ingress`, `httpRoute`, `resources`, `podSecurityContext`, `securityContext`, `podAnnotations`, `podLabels`, `nodeSelector`, `tolerations`, `affinity`, `extraEnv` and `podDisruptionBudget` where it has one.
- `demo`, and `networkPolicy` with the `from` peers of the receiver, Grafana and the MCP server, `externalDatabase`, `grafanaEgress`, `dns` (the cluster DNS by default) and, for the database pods, `postgres.from`, `postgres.cnpgOperator` and `postgres.extraIngress`.
- The names of what the chart creates, which data and other tools point at: `<release>-life-dashboard` (the Secret) and its `-receiver`, `-grafana`, `-mcp` and `-postgres` suffixes, the role Secrets `-grafana-ro` and `-dash-ro`, Grafana's volume `-grafana`, the bundled volume `data-<release>-life-dashboard-postgres-0`, and the CloudNativePG `Cluster` `<release>-life-dashboard-postgres` with its `ObjectStore` `-backup` and `ScheduledBackup` `-daily`. Renaming one would leave data or a password behind, so that never happens within a major version.
- The pod labels `app.kubernetes.io/name`, `app.kubernetes.io/instance` and `app.kubernetes.io/component` (`receiver`, `grafana`, `mcp`, `postgres`), which NetworkPolicies and monitoring of your own can select on.
- An uninstall keeps the database, Grafana's volume and the generated Secrets unless you switch that off.

Values may be added, with a default that keeps the chart doing what it did. A minor version may raise the default of `postgres.cnpg.imageName` within Postgres 18 and the CloudNativePG version the chart is tested with.

## What can change in a minor version

- The provisioned dashboards: panels, layout and the queries behind them. Grafana doesn't save changes over them, so a copy you made with **Save as** is yours and isn't touched.
- The versions of Grafana and Python, and of Postgres within its major version, as long as everything above keeps working. Dependabot proposes them.
- Log messages, the wording of error replies, and the files in the images other than the ones named here.
