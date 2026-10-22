#!/bin/sh
#
# Life Dashboard Stack: installs the stack with Docker Compose or Podman, checks it, and
# writes a .env for NAS and Portainer setups.
#
#   curl -fsSL https://github.com/owen282000/life-dashboard-stack/releases/latest/download/install.sh | sh
#   curl -fsSL .../install.sh | sh -s -- --with-mcp          options after "sh -s --"
#   curl -fsSL .../install.sh | sh -s -- doctor              check an install
#   curl -fsSL .../install.sh | sh -s -- --env-only          only print a .env, no engine needed
#   sh install.sh [doctor] [options]                         from a clone
#
# The script carries its release's version: it fetches docker-compose.yml and env.example of
# that release, so what it installs never changes under it. From a clone it uses the clone's
# files instead. It writes .env with a random secret, passwords and tokens, starts the stack,
# waits until it answers and shows the pairing code. Run again, it keeps .env; --upgrade
# moves the folder to this version. sh install.sh --help lists every option. docs/install.md
# explains it.
#
# Exit codes: 0 done, 1 failed, 2 wrong usage, 3 a prerequisite is missing (no Docker or
# Podman, no Compose, the engine not running), 4 started but not healthy in time. doctor
# exits 1 when a check failed.
#
# POSIX sh: dash, busybox ash, bash and zsh run it, on Linux and macOS.

set -eu

# The release this script belongs to. scripts/prepare-release.sh sets it, and
# .github/scripts/check-release.sh refuses a tag it doesn't match.
VERSION=1.2.0
REPO=https://github.com/owen282000/life-dashboard-stack
# Where the release files come from; LIFEDASH_DOWNLOAD points at a mirror or a test folder.
DOWNLOAD=${LIFEDASH_DOWNLOAD:-$REPO/releases/download/$VERSION}
ONE_LINER="curl -fsSL $REPO/releases/latest/download/install.sh | sh"

# Settings from the environment; the options below override them.
DIR=${LIFEDASH_DIR:-}
ENGINE_CHOICE=${LIFEDASH_ENGINE:-}
YES=${LIFEDASH_YES:-0}
WITH_API=${LIFEDASH_WITH_API:-}
WITH_MCP=${LIFEDASH_WITH_MCP:-}
PULL=${LIFEDASH_PULL:-1}
TIMEOUT=${LIFEDASH_TIMEOUT:-300}
LABEL=${LIFEDASH_LABEL:-phone}
URL_IN=${PUBLIC_URL:-}
TZ_IN=${LIFEDASH_TZ:-}
TZ_SHELL=${TZ:-}
COMMAND=install
UPGRADE=0
START=1
SHOW_PASSWORD=0
ENV_ONLY=0
OUTPUT=
URL_GIVEN=0
[ -n "$URL_IN" ] && URL_GIVEN=1

# Messages go to fd 3: stdout, or stderr with --env-only, where stdout carries the .env.
exec 3>&1

say() { printf '%s\n' "$*" >&3; }
step() { printf '\n==> %s\n' "$*" >&3; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() {
    code=$1
    shift
    printf 'error: %s\n' "$*" >&2
    exit "$code"
}

usage() {
    cat <<EOF
Life Dashboard Stack $VERSION installer

Usage: sh install.sh [install|doctor] [options]
       curl -fsSL $REPO/releases/latest/download/install.sh | sh -s -- [options]

install (the default): fetch docker-compose.yml and env.example of $VERSION into a folder,
write .env with a random secret, passwords and tokens, start the stack and show the pairing
code. Run again on the same folder, it keeps .env and only starts the stack.
doctor: check an install, from the containers and the ports to each phone's last data.

Options:
  --dir DIR           the folder (LIFEDASH_DIR). Default: ./life-dashboard, or the current
                      folder when it holds an install, or the clone this script is in
  --public-url URL    the address your phone reaches this machine on (PUBLIC_URL).
                      Default: http://<this machine's LAN address>:8080
  --tz NAME           your time zone, such as Europe/Amsterdam (LIFEDASH_TZ). Default: the
                      machine's own
  --with-api          switch on the read-only API: generate API_TOKEN (LIFEDASH_WITH_API=1)
  --with-mcp          switch on the MCP server: generate MCP_TOKEN, COMPOSE_PROFILES=mcp
                      (LIFEDASH_WITH_MCP=1)
  --label LABEL       the phone label in the pairing code (LIFEDASH_LABEL). Default: phone
  --engine ENGINE     docker or podman (LIFEDASH_ENGINE). Default: whichever runs
  --upgrade           on an existing install: fetch docker-compose.yml of $VERSION, keep .env,
                      pull the images and restart
  --no-pull           don't pull images, use the ones on this machine (LIFEDASH_PULL=0)
  --no-start          only write the files
  --show-password     print Grafana's admin password once at the end
  --env-only          print a ready .env to stdout and the URL of the matching
                      docker-compose.yml, for a NAS or Portainer. Needs no container engine
  --output FILE       with --env-only: write the .env to FILE instead
  -y, --yes           ask nothing, use the defaults and the options (LIFEDASH_YES=1). Also
                      the case without a terminal
  -h, --help          this text

RECEIVER_PORT, GRAFANA_PORT, MCP_PORT, LIFEDASH_VERSION and COMPOSE_PROJECT_NAME in the
environment go into the new .env too. LIFEDASH_DOWNLOAD fetches the release files from a
mirror, LIFEDASH_TIMEOUT sets the seconds to wait for the stack (300). Exit codes: 0 done, 1 failed, 2 wrong usage, 3 a
prerequisite is missing, 4 started but not healthy in time.
EOF
}

# ------------------------------------------------------------------ arguments

need_value() {
    [ "$#" -ge 2 ] && [ -n "$2" ] || die 2 "$1 needs a value (sh install.sh --help)"
}

while [ "$#" -gt 0 ]; do
    case $1 in
        install) COMMAND=install ;;
        doctor) COMMAND=doctor ;;
        help | -h | --help) usage; exit 0 ;;
        --dir) need_value "$@"; DIR=$2; shift ;;
        --dir=*) DIR=${1#*=} ;;
        --public-url) need_value "$@"; URL_IN=$2; URL_GIVEN=1; shift ;;
        --public-url=*) URL_IN=${1#*=}; URL_GIVEN=1 ;;
        --tz) need_value "$@"; TZ_IN=$2; shift ;;
        --tz=*) TZ_IN=${1#*=} ;;
        --label) need_value "$@"; LABEL=$2; shift ;;
        --label=*) LABEL=${1#*=} ;;
        --engine) need_value "$@"; ENGINE_CHOICE=$2; shift ;;
        --engine=*) ENGINE_CHOICE=${1#*=} ;;
        --output) need_value "$@"; OUTPUT=$2; shift ;;
        --output=*) OUTPUT=${1#*=} ;;
        --with-api) WITH_API=1 ;;
        --with-mcp) WITH_MCP=1 ;;
        --upgrade) UPGRADE=1 ;;
        --no-pull) PULL=0 ;;
        --no-start) START=0 ;;
        --show-password) SHOW_PASSWORD=1 ;;
        --env-only) ENV_ONLY=1 ;;
        -y | --yes) YES=1 ;;
        *) die 2 "unknown option $1 (sh install.sh --help)" ;;
    esac
    shift
