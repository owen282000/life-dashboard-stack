#!/bin/sh
# Move a Life Dashboard database from the Postgres 16 volume of earlier versions to the
# Postgres 18 volume this version uses. Run it once, in a clone of the repository:
#
#     tools/upgrade-postgres.sh [compose project name]
#
# or, without a clone, in the folder with docker-compose.yml and .env. The db image carries
# a copy of this script:
#
#     docker compose run --rm --no-deps -T --entrypoint cat db \
#         /usr/local/share/life-dashboard/upgrade-postgres.sh > upgrade-postgres.sh
#     sh upgrade-postgres.sh [compose project name]
#
# It works on the docker-compose.yml in the current folder, or else on the one in the
# repository this script sits in. The project name defaults to COMPOSE_PROJECT_NAME (from the
# environment or .env), else the name of that folder, which is what docker compose uses when
# you do not pass -p.
#
# What it does:
#   1. checks that the new volume is absent or empty (if not, it stops here and changes
#      nothing, so the stack keeps running)
#   2. stops the stack
#   3. copies the old volume, starts Postgres 16 on the copy and writes a pg_dump to
#      backups/lifedash-pg16-<date>.dump (the old volume itself is never written)
#   4. initializes the new volume with the stack's db image and restores the dump
#   5. compares the row count of every table on both servers
#   6. writes the marker that lets the db service start, only when all counts match
#   7. starts the stack; the receiver then runs its migrations
#
# To go back: start the previous version of the stack (in a clone: check out the last commit
# on Postgres 16 and run docker compose up -d --build). It starts on the untouched old volume.
set -eu

if [ -f docker-compose.yml ]; then
    DIR=$(pwd)
else
    DIR=$(cd "$(dirname "$0")/.." && pwd)
fi
if [ ! -f "$DIR/docker-compose.yml" ]; then
    printf '\nUpgrade stopped: no docker-compose.yml here. Run this in the folder with docker-compose.yml.\n' >&2
    exit 1
