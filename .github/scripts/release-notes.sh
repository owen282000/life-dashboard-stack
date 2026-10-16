#!/usr/bin/env bash
#
# Prints the notes of a GitHub release: the version's section of CHANGELOG.md and where to get
# that version, the release files, the images and the Helm chart. Prints nothing when the
# changelog has no section, or an empty one, for the version.
#
# Usage: .github/scripts/release-notes.sh 1.2.3 > notes.md
#
set -euo pipefail
cd "$(dirname "$0")/../.."

V="${1:?usage: release-notes.sh <version X.Y.Z>}"
REPO=https://github.com/owen282000/life-dashboard-stack

section=$(awk -v v="$V" '
    /^## \[/ { if (found) exit; if (index($0, "## [" v "]") == 1) { found = 1; next } }
    found && !/^\[.+\]: http/
' CHANGELOG.md)
grep -q '[^[:space:]]' <<<"$section" || exit 0

printf '%s\n' "$section" | sed -e '/./,$!d'
cat <<EOF

### Install $V

- **One line**, with Docker or Podman: \`curl -fsSL $REPO/releases/download/$V/install.sh | sh\` installs this version, writes \`.env\` with random secrets and shows the pairing code; see the [setup guide]($REPO/blob/$V/docs/install.md).
- **Docker Compose by hand:** \`docker-compose.yml\` and \`env.example\` below run this version.
- **Images**, for amd64 and arm64: \`ghcr.io/owen282000/life-dashboard-receiver:$V\`, \`life-dashboard-grafana:$V\`, \`life-dashboard-db:$V\` and \`life-dashboard-mcp:$V\`.
- **Helm chart** $V, which runs these images: \`helm install lifedash oci://ghcr.io/owen282000/charts/life-dashboard --version $V\`; see [docs/kubernetes.md]($REPO/blob/$V/docs/kubernetes.md).
EOF