done

case $ENGINE_CHOICE in "" | docker | podman) ;; *) die 2 "--engine is docker or podman, not $ENGINE_CHOICE" ;; esac
case $TIMEOUT in '' | *[!0-9]*) die 2 "LIFEDASH_TIMEOUT is a number of seconds" ;; esac
if ! printf '%s\n' "$LABEL" | grep -Eq '^[a-z0-9_-]{1,32}$'; then
    die 2 "a label is 1 to 32 lowercase letters, digits, - and _, not '$LABEL'"
fi
[ -z "$OUTPUT" ] || [ "$ENV_ONLY" = 1 ] || die 2 "--output goes with --env-only"
[ "$ENV_ONLY" = 0 ] || [ "$COMMAND" = install ] || die 2 "--env-only goes without doctor"

# ------------------------------------------------------------------ small helpers

is_on() { case ${1:-} in 1 | y | Y | yes | true | on) return 0 ;; *) return 1 ;; esac; }

# The folder of this script when it runs from a clone of the repository, else empty. Piped
# through curl, $0 is the shell and there is no clone.
CLONE=
case $0 in
    */install.sh | install.sh)
        here=$(cd "$(dirname "$0")" 2>/dev/null && pwd -P) || here=
        if [ -n "$here" ] && [ -f "$here/docker-compose.yml" ] && [ -f "$here/.env.example" ] && [ -f "$here/receiver/doctor.py" ]; then
            CLONE=$here
        fi
        ;;
esac

# Random hex from the kernel's generator: n bytes give 2n characters.
rand_hex() {
    if [ -r /dev/urandom ]; then
        hex=$(od -An -N"$1" -tx1 /dev/urandom | tr -d ' \n')
    elif command -v openssl >/dev/null 2>&1; then
        hex=$(openssl rand -hex "$1")
    else
        die 3 "no /dev/urandom and no openssl to generate the secrets"
    fi
    [ "${#hex}" -eq $(($1 * 2)) ] || die 1 "could not generate random values"
    printf '%s' "$hex"
}

# a >= b for versions like 2.24.1 or v1.5.0-dev.
version_ge() {
    awk -v a="$1" -v b="$2" 'BEGIN {
        sub(/^v/, "", a); sub(/^v/, "", b)
        n = split(a, x, "."); m = split(b, y, ".")
        for (i = 1; i <= 3; i++) {
            p = (i <= n) ? x[i] + 0 : 0; q = (i <= m) ? y[i] + 0 : 0
            if (p > q) exit 0
            if (p < q) exit 1
        }
        exit 0
    }'
}

have() { command -v "$1" >/dev/null 2>&1; }

fetch() {
    if have curl; then
        curl -fsSL --retry 3 -o "$2" "$1"
    elif have wget; then
        wget -q -O "$2" "$1"
    else
        die 3 "neither curl nor wget is installed, so the release files can't be downloaded"
    fi
}

# The HTTP status of a GET (000 when nothing answered). Extra curl config, such as a header,
# comes on stdin, so a token never shows up in the process list.
http_code() {
    if have curl; then
        curl -s -o /dev/null -w '%{http_code}' --max-time 4 -K - "$@" || true
    elif have wget; then
        cat >/dev/null
        if wget -q -O /dev/null -T 4 "$1" 2>/dev/null; then echo 200; else echo 000; fi
    else
        cat >/dev/null
        echo 000
    fi
}

# Questions go to the terminal, also when the script itself comes through a pipe.
can_ask() {
    [ "$YES" != 1 ] || return 1
    # In a subshell: a failed redirection on a builtin ends a POSIX shell, here only the subshell.
    (exec </dev/tty >/dev/tty) 2>/dev/null
}

ask() {
    printf '%s [%s]: ' "$1" "$2" >/dev/tty
    IFS= read -r answer </dev/tty || answer=
    [ -n "$answer" ] || answer=$2
    printf '%s' "$answer"
}

ask_yes() {
    if [ "$2" = y ]; then hint=Y/n; else hint=y/N; fi
    printf '%s [%s]: ' "$1" "$hint" >/dev/tty
    IFS= read -r answer </dev/tty || answer=
    case ${answer:-$2} in y | Y | yes | Yes) return 0 ;; *) return 1 ;; esac
}

# KEY=VALUE in an env file: replaces the line, or a commented-out "# KEY=" line, or adds it.
set_key() {
    K=$2 V=$3 awk 'BEGIN { k = ENVIRON["K"]; v = ENVIRON["V"] }
        !done && ($0 ~ "^" k "=" || $0 ~ "^# *" k "=") { print k "=" v; done = 1; next }
        { print }
        END { if (!done) print k "=" v }' "$1" >"$1.tmp"
    mv "$1.tmp" "$1"
}

# A value from an env file, without surrounding quotes. Never sources the file.
env_get() {
    [ -f "$1" ] || return 0
    sed -n "s/^$2=//p" "$1" | tail -n 1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

# A port from .env, or its default.
# A port setting may carry a host address (MCP_PORT=127.0.0.1:8090); only the port counts here.
port_of() {
    p=$(env_get .env "$1")
    p=${p##*:}
    printf '%s' "${p:-$2}"
}

# ------------------------------------------------------------------ this machine

detect_ip() {
    ip=
    case $(uname -s) in
        Darwin)
            iface=$(route -n get default 2>/dev/null | awk '/interface:/ { print $2; exit }')
            for i in $iface en0 en1; do
                ip=$(ipconfig getifaddr "$i" 2>/dev/null || true)
                [ -z "$ip" ] || break
            done
            ;;
        *)
            if have ip; then
                ip=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{ for (i = 1; i < NF; i++) if ($i == "src") { print $(i + 1); exit } }')
            fi
            if [ -z "$ip" ] && have hostname; then
                ip=$(hostname -I 2>/dev/null | awk '{ print $1 }')
            fi
            ;;
    esac
    case $ip in 127.* | "") ip= ;; esac
    printf '%s' "$ip"
}

