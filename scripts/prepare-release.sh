#!/usr/bin/env bash
#
# Prepares a release for tagging: moves the Unreleased section of CHANGELOG.md under the new
# version, points the image defaults in docker-compose.yml, the version in install.sh, the Helm
# chart's version, appVersion and image list, the version and image of the MCP server in
# server.json and the version mcp/server.py reports at it, the version receiver/receiver.py
# names in its answer to a signed payload, gives the chart that section's
# entries as its Artifact Hub changelog, and updates the version links at the bottom of the
# changelog. The release workflow checks all of these against the tag it builds.
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

# install.sh fetches the release files of the version it carries.
sed -E -i.bak 's/^VERSION=[0-9]+\.[0-9]+\.[0-9]+$/VERSION='"$V"'/' install.sh && rm install.sh.bak

CHART=charts/life-dashboard/Chart.yaml
if [ -f "$CHART" ]; then
    sed -E -i.bak -e 's/^version: .*/version: '"$V"'/' -e 's/^appVersion: .*/appVersion: "'"$V"'"/' \
        -e 's#(ghcr\.io/owen282000/life-dashboard-[a-z]+):[0-9]+\.[0-9]+\.[0-9]+#\1:'"$V"'#' "$CHART" && rm "$CHART.bak"
    # Artifact Hub's changelog for this version: the entries of its changelog section, by kind.
    python3 - "$V" "$CHART" <<'PY'
import json, re, sys
v, chart = sys.argv[1:]
kinds = {"Added": "added", "Changed": "changed", "Deprecated": "deprecated", "Removed": "removed",
         "Fixed": "fixed", "Security": "security"}
section = re.search(rf"^## \[{re.escape(v)}\].*?\n(.*?)(?=^## \[|^\[[^\]]+\]: |\Z)",
                    open("CHANGELOG.md").read(), re.M | re.S).group(1)
changes, kind = [], None
for line in section.splitlines():
    if line.startswith("### "):
        kind = kinds.get(line[4:].strip())
    elif kind and line.startswith("- "):
        changes.append([kind, line[2:].strip()])
    elif kind and changes and line.startswith("  ") and line.strip():
        changes[-1][1] += " " + line.strip()
plain = [(k, json.dumps(re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", d), ensure_ascii=False)) for k, d in changes]
block = "".join(f"    - kind: {k}\n      description: {d}\n" for k, d in plain)
security = "true" if any(k == "security" for k, _ in changes) else "false"
s = open(chart).read()
s = re.sub(r"^  artifacthub\.io/containsSecurityUpdates: .*\n", "", s, flags=re.M)
s = re.sub(r"^  artifacthub\.io/changes: \|\n(?:    .*\n)*", "", s, flags=re.M)
new = f'  artifacthub.io/containsSecurityUpdates: "{security}"\n'
if block:
    new += "  artifacthub.io/changes: |\n" + block
s = s.replace("\nannotations:\n", "\nannotations:\n" + new, 1)
open(chart, "w").write(s)
PY
fi

# The MCP Registry entry: the server's version and the image it points at.
sed -E -i.bak -e 's/^  "version": "[^"]*"/  "version": "'"$V"'"/' \
    -e 's#"ghcr\.io/owen282000/life-dashboard-mcp:[^"]*"#"ghcr.io/owen282000/life-dashboard-mcp:'"$V"'"#' server.json && rm server.json.bak
# The version the MCP server reports to clients.
sed -E -i.bak 's/^VERSION = "[^"]*"/VERSION = "'"$V"'"/' mcp/server.py && rm mcp/server.py.bak
# The version the receiver names in its answer to a signed payload.
sed -E -i.bak 's/^VERSION = "[^"]*"/VERSION = "'"$V"'"/' receiver/receiver.py && rm receiver/receiver.py.bak

echo "Prepared $V (previous ${PREV:-none}). Check the changelog section, then commit and: git tag $V && git push origin $V"