fi
DEFAULT_PROJECT=$(basename "$DIR" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')
ENV_PROJECT=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' "$DIR/.env" 2>/dev/null | tail -n 1 | tr -d "\"' \r")
PROJECT=${1:-${COMPOSE_PROJECT_NAME:-${ENV_PROJECT:-$DEFAULT_PROJECT}}}
OLD_IMAGE=postgres:16-alpine
NEW_IMAGE=$(cd "$DIR" && docker compose -p "$PROJECT" config --images db 2>/dev/null | head -n 1)
OLD_VOLUME=${PROJECT}_db-data
NEW_VOLUME=${PROJECT}_pgdata
COPY_VOLUME=${PROJECT}_pg16-copy
OLD_CONTAINER=${PROJECT}-upgrade-pg16
NEW_CONTAINER=${PROJECT}-upgrade-pg18
DUMP_DIR=$DIR/backups
DUMP=$DUMP_DIR/lifedash-pg16-$(date +%Y-%m-%d-%H%M%S).dump

say() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nUpgrade stopped: %s\n' "$*" >&2; exit 1; }

cleanup() {
    docker rm -f "$OLD_CONTAINER" "$NEW_CONTAINER" >/dev/null 2>&1 || true
    docker volume rm "$COPY_VOLUME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

wait_ready() {  # container
    i=0
    # Over TCP: the entrypoint's temporary init server listens on the socket only.
    until docker exec "$1" pg_isready -q -h 127.0.0.1 -U lifedash -d lifedash 2>/dev/null; do
        i=$((i + 1))
        [ "$i" -lt 120 ] || fail "$1 did not become ready; see: docker logs $1"
        sleep 1
    done
}

counts() {  # container -> "schema.table count" lines, sorted
    tables=$(docker exec "$1" psql -U lifedash -d lifedash -XAt -c \
        "SELECT quote_ident(table_schema) || '.' || quote_ident(table_name) FROM information_schema.tables
         WHERE table_type = 'BASE TABLE' AND table_schema NOT IN ('pg_catalog', 'information_schema')
         ORDER BY 1")
    for t in $tables; do
        n=$(docker exec "$1" psql -U lifedash -d lifedash -XAt -c "SELECT count(*) FROM $t")
        echo "$t $n"
    done
}

[ -n "$NEW_IMAGE" ] || fail "docker compose finds no db service in $DIR/docker-compose.yml"
docker volume inspect "$OLD_VOLUME" >/dev/null 2>&1 \
    || fail "volume $OLD_VOLUME does not exist. Is '$PROJECT' the right project name? See: docker volume ls"

say "Getting the db image ($NEW_IMAGE)"
if ! docker image inspect "$NEW_IMAGE" >/dev/null 2>&1; then
    (cd "$DIR" && docker compose -p "$PROJECT" pull --ignore-buildable db && docker compose -p "$PROJECT" build db)
fi

say "Checking that $NEW_VOLUME is absent or empty"
if docker volume inspect "$NEW_VOLUME" >/dev/null 2>&1; then
    content=$(docker run --rm -v "$NEW_VOLUME:/v" --entrypoint sh "$NEW_IMAGE" -c 'ls -A /v | head -n 1')
    [ -z "$content" ] || fail "$NEW_VOLUME already holds data. Nothing was changed.
Remove it only if you are sure it holds nothing you need: docker volume rm $NEW_VOLUME"
fi

say "Stopping the stack ($PROJECT)"
(cd "$DIR" && docker compose -p "$PROJECT" stop)

say "Copying $OLD_VOLUME (read-only) and dumping it with Postgres 16"
docker volume rm "$COPY_VOLUME" >/dev/null 2>&1 || true
docker run --rm -v "$OLD_VOLUME:/from:ro" -v "$COPY_VOLUME:/to" --entrypoint sh "$OLD_IMAGE" \
    -c 'cp -a /from/. /to/ && rm -f /to/postmaster.pid'
docker run -d --name "$OLD_CONTAINER" -e POSTGRES_PASSWORD=lifedash \
    -v "$COPY_VOLUME:/var/lib/postgresql/data" "$OLD_IMAGE" >/dev/null
wait_ready "$OLD_CONTAINER"
mkdir -p "$DUMP_DIR"
docker exec "$OLD_CONTAINER" pg_dump -U lifedash -Fc lifedash > "$DUMP"
echo "Dump written: $DUMP ($(wc -c < "$DUMP" | tr -d ' ') bytes)"

say "Initializing $NEW_VOLUME with $NEW_IMAGE and restoring"
docker run -d --name "$NEW_CONTAINER" -e POSTGRES_USER=lifedash -e POSTGRES_PASSWORD=lifedash \
    -e POSTGRES_DB=lifedash -v "$NEW_VOLUME:/var/lib/postgresql" "$NEW_IMAGE" >/dev/null
wait_ready "$NEW_CONTAINER"
docker exec -i "$NEW_CONTAINER" pg_restore -U lifedash -d lifedash --no-owner --role=lifedash \
    --exit-on-error < "$DUMP"

say "Comparing row counts"
old_counts=$(counts "$OLD_CONTAINER")
new_counts=$(counts "$NEW_CONTAINER")
printf '%-40s %12s %12s\n' table "postgres 16" "postgres 18"
echo "$old_counts" | while read -r t n; do
    m=$(echo "$new_counts" | awk -v t="$t" '$1 == t { print $2 }')
    printf '%-40s %12s %12s\n' "$t" "$n" "${m:-missing}"
done
[ "$old_counts" = "$new_counts" ] || fail "the row counts differ. The new volume was not marked, so the db service
will not start on it. The old volume is unchanged. Remove the new one before trying again:
docker volume rm $NEW_VOLUME"

docker exec "$NEW_CONTAINER" sh -c 'date -u +%Y-%m-%dT%H:%M:%SZ > /var/lib/postgresql/.restored-from-16'
docker stop "$NEW_CONTAINER" >/dev/null

say "Starting the stack"
(cd "$DIR" && docker compose -p "$PROJECT" up -d)

cat <<DONE

Done. Your data is now in $NEW_VOLUME on Postgres 18; the receiver migrates it on start.
The old volume $OLD_VOLUME is unchanged, and the dump stays in $DUMP.
Once the dashboards look right you can remove the old volume: docker volume rm $OLD_VOLUME
DONE