# An IANA name, and one this machine knows when it has the time zone database.
valid_tz() {
    printf '%s\n' "$1" | grep -Eq '^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*$' || return 1
    [ ! -d /usr/share/zoneinfo ] || [ -f "/usr/share/zoneinfo/$1" ]
}

detect_tz() {
    tz=
    if [ -r /etc/timezone ]; then
        tz=$(head -n 1 /etc/timezone)
    fi
    if [ -z "$tz" ] && [ -L /etc/localtime ]; then
        tz=$(readlink /etc/localtime | sed 's|.*zoneinfo/||')
    fi
    valid_tz "$tz" || tz=UTC
    printf '%s' "$tz"
}

# Compose's own rule for a project name: lowercase, only a-z, 0-9, - and _.
project_name() {
    printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | LC_ALL=C tr -cd 'a-z0-9_-' | sed 's/^[-_]*//'
}

# ------------------------------------------------------------------ the engine

ENGINE=
COMPOSE_KIND=
COMPOSE_TEXT=

# The one way this script runs Compose, whichever engine and provider it found.
compose() {
    case $COMPOSE_KIND in
        docker) docker compose "$@" ;;
        podman) podman compose "$@" ;;
        podman-compose) podman-compose "$@" ;;
    esac
}

engine() { "$ENGINE" "$@"; }

# Podman prints a notice before every external provider call; it says nothing new here.
PODMAN_COMPOSE_WARNING_LOGS=false
export PODMAN_COMPOSE_WARNING_LOGS

ENGINE_PROBLEM=

try_docker() {
    have docker || { ENGINE_PROBLEM="Docker is not installed"; return 1; }
    if ! v=$(docker compose version --short 2>/dev/null); then
        ENGINE_PROBLEM="Docker Compose v2 is missing (docker compose version fails). Install the Compose plugin: https://docs.docker.com/compose/install/"
        return 1
    fi
    if ! version_ge "$v" 2.20.0; then
        ENGINE_PROBLEM="Docker Compose $v is too old; 2.20 or newer is needed"
        return 1
    fi
    if ! out=$(docker info 2>&1 >/dev/null); then
        case $out in
            *"permission denied"*) ENGINE_PROBLEM="Docker runs, but this user may not use it. Add it to the docker group (sudo usermod -aG docker \$USER, then log in again), or run this with sudo" ;;
            *) ENGINE_PROBLEM="Docker is installed, but the Docker daemon isn't running. Start Docker (Docker Desktop, or sudo systemctl start docker) and try again" ;;
        esac
        return 1
    fi
    ENGINE=docker COMPOSE_KIND=docker COMPOSE_TEXT="docker compose" ENGINE_VERSION="Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?'), Compose $v"
}

try_podman() {
    have podman || { ENGINE_PROBLEM=${ENGINE_PROBLEM:-"Podman is not installed"}; return 1; }
    pv=$(podman version --format '{{.Client.Version}}' 2>/dev/null || podman --version | awk '{ print $NF }')
    if ! version_ge "$pv" 4.7.0; then
        ENGINE_PROBLEM="Podman $pv is too old; 4.7 or newer is needed"
        return 1
    fi
    if ! podman info >/dev/null 2>&1; then
        if [ "$(uname -s)" = Darwin ]; then
            ENGINE_PROBLEM="Podman can't reach its machine. Start it with: podman machine init (once), then podman machine start"
        else
            ENGINE_PROBLEM="podman info fails; run it to see why"
        fi
        return 1
    fi
    # podman compose runs a provider: docker-compose or podman-compose. Without one, try
    # podman-compose directly.
    if out=$(podman compose version 2>/dev/null); then
        kind=podman text="podman compose"
    elif have podman-compose && out=$(podman-compose version 2>/dev/null); then
        kind=podman-compose text=podman-compose
    else
        ENGINE_PROBLEM="Podman runs, but there is no Compose provider. Install podman-compose 1.5 or newer (pipx install podman-compose) or Docker Compose v2"
        return 1
    fi
    pc=$(printf '%s\n' "$out" | sed -n 's/.*podman-compose version \([0-9][0-9.]*\).*/\1/p' | head -n 1)
    dc=$(printf '%s\n' "$out" | sed -n 's/.*Docker Compose version v\{0,1\}\([0-9][0-9.]*\).*/\1/p' | head -n 1)
    if [ -n "$pc" ]; then
        version_ge "$pc" 1.5.0 || {
            ENGINE_PROBLEM="podman-compose $pc is too old: 1.5 or newer waits for the database's healthcheck and runs the stack's healthchecks right. pipx install podman-compose"
            return 1
        }
        provider="podman-compose $pc"
    elif [ -n "$dc" ]; then
        version_ge "$dc" 2.20.0 || { ENGINE_PROBLEM="Docker Compose $dc (Podman's provider) is too old; 2.20 or newer is needed"; return 1; }
        provider="Docker Compose $dc"
    else
        provider="an unknown Compose provider"
    fi
    ENGINE=podman COMPOSE_KIND=$kind COMPOSE_TEXT=$text ENGINE_VERSION="Podman $pv, $provider"
}

find_engine() {
    case $ENGINE_CHOICE in
        docker) try_docker || die 3 "$ENGINE_PROBLEM" ;;
        podman) try_podman || die 3 "$ENGINE_PROBLEM" ;;
        *)
            if ! try_docker; then
                first=$ENGINE_PROBLEM
                if have podman; then
                    ENGINE_PROBLEM=
                    try_podman || die 3 "$first. And: $ENGINE_PROBLEM"
                elif have docker; then
                    die 3 "$first"
                else
                    die 3 "Neither Docker nor Podman is installed. Install Docker (https://docs.docker.com/engine/install/) or Podman 4.7+ with podman-compose, then run this again"
                fi
            fi
            ;;
    esac
    ROOTLESS=false
    if [ "$ENGINE" = podman ]; then
        ROOTLESS=$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null || echo false)
    elif [ "$(docker info --format '{{.Host.Security.Rootless}}' 2>/dev/null || true)" = true ]; then
        # Podman behind the docker command (podman-docker).
        ROOTLESS=true
    elif docker info --format '{{.SecurityOptions}}' 2>/dev/null | grep -q rootless; then
        ROOTLESS=true
    fi
}

