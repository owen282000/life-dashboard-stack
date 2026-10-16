# life-dashboard

The Life Dashboard Stack on Kubernetes: the receiver for the Life Dashboard Companion apps on Android and iOS, Postgres, Grafana with six dashboards of your health and screen time data, and an optional read-only MCP server and JSON API. Your data stays in your own cluster.

## Prerequisites

- Kubernetes 1.29 or later
- Helm 3 or Helm 4
- For the default Postgres mode: the [CloudNativePG](https://cloudnative-pg.io) operator, and its Barman Cloud plugin for backups

## Install

```bash
helm install lifedash oci://ghcr.io/owen282000/charts/life-dashboard \
  -n life-dashboard --create-namespace --version <version> \
  --set receiver.publicUrl=https://lifedash.example.com \
  --set timezone=Europe/Amsterdam \
  --wait --timeout 10m
helm test lifedash -n life-dashboard --logs
```

From a clone of the [repository](https://github.com/owen282000/life-dashboard-stack), use `charts/life-dashboard` instead of the `oci://` address. The chart's version is the version of the stack, and the images default to it.

## Postgres

| `postgres.mode` | What you get |
|---|---|
| `cnpg` (default) | A CloudNativePG `Cluster`: replicas and failover with `postgres.cnpg.instances`, backups to object storage and restores, `grafana_ro` and `dash_ro` as managed roles. |
| `bundled` | One Postgres pod in a StatefulSet with a volume. No replicas and no backups of its own. |
| `external` | Your own Postgres 18: the owner's URL from a Secret (`postgres.external.existingSecret`) and `postgres.external.host` for Grafana and the MCP server. |

## Main values

| Value | Default | What it does |
|---|---|---|
| `timezone` | `UTC` | Your time zone as an IANA name; days and nights in the dashboards follow it. |
| `receiver.publicUrl` | `""` | The address your phone reaches the receiver on, for the pairing code. |
| `receiver.api.enabled` | `false` | The read-only JSON API under `/api/`. |
| `mcp.enabled` | `false` | The read-only MCP server on port 8090. |
| `receiver.expose.webhook`, `.api`, `.pair` | `true`, `false`, `false` | The paths the Ingress and the HTTPRoute send to the receiver. |
| `receiver.ingress`, `receiver.httpRoute` | off | An Ingress or a Gateway API route for the receiver; the same for `grafana`. |
| `postgres.mode` | `cnpg` | See above. |
| `postgres.cnpg.instances` | `1` | Postgres instances; 2 or more gives failover. |
| `postgres.cnpg.backup.enabled` | `false` | Backups through the Barman Cloud plugin. |
| `secrets.existingSecret` | `""` | Your own Secret with the stack's secrets. Empty: generated once and kept on upgrades. |
| `networkPolicy.enabled` | `false` | NetworkPolicies for every pod of the release. |
| `demo.enabled` | `false` | Posts 60 days of sample data. Not for a real install. |

`helm show values oci://ghcr.io/owen282000/charts/life-dashboard` lists them all with a comment, and `values.schema.json` refuses a value with a typo.

## Pair a phone

The pairing code holds the webhook secret, so the chart keeps it out of the log and switches the `/pair` page off. Print it on your own terminal, with one label per phone:

```bash
kubectl -n life-dashboard exec deploy/lifedash-life-dashboard-receiver -- python pairing.py phone
```

Or set `receiver.pairing.page: true` while you pair, and open `/pair` through `kubectl port-forward`.

## Security

Every pod runs as a non-root user with a read-only root file system, no capabilities, `RuntimeDefault` seccomp and no service account token, so the chart installs under the `restricted` Pod Security level. Grafana and the MCP server read through read-only database roles, never as the owner. The Ingress and HTTPRoute send only `/webhook` to the receiver unless you open more.

## Upgrade and uninstall

`helm upgrade` with the same values file; the receiver migrates the database at its start, and generated secrets stay. `helm uninstall` keeps the database, Grafana's volume and the generated Secrets, so a reinstall under the same name finds the data.

## Documentation

- [Kubernetes guide](https://github.com/owen282000/life-dashboard-stack/blob/main/docs/kubernetes.md): every mode, secrets, pairing, routes, backups, restores, NetworkPolicies and GitOps tools.
- [What a version keeps stable](https://github.com/owen282000/life-dashboard-stack/blob/main/docs/compatibility.md), the chart's values included.
