#!/bin/sh
# Runs before Postgres on every start of the db service.
#
# Postgres 18 cannot read a Postgres 16 data directory. The old volume is mounted read-only
# at /old. While it holds a database and the new volume has no marker from
# tools/upgrade-postgres.sh, this refuses to start, so nobody ends up with an empty
# dashboard and their data left behind in a volume they forgot about.
set -eu

marker=/var/lib/postgresql/.restored-from-16

if [ -f /old/PG_VERSION ] && [ ! -f "$marker" ] && [ "${IGNORE_OLD_VOLUME:-0}" != "1" ]; then
    cat >&2 <<MSG

========================================================================
Life Dashboard: your data is still in the Postgres $(cat /old/PG_VERSION) volume.

This version of the stack runs Postgres 18, which cannot open that volume.
Move your data over once with:

    tools/upgrade-postgres.sh <compose project name>

It dumps the old database, restores it into the new volume and checks the
row counts. The old volume is never changed, so the previous version of
docker-compose.yml still starts on it if you want to go back.

To start with an empty database instead and keep the old volume as it is,
set IGNORE_OLD_VOLUME=1 in .env.
========================================================================

MSG
    exit 1
fi

exec docker-entrypoint.sh "$@"