# Rootless Podman on Linux may not publish a port below the unprivileged start (1024).
check_port() {
    [ "$ROOTLESS" = true ] && [ "$(uname -s)" = Linux ] || return 0
    start=$(cat /proc/sys/net/ipv4/ip_unprivileged_port_start 2>/dev/null || echo 1024)
    if [ "$2" -lt "$start" ]; then
        die 1 "$1=$2: rootless Podman can't publish a port below $start. Pick a port of 1024 or higher, or allow it with: sudo sysctl net.ipv4.ip_unprivileged_port_start=$2"
    fi
}

# ------------------------------------------------------------------ the install folder

resolve_dir() {
    if [ -z "$DIR" ]; then
        if [ -f docker-compose.yml ] && [ -f .env ] && is_stack_compose docker-compose.yml; then
            DIR=.
        elif [ -n "$CLONE" ]; then
            DIR=$CLONE
        else
            DIR=./life-dashboard
            if [ "$COMMAND" = install ] && can_ask; then
                DIR=$(ask "Install in which folder?" "$DIR")
            fi
        fi
    fi
    case $DIR in "~"/*) DIR="$HOME/${DIR#"~/"}" ;; esac
}

# Containers and volumes are named after the project: COMPOSE_PROJECT_NAME, else the folder.
resolve_project() {
    PROJECT=$(env_get "$DIR/.env" COMPOSE_PROJECT_NAME)
    [ -n "$PROJECT" ] || PROJECT=${COMPOSE_PROJECT_NAME:-}
    [ -n "$PROJECT" ] || PROJECT=$(project_name "$(basename "$DIR")")
    [ -n "$PROJECT" ] || die 2 "can't make a Compose project name from the folder $DIR; set COMPOSE_PROJECT_NAME"
}

MCP_ON=0

# Compose in the install folder, with the project name and the mcp profile when it's on.
# podman-compose before 1.6 ignores COMPOSE_PROFILES, so the profile is always passed.
dc() {
    if [ "$MCP_ON" = 1 ]; then
        compose -p "$PROJECT" --profile mcp "$@"
    else
        compose -p "$PROJECT" "$@"
    fi
}

mcp_from_env() {
    MCP_ON=0
    case ",$(env_get "$DIR/.env" COMPOSE_PROFILES)," in *,mcp,*) MCP_ON=1 ;; esac
}

# The container of a service, found by the labels both Docker Compose and podman-compose set.
cid() {
    engine ps -aq --filter "label=com.docker.compose.project=$PROJECT" \
        --filter "label=com.docker.compose.service=$1" 2>/dev/null | head -n 1
}

# "running healthy", "running starting", "exited", ... Podman before 4.3 calls it Healthcheck.
container_state() {
    engine inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$1" 2>/dev/null ||
        engine inspect -f '{{.State.Status}} {{.State.Healthcheck.Status}}' "$1" 2>/dev/null ||
        echo unknown
}

# ------------------------------------------------------------------ the files

# Copies docker-compose.yml and the settings template of this version to $1 and $2.
get_files() {
    if [ -n "$CLONE" ]; then
        if [ "$(cd "$DIR" && pwd -P)" != "$CLONE" ]; then
            cp "$CLONE/docker-compose.yml" "$1"
        fi
        cp "$CLONE/.env.example" "$2"
    else
        fetch "$DOWNLOAD/docker-compose.yml" "$1.download" || die 1 "could not download $DOWNLOAD/docker-compose.yml"
        mv "$1.download" "$1"
        fetch "$DOWNLOAD/env.example" "$2" || die 1 "could not download $DOWNLOAD/env.example"
    fi
}

# A compose file of this stack, not of another project that happens to be in the folder.
is_stack_compose() { grep -q 'ghcr.io/owen282000/life-dashboard-receiver' "$1" 2>/dev/null; }

# Stops before touching a folder that holds another project.
check_own_folder() {
    if [ -f docker-compose.yml ] && ! is_stack_compose docker-compose.yml; then
        die 2 "$DIR has a docker-compose.yml of another project. Pick an empty folder with --dir"
    fi
    if [ -f .env ] && ! grep -q '^WEBHOOK_SECRET=' .env; then
        die 2 "$DIR has a .env of another project. Pick an empty folder with --dir"
    fi
}

compose_version() {
    grep -o 'LIFEDASH_VERSION:-[0-9][0-9.]*' "$1" 2>/dev/null | head -n 1 | cut -d- -f2
}

# Fills the settings template ($1) with this install's values, in place.
fill_env() {
    file=$1
    secret=$(rand_hex 32)
    set_key "$file" WEBHOOK_SECRET "$secret"
    set_key "$file" PUBLIC_URL "$PUBLIC_URL_VALUE"
    set_key "$file" TZ "$TZ_VALUE"
    set_key "$file" GRAFANA_PASSWORD "$(rand_hex 16)"
    set_key "$file" GRAFANA_DB_PASSWORD "$(rand_hex 16)"
    set_key "$file" DASH_DB_PASSWORD "$(rand_hex 16)"
    api=
    if is_on "$WITH_API"; then
        api=$(rand_hex 32)
        set_key "$file" API_TOKEN "$api"
    fi
    if is_on "$WITH_MCP"; then
        # At least 16 characters, and never the same as API_TOKEN.
        mcp=$(rand_hex 32)
        while [ "$mcp" = "$api" ]; do mcp=$(rand_hex 32); done
        set_key "$file" MCP_TOKEN "$mcp"
        set_key "$file" COMPOSE_PROFILES mcp
    fi
    for key in RECEIVER_PORT GRAFANA_PORT MCP_PORT LIFEDASH_VERSION; do
        eval "value=\${$key:-}"
        # shellcheck disable=SC2154 # value is set by the eval above
        [ -z "$value" ] || set_key "$file" "$key" "$value"
    done
    header="# Written by install.sh $VERSION on $(date +%Y-%m-%d). The secret, the passwords and the tokens are random."
    H=$header awk 'NR == 1 && /^# Copy to \.env/ { print ENVIRON["H"]; next } { print }' "$file" >"$file.tmp"
    mv "$file.tmp" "$file"
}

choose_settings() {
    PORT_R=${RECEIVER_PORT:-8080}
    LAN_IP=$(detect_ip)
    if [ "$URL_GIVEN" = 1 ]; then
        PUBLIC_URL_VALUE=$URL_IN
    elif [ -n "$LAN_IP" ] && [ "$ENV_ONLY" = 0 ]; then
        # With --env-only the stack runs elsewhere, so this machine's address means nothing.
        PUBLIC_URL_VALUE="http://$LAN_IP:$PORT_R"
    else
        PUBLIC_URL_VALUE=
    fi
    # --tz must be right; the shell's own TZ may be a POSIX rule such as CET-1CEST, so it's only a hint.
    if [ -n "$TZ_IN" ]; then
        valid_tz "$TZ_IN" || die 2 "--tz $TZ_IN is not a time zone name such as Europe/Amsterdam"
        TZ_VALUE=$TZ_IN
    elif [ -n "$TZ_SHELL" ] && valid_tz "$TZ_SHELL"; then
        TZ_VALUE=$TZ_SHELL
    else
        TZ_VALUE=$(detect_tz)
    fi
    if can_ask && [ "$ENV_ONLY" = 0 ]; then
        say ""
        say "A few questions. Enter keeps the answer in brackets."
        if [ "$URL_GIVEN" = 0 ]; then
            PUBLIC_URL_VALUE=$(ask "The address your phone reaches this machine on" "${PUBLIC_URL_VALUE:-none}")
            [ "$PUBLIC_URL_VALUE" != none ] || PUBLIC_URL_VALUE=
        fi
        TZ_VALUE=$(ask "Your time zone" "$TZ_VALUE")
        if [ -z "$WITH_API" ]; then
            if ask_yes "Switch on the read-only API (docs/api.md)?" n; then WITH_API=1; else WITH_API=0; fi
        fi
        if [ -z "$WITH_MCP" ]; then
            if ask_yes "Switch on the MCP server for your own AI (docs/mcp.md)?" n; then WITH_MCP=1; else WITH_MCP=0; fi
        fi
    fi
    valid_tz "$TZ_VALUE" || die 2 "'$TZ_VALUE' is not a time zone name such as Europe/Amsterdam"
    case $PUBLIC_URL_VALUE in
        "" | http://* | https://*) ;;
        *) die 2 "PUBLIC_URL starts with http:// or https://, such as http://192.168.1.10:8080" ;;
    esac
}

# ------------------------------------------------------------------ --env-only

env_only() {
    # The temporary files hold every secret: only this user may read them.
    umask 077
    # The notes go to stderr, so stdout is the .env alone, or nothing with --output.
    exec 3>&2
    if [ -n "$OUTPUT" ] && [ -e "$OUTPUT" ]; then
        die 1 "$OUTPUT exists already; it is never overwritten. Pick another --output or remove it"
    fi
    choose_settings
    tmp=$(mktemp "${TMPDIR:-/tmp}/lifedash-env.XXXXXX")
    trap 'rm -f "$tmp" "$tmp.tmp"' EXIT
    if [ -n "$CLONE" ]; then
        cp "$CLONE/.env.example" "$tmp"
    else
        fetch "$DOWNLOAD/env.example" "$tmp" || die 1 "could not download $DOWNLOAD/env.example"
    fi
    fill_env "$tmp"
    if [ -z "$PUBLIC_URL_VALUE" ]; then
        # Empty rather than a made-up address: an empty PUBLIC_URL makes no pairing code with a
        # wrong address, and /pair still works with the address you open it with.
        M="# CHANGE ME: PUBLIC_URL is the address your phone reaches the receiver on, such as http://192.168.1.10:8080" \
            awk '/^PUBLIC_URL=/ { print ENVIRON["M"] } { print }' "$tmp" >"$tmp.tmp"
        mv "$tmp.tmp" "$tmp"
    fi
    if [ -n "$OUTPUT" ]; then
        (umask 077 && cp "$tmp" "$OUTPUT")
        say "Wrote $OUTPUT (only you can read it)."
    else
        cat "$tmp"
    fi
    say ""
    say "The docker-compose.yml that goes with it, Life Dashboard Stack $VERSION:"
    say "  $DOWNLOAD/docker-compose.yml"
    if [ -z "$PUBLIC_URL_VALUE" ]; then
        say "PUBLIC_URL is empty (CHANGE ME): set it to the address your phone reaches the receiver on,"
        say "such as http://<address of the NAS>:8080, or pass --public-url."
    fi
    say "TZ=$TZ_VALUE, from this machine or --tz."
    say "Paste the compose file as a stack or project and this .env as its environment:"
    say "  $REPO/blob/$VERSION/docs/install.md#on-a-nas-or-in-portainer"
}

# ------------------------------------------------------------------ install

wait_ready() {
    step "Waiting until the stack answers (up to $TIMEOUT s)"
    port_r=$(env_get .env RECEIVER_PORT)
    port_r=${port_r:-8080}
    waited=0
    ready=0
    while [ "$waited" -lt "$TIMEOUT" ]; do
        id=$(cid receiver)
        state=$( [ -z "$id" ] || container_state "$id")
        case $state in
            exited* | dead*)
                say "The receiver stopped. Its log:"
                dc logs --tail 40 receiver >&3 2>&1 || true
                return 1
                ;;
        esac
        if [ "$(http_code "http://127.0.0.1:$port_r/health" </dev/null)" = 200 ] || [ "$state" = "running healthy" ]; then
            ready=1
            break
        fi
        sleep 3
        waited=$((waited + 3))
    done
    if [ "$ready" = 0 ]; then
        say "The receiver did not answer within $TIMEOUT s."
        dc ps >&3 2>&1 || true
        dc logs --tail 40 receiver >&3 2>&1 || true
        return 1
    fi
    say "The receiver answers on port $port_r."
    port_g=$(env_get .env GRAFANA_PORT)
    port_g=${port_g:-3000}
    while [ "$waited" -lt "$TIMEOUT" ]; do
        [ "$(http_code "http://127.0.0.1:$port_g/api/health" </dev/null)" = 200 ] && break
        sleep 3
        waited=$((waited + 3))
    done
    if [ "$waited" -ge "$TIMEOUT" ]; then
        warn "Grafana does not answer on port $port_g yet; look at: $COMPOSE_TEXT logs grafana"
    else
        say "Grafana answers on port $port_g."
    fi
}

start_stack() {
    if [ "$PULL" = 1 ]; then
        step "Pulling the images"
        dc pull || die 1 "pulling the images failed. Check the network, or run with --no-pull to use the images on this machine"
    fi
    step "Starting the stack"
    if ! dc up -d; then
        die 1 "$COMPOSE_TEXT up failed. If a port is in use, set RECEIVER_PORT or GRAFANA_PORT in $DIR/.env and run this again"
    fi
    wait_ready || exit 4
}

show_pairing() {
    id=$(cid receiver)
    [ -n "$id" ] || return 0
    step "Pair a phone"
    if ! engine exec "$id" python pairing.py "$LABEL" >&3 2>&1; then
        if [ "$ROOTLESS" = true ]; then
            warn "could not show the pairing code; run: cd $DIR && $COMPOSE_TEXT exec receiver python pairing.py $LABEL"
        else
            warn "could not show the pairing code; open http://<this machine>:$(port_of RECEIVER_PORT 8080)/pair instead"
        fi
    fi
}

summary() {
    ip=${LAN_IP:-$(detect_ip)}
    host=${ip:-localhost}
    port_g=$(env_get .env GRAFANA_PORT)
    port_m=$(env_get .env MCP_PORT)
    public=$(env_get .env PUBLIC_URL)
    receiver=${public:-http://$host:$(port_of RECEIVER_PORT 8080)}
    # The /pair page is on unless PAIRING_PAGE switches it off, as the receiver reads it.
    case $(env_get .env PAIRING_PAGE | tr '[:upper:]' '[:lower:]') in 0 | false | off | no) page= ;; *) page=${receiver%/}/pair ;; esac
    step "Life Dashboard Stack is running"
    say "Folder:    $DIR (Compose project $PROJECT, $ENGINE_VERSION)"
    say "Grafana:   http://$host:${port_g:-3000}  user admin, the password is GRAFANA_PASSWORD in $DIR/.env"
    say "Receiver:  $receiver (PUBLIC_URL)"
    [ -z "$page" ] || say "Pairing:   $page (another phone: ?label=<label>)"
    if [ -n "$(env_get .env API_TOKEN)" ]; then
        say "API:       on, under /api/ on the receiver, with API_TOKEN from .env (docs/api.md)"
    fi
    if [ "$MCP_ON" = 1 ]; then
        say "MCP:       http://$host:${port_m:-8090}/mcp with MCP_TOKEN from .env (docs/mcp.md)"
    fi
    if [ "$SHOW_PASSWORD" = 1 ]; then
        say ""
        say "Grafana admin password (shown once, as you asked): $(env_get .env GRAFANA_PASSWORD)"
    fi
    say ""
    say "Next:"
    say "  1. In Life Dashboard Companion, open the Webhook card, tap Scan a pairing code and scan"
    say "     the code above. Then Sync Now and Backfill."
    if [ -n "$page" ]; then
        say "  2. Another phone gets its own label: open $page?label=<label>"
        say "     from your own network, or run: cd $DIR && $COMPOSE_TEXT exec receiver python pairing.py <label>"
    else
        say "  2. Another phone gets its own label: cd $DIR && $COMPOSE_TEXT exec receiver python pairing.py <label>"
    fi
    say "  3. Something wrong? $ONE_LINER -s -- doctor --dir $DIR"
    say "  Upgrade later with the same line and --upgrade --dir $DIR."
}

install() {
    resolve_dir
    find_engine
    mkdir -p "$DIR"
    DIR=$(cd "$DIR" && pwd -P)
    cd "$DIR"
    resolve_project
    say "Life Dashboard Stack $VERSION into $DIR ($ENGINE_VERSION)"
    [ -z "$CLONE" ] || say "From the clone in $CLONE."

    check_own_folder
    if [ -f .env ]; then
        mcp_from_env
        if [ "$UPGRADE" = 1 ]; then
            upgrade
        else
            say "$DIR/.env exists, so this install is kept as it is: .env and docker-compose.yml stay."
            if [ ! -f docker-compose.yml ]; then
                tmp=$(mktemp "${TMPDIR:-/tmp}/lifedash-env.XXXXXX")
                get_files docker-compose.yml "$tmp"
                rm -f "$tmp"
            fi
            if [ "$(compose_version docker-compose.yml)" != "$VERSION" ]; then
                say "Its docker-compose.yml is of $(compose_version docker-compose.yml); run this again with --upgrade to move it to $VERSION."
            fi
            [ "$START" = 1 ] || exit 0
            PULL=0
        fi
        if is_on "$WITH_API" || is_on "$WITH_MCP"; then
            say "--with-api and --with-mcp only apply to a new .env; docs/api.md and docs/mcp.md say how to switch them on."
        fi
        start_stack
        show_pairing
        summary
        return
    fi

    for key in RECEIVER_PORT GRAFANA_PORT MCP_PORT; do
        eval "value=\${$key:-}"
        value=${value##*:}
        case $value in '' ) ;; *[!0-9]*) die 2 "$key is a port number, or an address and a port" ;; *) check_port "$key" "$value" ;; esac
    done
    if [ -n "$(engine volume ls -q --filter "name=${PROJECT}_pgdata" 2>/dev/null)" ]; then
        warn "volumes of an earlier install of the project $PROJECT exist. They are kept, so your data is too, but Grafana keeps the admin password it had then."
    fi
    choose_settings
    if [ -z "$PUBLIC_URL_VALUE" ] && [ "$ROOTLESS" = true ]; then
        warn "no PUBLIC_URL: set it in .env and run $COMPOSE_TEXT up -d, then the pairing code is in the receiver's log"
    elif [ -z "$PUBLIC_URL_VALUE" ]; then
        warn "no PUBLIC_URL: the pairing code then comes from the /pair page. Set PUBLIC_URL in .env later and run $COMPOSE_TEXT up -d"
    fi

    step "Writing docker-compose.yml and .env"
    get_files docker-compose.yml .env.new
    # .env holds the secret and the passwords: only this user may read it.
    chmod 600 .env.new
    umask 077
    fill_env .env.new
    set_key .env.new COMPOSE_PROJECT_NAME "$PROJECT"
    if [ "$ROOTLESS" = true ]; then
        # Rootless port forwarding (Podman, rootless Docker) makes every request look internal,
        # so /pair would answer anyone who reaches the port. The installer shows the code itself.
        set_key .env.new PAIRING_PAGE 0
        say "PAIRING_PAGE=0: a rootless engine hides where a request comes from, so the /pair page stays off."
    fi
    mv .env.new .env
    umask 022
    if [ -f docker-compose.override.yml ]; then say "docker-compose.override.yml is there, and Compose uses it too."; fi
    if is_on "$WITH_MCP"; then MCP_ON=1; fi
    say "Wrote .env with a random WEBHOOK_SECRET, passwords$(is_on "$WITH_API" && printf ', API_TOKEN')$(is_on "$WITH_MCP" && printf ', MCP_TOKEN'). Only you can read it."
    say "PUBLIC_URL=${PUBLIC_URL_VALUE:-(empty)}  TZ=$TZ_VALUE"

    if [ "$START" = 0 ]; then
        say "Not started (--no-start). Start it with: cd $DIR && $COMPOSE_TEXT up -d"
        return
    fi
    start_stack
    show_pairing
    summary
}

upgrade() {
    old=$(compose_version docker-compose.yml)
    if [ -n "$old" ] && [ "$old" != "$VERSION" ] && version_ge "$old" "$VERSION"; then
        die 1 "this folder runs $old, newer than this installer ($VERSION). Use the installer of $old or later: $ONE_LINER -s -- --upgrade"
    fi
    if [ "$old" = "$VERSION" ]; then
        step "Already on $VERSION: fetching its docker-compose.yml again and pulling the images, keeping .env"
    else
        step "Upgrading ${old:-?} to $VERSION, keeping .env"
    fi
    tmp=$(mktemp "${TMPDIR:-/tmp}/lifedash-env.XXXXXX")
    prev=
    if [ -f docker-compose.yml ]; then
        prev=$(mktemp "${TMPDIR:-/tmp}/lifedash-compose.XXXXXX")
        cp docker-compose.yml "$prev"
    fi
    get_files docker-compose.yml "$tmp"
    # Any change to the compose file, also one made by hand, is kept as a backup.
    if [ -n "$prev" ]; then
        if ! cmp -s "$prev" docker-compose.yml; then
            bak="docker-compose.yml.${old:-previous}.bak"
            cp "$prev" "$bak"
            say "Kept the old one as $bak."
        fi
        rm -f "$prev"
    fi
    # Settings this version knows and .env doesn't have yet: they have defaults, so it's only news.
    new=$(sed -n 's/^\([A-Z][A-Z_]*\)=.*/\1/p' "$tmp" | while read -r key; do
        grep -q "^$key=" .env || printf '%s ' "$key"
    done)
    rm -f "$tmp"
    [ -z "$new" ] || say "Settings you haven't set, which keep their defaults: $new(see env.example of $VERSION)."
    pinned=$(env_get .env LIFEDASH_VERSION)
    case $pinned in
        "" | "$VERSION") ;;
        *) warn "LIFEDASH_VERSION=$pinned in .env decides which images run. Set it to $VERSION, or empty for the version of docker-compose.yml, and run $COMPOSE_TEXT pull && $COMPOSE_TEXT up -d" ;;
    esac
    [ "$START" = 1 ] || exit 0
}

