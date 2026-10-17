#!/usr/bin/env bash
#
# Checks that the files of a release agree with its version, the way scripts/prepare-release.sh
# leaves them: the version is strict semver and higher than every other version tag, the Helm
# chart's version, appVersion and four images are the version, docker-compose.yml defaults all
# five image lines to it, install.sh carries it, server.json has it as the MCP server's version
# and image tag under the name the MCP image's label carries, mcp/server.py reports it, and
# CHANGELOG.md has a section for it. The release workflow runs this on the tag, and the test
# workflow on a throwaway checkout after prepare-release.sh.
#
# Usage: .github/scripts/check-release.sh 1.2.3
#
set -euo pipefail
cd "$(dirname "$0")/../.."

V="${1:?usage: check-release.sh <version X.Y.Z>}"
fail=0
problem() { echo "::error::$*"; fail=1; }

if ! [[ "$V" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "::error::$V is not strict semver (X.Y.Z)"
    exit 1
fi

prev=$(git tag --list | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | grep -vx "$V" | sort -V | tail -1 || true)
if [ -n "$prev" ] && [ "$(printf '%s\n%s\n' "$prev" "$V" | sort -V | tail -1)" != "$V" ]; then
    problem "$V is not higher than the latest version $prev"
fi

# One version for everything: the chart is released with the stack, under the stack's version.
chart=charts/life-dashboard/Chart.yaml
for key in version appVersion; do
    got=$(awk -v k="$key:" '$1 == k { gsub(/"/, "", $2); print $2; exit }' "$chart")
    [ "$got" = "$V" ] || problem "$chart has $key ${got:-(none)}; it should be $V (scripts/prepare-release.sh $V sets it)"
done

# The images Artifact Hub lists for the chart (artifacthub.io/images).
for image in receiver grafana db mcp; do
    grep -qF "image: ghcr.io/owen282000/life-dashboard-$image:$V" "$chart" \
        || problem "$chart should list ghcr.io/owen282000/life-dashboard-$image:$V in artifacthub.io/images"
done

# The MCP Registry entry, and the label on the MCP image that proves the image is this server's.
label=$(sed -n 's/.*io\.modelcontextprotocol\.server\.name="\([^"]*\)".*/\1/p' mcp/Dockerfile)
python3 - "$V" "$label" <<'PY' || fail=1
import json, sys
v, label = sys.argv[1:]
s = json.load(open("server.json"))
problems = []
if s.get("version") != v:
    problems.append(f"server.json has version {s.get('version')}; it should be {v} (scripts/prepare-release.sh {v} sets it)")
if not label or s.get("name") != label:
    problems.append(f"server.json has name {s.get('name')}, but mcp/Dockerfile labels the image {label or '(no label)'}")
import re
m = re.search(r'^VERSION = "([^"]*)"', open("mcp/server.py").read(), re.M)
if not m or m.group(1) != v:
    problems.append(f"mcp/server.py reports version {m.group(1) if m else '(none)'}; it should be {v} (scripts/prepare-release.sh {v} sets it)")
images = [p.get("identifier") for p in s.get("packages", [])]
if images != [f"ghcr.io/owen282000/life-dashboard-mcp:{v}"]:
    problems.append(f"server.json should have one package, ghcr.io/owen282000/life-dashboard-mcp:{v} (found {images})")
for p in problems:
    print(f"::error::{p}")
sys.exit(1 if problems else 0)
PY

n=$(grep -cF ":\${LIFEDASH_VERSION:-$V}" docker-compose.yml || true)
if [ "$n" != 5 ]; then
    problem "docker-compose.yml should default all five image lines to $V (found $n)"
    grep -n "image:" docker-compose.yml
fi

got=$(sed -n 's/^VERSION=//p' install.sh)
[ "$got" = "$V" ] || problem "install.sh has VERSION=${got:-(none)}; it should be $V (scripts/prepare-release.sh $V sets it)"

notes=$(.github/scripts/release-notes.sh "$V")
if [ -z "$notes" ]; then
    problem "CHANGELOG.md has no section '## [$V]', or it is empty"
fi

exit "$fail"
