#!/bin/sh
# Grafana takes its admin password from GF_SECURITY_ADMIN_PASSWORD only when it creates the admin
# user, on the first start with an empty grafana-data volume; without one it would be admin. From
# 1.2 that first start needs a password of its own. A Grafana that exists keeps the one it has.
if [ ! -e "${GF_PATHS_DATA:-/var/lib/grafana}/grafana.db" ]; then
    case "${GF_SECURITY_ADMIN_PASSWORD:-}" in
        '' | admin | lifedash)
            echo "Life Dashboard: a new Grafana needs GRAFANA_PASSWORD set to a password of your own" \
                "(not empty, admin or lifedash). Set it in .env, for example to the output of" \
                "openssl rand -hex 16, then run docker compose up -d. An existing Grafana keeps its password." >&2
            exit 1
            ;;
    esac
fi
exec /run.sh "$@"
