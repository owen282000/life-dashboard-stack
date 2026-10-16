# What a version keeps stable

The stack has version numbers from 1.0.0 on, in the form `major.minor.patch`. Within one major version, so for all of 1.x, an upgrade is `docker compose pull && docker compose up -d` and nothing you built on top of the stack breaks: the phones keep sending, your own queries and panels keep working, and your `.env` stays valid. A change to anything on this page is a new major version, with the steps for it in [CHANGELOG.md](../CHANGELOG.md) and the README.

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

- The name `grafana_ro`, with its password from `GRAFANA_DB_PASSWORD`.
- `SELECT` on `records`, `buckets` and every view in `dash`, `USAGE` on the `dash` and `public` schemas, and nothing else: no access to the ledger tables, read-only transactions and a 30-second statement timeout.
- The Grafana data source **LifeDashboard Postgres** with the uid `lifedash-postgres`, which panels of your own can point at.

## The environment variables

The settings in [.env.example](../.env.example) keep their names, meaning and defaults: `WEBHOOK_SECRET`, `TZ`, `GRAFANA_DB_PASSWORD`, `GRAFANA_PASSWORD`, `RECEIVER_PORT`, `GRAFANA_PORT`, `LIFEDASH_VERSION`, `IGNORE_OLD_VOLUME`, and from 1.1 the pairing settings `PUBLIC_URL`, `PAIRING_LOG`, `PAIRING_PAGE` and `PAIRING_TOKEN`. The pairing code keeps the apps' link format (`v=1` with `url`, `secret` and `name`), and `/pair` stays limited to your own network or the token. The receiver's own settings (`DATABASE_URL`, `WEBHOOK_SECRET`, `TZ`, `GRAFANA_DB_PASSWORD`, `PORT`) stay the same too. New settings come with a default, so an older `.env` keeps working.

## The ports, services and volumes

- The receiver listens on 8080 and Grafana on 3000, inside their containers and as the defaults of `RECEIVER_PORT` and `GRAFANA_PORT`. Postgres isn't published.
- The Compose services `db`, `receiver` and `grafana`, and the volumes `pgdata` (Postgres), `grafana-data` (Grafana) and `db-data` (the old Postgres 16 volume). Renaming a volume would leave your data behind, so that never happens within a major version.
- The images `ghcr.io/owen282000/life-dashboard-receiver`, `-grafana` and `-db`, tagged `1.2.3`, `1.2`, `1` and `latest`. `LIFEDASH_VERSION=1` follows every 1.x release.

## What can change in a minor version

- The provisioned dashboards: panels, layout and the queries behind them. Grafana doesn't save changes over them, so a copy you made with **Save as** is yours and isn't touched.
- The versions of Grafana and Python, and of Postgres within its major version, as long as everything above keeps working. Dependabot proposes them.
- Log messages, the wording of error replies, and the files in the images other than the ones named here.
