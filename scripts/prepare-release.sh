#!/usr/bin/env bash
#
# Prepares a release for tagging: moves the Unreleased section of CHANGELOG.md under the new
# version, points the image defaults in docker-compose.yml (and the Helm chart, once there is
# one) at it, and updates the version links at the bottom of the changelog. The release
# workflow checks all of these against the tag it builds.
#
# Usage: scripts/prepare-release.sh 1.1.0
#
set -euo pipefail
cd "$(dirname "$0")/.."

V="${1:?usage: prepare-release.sh <version X.Y.Z>}"
[[ "$V" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Not strict semver: $V" >&2; exit 1; }

PREV=$(git tag --list | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1 || true)
if [ -n "$PREV" ] && [ "$(printf '%s\n%s\n' "$PREV" "$V" | sort -V | tail -1)" != "$V" ] || [ "$PREV" = "$V" ]; then
    echo "$V is not higher than the latest version $PREV" >&2
    exit 1
fi
grep -q "^## \[$V\]" CHANGELOG.md && { echo "CHANGELOG.md already has a section for $V" >&2; exit 1; }
grep -q '^## \[Unreleased\]' CHANGELOG.md || { echo "CHANGELOG.md has no ## [Unreleased] section" >&2; exit 1; }

TODAY=$(date +%Y-%m-%d)
REPO=https://github.com/owen282000/life-dashboard-stack
python3 - "$V" "$PREV" "$TODAY" "$REPO" <<'PY'
import re, sys
v, prev, today, repo = sys.argv[1:]
p = "CHANGELOG.md"
s = open(p).read()
s = s.replace("## [Unreleased]\n", f"## [Unreleased]\n\n## [{v}] - {today}\n", 1)
s = re.sub(r"^\[Unreleased\]: .*\n", "", s, flags=re.M)
links = f"[Unreleased]: {repo}/compare/{v}...HEAD\n"
links += f"[{v}]: {repo}/compare/{prev}...{v}\n" if prev else f"[{v}]: {repo}/releases/tag/{v}\n"
m = re.search(r"^\[\d+\.\d+\.\d+\]: ", s, flags=re.M)
s = s[:m.start()] + links + s[m.start():] if m else s.rstrip("\n") + "\n\n" + links
open(p, "w").write(s)
PY

# Every image default in docker-compose.yml follows the version.
# shellcheck disable=SC2016 # the ${...} is literal compose syntax, not a shell expansion
sed -E -i.bak 's/\$\{LIFEDASH_VERSION:-[0-9]+\.[0-9]+\.[0-9]+\}/${LIFEDASH_VERSION:-'"$V"'}/g' docker-compose.yml && rm docker-compose.yml.bak

CHART=charts/life-dashboard/Chart.yaml
if [ -f "$CHART" ]; then
    sed -E -i.bak -e 's/^version: .*/version: '"$V"'/' -e 's/^appVersion: .*/appVersion: "'"$V"'"/' "$CHART" && rm "$CHART.bak"
fi

echo "Prepared $V (previous ${PREV:-none}). Check the changelog section, then commit and: git tag $V && git push origin $V"
