#!/usr/bin/env bash
#
# Runs a pod that tries TCP connections and checks each against what it should get: "open"
# (connected) or "blocked" (timed out, the way a NetworkPolicy drops it). A refused connection
# is neither, so a probe of a port nothing listens on fails instead of passing as blocked.
# The pod uses the receiver image for its Python and meets the restricted Pod Security level.
#
# Usage: .github/scripts/kind-probe.sh <namespace> <labels, as k=v,k=v, or -> <want>:<host>:<port>...
#   e.g. kind-probe.sh outsider - blocked:10.244.1.5:5432 open:lifedash-life-dashboard-receiver.cnpg:8080
#
set -euo pipefail

ns="$1" labels="$2"
shift 2
image="ghcr.io/owen282000/life-dashboard-receiver:${TAG:?TAG is the tag of the images in kind}"
name="probe-$RANDOM"
label_json="{}"
if [ "$labels" != - ]; then
    label_json=$(jq -cn --arg l "$labels" '$l | split(",") | map(split("=") | {(.[0]): .[1]}) | add')
fi

script='
import socket, sys
bad = False
for target in sys.argv[1:]:
    want, host, port = target.split(":")
    try:
        socket.create_connection((host, int(port)), timeout=5).close()
        got = "open"
    except TimeoutError:
        got = "blocked"
    except OSError as e:
        got = type(e).__name__
    print(f"{host}:{port}: {got}, want {want}" + ("" if got == want else "  <-- wrong"))
    bad |= got != want
sys.exit(1 if bad else 0)
'

jq -n --arg name "$name" --arg ns "$ns" --arg image "$image" --arg script "$script" \
    --argjson labels "$label_json" --args '{
  apiVersion: "v1", kind: "Pod",
  metadata: {name: $name, namespace: $ns, labels: $labels},
  spec: {
    restartPolicy: "Never",
    automountServiceAccountToken: false,
    securityContext: {runAsNonRoot: true, runAsUser: 65534, seccompProfile: {type: "RuntimeDefault"}},
    containers: [{
      name: "probe", image: $image, imagePullPolicy: "IfNotPresent",
      command: (["python", "-c", $script] + $ARGS.positional),
      securityContext: {allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: {drop: ["ALL"]}}
    }]
  }
}' "$@" | kubectl apply -f - > /dev/null

phase=""
for _ in $(seq 1 60); do
    phase=$(kubectl -n "$ns" get pod "$name" -o jsonpath='{.status.phase}')
    case "$phase" in Succeeded | Failed) break ;; esac
    sleep 2
done
if [ "$labels" = - ]; then echo "From a pod in $ns:"; else echo "From a pod in $ns with $labels:"; fi
kubectl -n "$ns" logs "$name" | sed 's/^/  /'
kubectl -n "$ns" delete pod "$name" --wait=false > /dev/null
[ "$phase" = Succeeded ]
