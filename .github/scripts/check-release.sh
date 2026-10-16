#!/usr/bin/env bash
#
# Checks that the files of a release agree with its version, the way scripts/prepare-release.sh
# leaves them: the version is strict semver and higher than every other version tag, the Helm
# chart's version and appVersion are the version, docker-compose.yml defaults all five image
# lines to it, and CHANGELOG.md has a section for it. The release workflow runs this on the
# tag, and the test workflow on a throwaway checkout after prepare-release.sh.
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

n=$(grep -cF ":\${LIFEDASH_VERSION:-$V}" docker-compose.yml || true)
if [ "$n" != 5 ]; then
    problem "docker-compose.yml should default all five image lines to $V (found $n)"
    grep -n "image:" docker-compose.yml
fi

notes=$(.github/scripts/release-notes.sh "$V")
if [ -z "$notes" ]; then
    problem "CHANGELOG.md has no section '## [$V]', or it is empty"
fi

exit "$fail"