# ------------------------------------------------------------------ doctor

NPASS=0 NWARN=0 NFAIL=0

res() {
    printf '%s  %s: %s\n' "$1" "$2" "$3"
    if [ -n "${4:-}" ]; then printf '      fix: %s\n' "$4"; fi
    case $1 in PASS) NPASS=$((NPASS + 1)) ;; WARN) NWARN=$((NWARN + 1)) ;; FAIL) NFAIL=$((NFAIL + 1)) ;; esac
}

running() { case $(container_state "$1") in running*) return 0 ;; *) return 1 ;; esac; }

# Where a container port is published: "all" (every interface), "loopback", or "none".
published() {
    out=$(engine port "$1" "$2" 2>/dev/null || true)
    case $out in
        *0.0.0.0:* | *"[::]:"* | *":::"*) echo all ;;
        *127.0.0.1:* | *"[::1]:"*) echo loopback ;;
        *) echo none ;;
    esac
}

doctor() {
    resolve_dir
    [ -d "$DIR" ] || die 3 "no install in $DIR: give the folder with --dir"
    DIR=$(cd "$DIR" && pwd -P)
    cd "$DIR"
    [ -f docker-compose.yml ] && [ -f .env ] || die 3 "no install in $DIR (docker-compose.yml and .env): give the folder with --dir"
    is_stack_compose docker-compose.yml || die 3 "the docker-compose.yml in $DIR is not the Life Dashboard Stack's: give the folder with --dir"
    find_engine
    resolve_project
    mcp_from_env
    say "Life Dashboard doctor for $DIR (Compose project $PROJECT)"
    res PASS engine "$ENGINE_VERSION"

    if dc config >/dev/null 2>&1; then
        res PASS "compose file" "docker-compose.yml and .env read fine, images of $(compose_version docker-compose.yml)$( [ -z "$(env_get .env LIFEDASH_VERSION)" ] || printf ' (LIFEDASH_VERSION=%s)' "$(env_get .env LIFEDASH_VERSION)")"
    else
        res FAIL "compose file" "$COMPOSE_TEXT config fails" "run $COMPOSE_TEXT config in $DIR to see the error"
    fi
    if [ -n "$(find .env -perm -004 2>/dev/null)" ]; then
        res WARN ".env" "every user of this machine can read it, and it holds the secret and the passwords" "chmod 600 $DIR/.env"
    fi

    services="db receiver grafana"
    [ "$MCP_ON" = 1 ] && services="$services mcp"
    for svc in $services; do
        id=$(cid "$svc")
        if [ -z "$id" ]; then
            res FAIL "container $svc" "not there" "cd $DIR && $COMPOSE_TEXT up -d"
            continue
        fi
        state=$(container_state "$id")
        case $state in
            "running healthy") res PASS "container $svc" "running, healthy" ;;
            "running starting") res WARN "container $svc" "running, its healthcheck hasn't passed yet" "wait a minute; if it stays like this: $COMPOSE_TEXT logs $svc" ;;
            "running unhealthy") res FAIL "container $svc" "running, but unhealthy" "$COMPOSE_TEXT logs $svc" ;;
            "running "*)
                if [ "$svc" = grafana ]; then
                    res PASS "container $svc" "running"
                else
                    res WARN "container $svc" "running, but its healthcheck reports nothing" "on Podman, healthchecks need systemd (podman healthcheck run $id runs one by hand)"
                fi
                ;;
            *) res FAIL "container $svc" "${state%% *}" "$COMPOSE_TEXT logs $svc, then $COMPOSE_TEXT up -d" ;;
        esac
    done

    port_r=$(port_of RECEIVER_PORT 8080)
    port_g=$(port_of GRAFANA_PORT 3000)
    port_m=$(port_of MCP_PORT 8090)

    rid=$(cid receiver)
    if [ -n "$rid" ] && running "$rid"; then
        case $(published "$rid" 8080/tcp) in
            all) res PASS "receiver port" "$port_r, published on every interface, so the network can reach it" ;;
            loopback) res WARN "receiver port" "$port_r is published on 127.0.0.1 only, which a phone can't reach" "publish it on every interface, or put a reverse proxy in front" ;;
            *) res FAIL "receiver port" "not published" "check RECEIVER_PORT in .env and docker-compose.yml" ;;
        esac
    fi
    gid=$(cid grafana)
    if [ -n "$gid" ] && running "$gid"; then
        case $(published "$gid" 3000/tcp) in
            all) res PASS "Grafana port" "$port_g, on every interface (keep it on your own network or a VPN)" ;;
            loopback) res PASS "Grafana port" "$port_g, on 127.0.0.1 only" ;;
            *) res FAIL "Grafana port" "not published" "check GRAFANA_PORT in .env" ;;
        esac
    fi

    code=$(http_code "http://127.0.0.1:$port_r/health" </dev/null)
    if [ "$code" = 200 ]; then
        res PASS "receiver from this machine" "http://127.0.0.1:$port_r/health answers 200"
    else
        res FAIL "receiver from this machine" "http://127.0.0.1:$port_r/health answers $code" "$COMPOSE_TEXT logs receiver"
    fi
    public=$(env_get .env PUBLIC_URL)
    case $public in
        "") ;;
        *://localhost* | *://127.*) ;;
        *)
            code=$(http_code "${public%/}/health" </dev/null)
            if [ "$code" = 200 ]; then
                res PASS "PUBLIC_URL from this machine" "${public%/}/health answers 200"
            else
                res WARN "PUBLIC_URL from this machine" "${public%/}/health answers $code" "check that the address is this machine's and that no firewall blocks port $port_r (Linux: ufw or firewalld; macOS: System Settings, Network, Firewall). A machine that can't reach its own public address can still be fine for the phone"
            fi
            ;;
    esac

    code=$(http_code "http://127.0.0.1:$port_g/api/health" </dev/null)
    if [ "$code" = 200 ]; then
        res PASS "Grafana" "http://127.0.0.1:$port_g answers"
        pw=$(env_get .env GRAFANA_PASSWORD)
        if [ -n "$pw" ] && have curl; then
            code=$(printf 'user = "admin:%s"\n' "$pw" | http_code "http://127.0.0.1:$port_g/api/user")
            if [ "$code" = 200 ]; then
                res PASS "Grafana login" "admin logs in with GRAFANA_PASSWORD from .env"
            elif [ "$code" = 401 ]; then
                res WARN "Grafana login" "GRAFANA_PASSWORD in .env doesn't log in admin. Fine if you changed it in Grafana: Grafana reads it only on its first start" "to reset it: $COMPOSE_TEXT exec grafana grafana cli admin reset-admin-password <new password>"
            fi
        fi
    else
        res FAIL "Grafana" "http://127.0.0.1:$port_g/api/health answers $code" "$COMPOSE_TEXT logs grafana"
    fi

    api=$(env_get .env API_TOKEN)
    mcp=$(env_get .env MCP_TOKEN)
    if [ -n "$mcp" ] && [ "$mcp" = "$api" ]; then
        res WARN "MCP_TOKEN" "the same as API_TOKEN" "give the MCP server a token of its own (openssl rand -hex 32)"
    fi
    if [ "$MCP_ON" = 1 ]; then
        if ! have curl; then
            res INFO "MCP server" "not checked over HTTP: that needs curl"
        elif [ "${#mcp}" -lt 16 ]; then
            res FAIL "MCP server" "MCP_TOKEN is empty or shorter than 16 characters, so the server won't start" "set MCP_TOKEN to the output of openssl rand -hex 32"
        else
            list='{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
            health=$(http_code "http://127.0.0.1:$port_m/health" </dev/null)
            without=$(printf 'request = "POST"\ndata = %s\n' "\"$(printf '%s' "$list" | sed 's/"/\\"/g')\"" | http_code "http://127.0.0.1:$port_m/mcp")
            with=$(printf 'request = "POST"\nheader = "Authorization: Bearer %s"\nheader = "Content-Type: application/json"\nheader = "Accept: application/json, text/event-stream"\ndata = %s\n' \
                "$mcp" "\"$(printf '%s' "$list" | sed 's/"/\\"/g')\"" | http_code "http://127.0.0.1:$port_m/mcp")
            if [ "$health" = 200 ] && [ "$without" = 401 ] && [ "$with" = 200 ]; then
                res PASS "MCP server" "port $port_m: answers with MCP_TOKEN and refuses without it"
            else
                res FAIL "MCP server" "port $port_m: /health $health, /mcp $with with the token and $without without" "$COMPOSE_TEXT logs mcp"
            fi
        fi
    elif [ -n "$mcp" ]; then
        res INFO "MCP server" "MCP_TOKEN is set but the mcp profile is not on" "add COMPOSE_PROFILES=mcp to .env and run $COMPOSE_TEXT up -d (docs/mcp.md)"
    fi

    dbid=$(cid db)
    if [ -n "$dbid" ]; then
        use=$(engine exec "$dbid" df -P /var/lib/postgresql 2>/dev/null | awk 'NR == 2 { sub(/%/, "", $5); print $5 }')
        avail=$(engine exec "$dbid" df -Ph /var/lib/postgresql 2>/dev/null | awk 'NR == 2 { print $4 }')
        if [ -z "$use" ]; then
            :
        elif [ "$use" -ge 90 ]; then
            res WARN "disk" "the database volume is $use% full ($avail free)" "free up space; Postgres stops writing when the disk is full"
        else
            res PASS "disk" "the database volume is $use% full ($avail free)"
        fi
    fi

    if [ "$ENGINE" = podman ] && [ "$ROOTLESS" = true ] && have systemctl; then
        if ! systemctl --user is-enabled podman-restart.service >/dev/null 2>&1; then
            res WARN "start at boot" "rootless Podman doesn't start the containers after a reboot by itself" "systemctl --user enable podman-restart.service, and loginctl enable-linger \$USER"
        fi
    fi

    in=0
    if [ -n "$rid" ] && running "$rid"; then
        say ""
        say "Inside the receiver (python doctor.py):"
        engine exec "$rid" python doctor.py || in=$?
        if [ "$in" = 2 ]; then
            res WARN "doctor.py" "this receiver image has no doctor.py; it comes with 1.1.0" "upgrade: $ONE_LINER -s -- --upgrade --dir $DIR"
            in=0
        fi
    else
        res FAIL "doctor.py" "not run: the receiver isn't running" "start it ($COMPOSE_TEXT up -d), then run the doctor again"
    fi
    say ""
    say "On this machine: $NPASS passed, $NWARN warnings, $NFAIL failed."
    [ "$NFAIL" = 0 ] && [ "$in" = 0 ]
}

# ------------------------------------------------------------------ main

if [ "$ENV_ONLY" = 1 ]; then
    env_only
elif [ "$COMMAND" = doctor ]; then
    doctor || exit 1
else
    install
fi
