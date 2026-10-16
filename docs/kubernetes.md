# Kubernetes

The stack also comes as a Helm chart, `life-dashboard`, for a Kubernetes cluster of your own. It runs the same four images as Docker Compose: the receiver, Grafana with the six dashboards, the optional MCP server, and Postgres in one of three ways. Pick CloudNativePG if you can: it gives you replicas, failover and backups to object storage. The chart works with Helm 3 and Helm 4 on Kubernetes 1.29 or later.

## Contents

- [What the chart runs](#what-the-chart-runs)
- [Install CloudNativePG](#install-cloudnativepg)
- [Install the chart](#install-the-chart)
- [Postgres: three modes](#postgres-three-modes)
- [Secrets](#secrets)
- [Pair a phone](#pair-a-phone)
- [Check the install](#check-the-install)
- [Reach it from outside](#reach-it-from-outside)
- [The API and the MCP server](#the-api-and-the-mcp-server)
- [Backups](#backups)
- [Restore](#restore)
- [Upgrades](#upgrades)
- [Uninstall, and what happens to your data](#uninstall-and-what-happens-to-your-data)
- [Argo CD and Flux](#argo-cd-and-flux)
- [NetworkPolicies](#networkpolicies)
- [Security](#security)
- [Try it with the demo data](#try-it-with-the-demo-data)
- [Tested on](#tested-on)
- [Values that stay stable](#values-that-stay-stable)

## What the chart runs

| Part | Kind | Port | Notes |
|---|---|---|---|
| Receiver | Deployment and Service | 8080 | Runs the migrations at every start; several replicas share them safely. Probes on `/health`. |
| Grafana | Deployment (one pod) and Service | 3000 | The six dashboards come with the image. The data source points at this release's Postgres. A volume for `/var/lib/grafana` is on by default. |
| MCP server | Deployment and Service, with `mcp.enabled` | 8090 | Off by default, like the `mcp` profile. |
| Postgres | CloudNativePG `Cluster`, a StatefulSet, or none | 5432 | See [the three modes](#postgres-three-modes). |

Every pod runs as a non-root user with a read-only root file system, all capabilities dropped and the `RuntimeDefault` seccomp profile, so the chart installs in a namespace with the `restricted` Pod Security level. The chart's own pods (the receiver, Grafana, the MCP server, the bundled Postgres and the demo and test pods) get no service account token: none of them talks to the Kubernetes API. CloudNativePG's instance pods do, with a service account the operator creates for them.

## Install CloudNativePG

[CloudNativePG](https://cloudnative-pg.io) is an operator that runs Postgres in Kubernetes. Install it once per cluster, from its release manifest (this is the version the chart was tested with):

```bash
kubectl apply --server-side -f \
  https://github.com/cloudnative-pg/cloudnative-pg/releases/download/v1.30.1/cnpg-1.30.1.yaml
kubectl -n cnpg-system rollout status deployment/cnpg-controller-manager
```

Its own [Helm chart](https://github.com/cloudnative-pg/charts) works too. For [backups](#backups) you also need the Barman Cloud plugin, which needs cert-manager:

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.21.2/cert-manager.yaml
kubectl -n cert-manager rollout status deployment/cert-manager-webhook
kubectl apply -f https://github.com/cloudnative-pg/plugin-barman-cloud/releases/download/v0.15.1/manifest.yaml
kubectl -n cnpg-system rollout status deployment/barman-cloud
```

Without CloudNativePG, use the [bundled](#bundled) Postgres instead.

## Install the chart

From release 1.3.0 on, every release of the stack is on GHCR as an OCI chart:

```bash
kubectl create namespace life-dashboard
helm install lifedash oci://ghcr.io/owen282000/charts/life-dashboard -n life-dashboard \
  --version 1.3.0 -f my-values.yaml --wait --timeout 10m
```

Or from a clone of the repository:

```bash
helm install lifedash charts/life-dashboard -n life-dashboard -f my-values.yaml --wait --timeout 10m
```

A small `my-values.yaml` to start from:

```yaml
timezone: Europe/Amsterdam
receiver:
  publicUrl: https://lifedash.example.com
```

Put your settings in a values file rather than in `--set`, so an upgrade uses the same ones. `helm show values` prints every value with a comment, and `values.schema.json` refuses a value with a typo before anything is installed. The chart has the version of the stack: chart `1.3.0` is the stack's release `1.3.0`, its `appVersion` is the same, and the four images default to it. `image.tag` pins all four images to another version, and `image.tag` or `digest` under a component pins one.

When it's up, check it:

```bash
helm test lifedash -n life-dashboard --logs
```

The test pods check that the receiver answers and refuses a wrong signature, that Grafana lists the six dashboards and its data source connects as `grafana_ro`, and, when they're on, that the API and the MCP server answer with their tokens and refuse without them. If you changed the Grafana admin password in Grafana itself, the Grafana test fails on its login; that's expected.

Open Grafana through a port-forward (the password is in the release's Secret, see [Secrets](#secrets)):

```bash
kubectl -n life-dashboard get secret lifedash-life-dashboard -o jsonpath='{.data.GRAFANA_PASSWORD}' | base64 -d; echo
kubectl -n life-dashboard port-forward svc/lifedash-life-dashboard-grafana 3000:3000
```

## Postgres: three modes

`postgres.mode` picks one. Changing it later doesn't move your data: pick before the first phone sends.

| Mode | Postgres | Replicas and failover | Backups | Needs |
|---|---|---|---|---|
| `cnpg` (default) | A CloudNativePG `Cluster` on CloudNativePG's Postgres 18 image | With `postgres.cnpg.instances` of 2 or more | To object storage, with point-in-time restore | The CloudNativePG operator, and the Barman Cloud plugin for backups |
| `bundled` | One pod of the stack's db image in a StatefulSet | None | Dumps of your own | Nothing |
| `external` | Your own Postgres 18 | Your provider's | Your provider's | A Secret with the owner's URL |

### cnpg

The default. The chart creates a CloudNativePG `Cluster` named `<release>-life-dashboard-postgres`, and the operator does the rest: the database `lifedash` with its owner `lifedash`, a Secret `<cluster>-app` with the owner's connection URL, which the receiver reads, and the Services `<cluster>-rw` (the primary), `-ro` and `-r`.

```yaml
postgres:
  mode: cnpg
  cnpg:
    instances: 3          # one primary, two replicas; 1 is the default
    storage:
      size: 10Gi          # 5Gi is the default
      storageClass: ""    # empty: the cluster's default
    walStorage:
      enabled: false      # WAL on a volume of its own
    parameters: {}        # postgresql.conf, such as shared_buffers: 256MB
    synchronous: {}       # see "Synchronous replication" below
    affinity: {}          # CloudNativePG's own affinity section
```

It runs CloudNativePG's own Postgres 18 image, `ghcr.io/cloudnative-pg/postgresql:18.6-standard-trixie`, not the stack's db image. The db image only adds a guard for an old Docker volume of Postgres 16, which a new cluster never has, and CloudNativePG builds its images for its instance manager, with the time zone data the receiver checks `TZ` against. `postgres.cnpg.imageName` picks another; keep it at major 18.

**Failover.** With more than one instance, the operator promotes a replica when the primary goes away. How long that takes depends on the cluster: on kind a new primary took over in about 2 seconds, on the real test cluster about 26 seconds after the primary's pod was deleted, and a planned switchover (a node drain, a rolling update) took about 10 seconds there, with writes held up for about 20 seconds during a drain. The receiver connects per request to `-rw`, which follows the new primary, so it needs no restart. Each payload connects with a 5 second timeout, so a payload that arrives in that window gets a 500 quickly, and the app sends it again. No payload was lost in the tests.

**Synchronous replication.** By default, as in CloudNativePG, replicas follow the primary asynchronously: a failover can lose the last transactions the primary committed but no replica had received yet, so a payload the receiver had already answered with 200 can be gone. `postgres.cnpg.synchronous` goes to the `Cluster`'s `postgresql.synchronous` as it is:

```yaml
postgres:
  cnpg:
    instances: 3
    synchronous:
      method: any
      number: 1
      dataDurability: preferred
```

Then a commit waits until one replica has it. With `dataDurability: preferred`, writes go on without that wait while no replica is available; with `required`, CloudNativePG's default for this section, they stop until one is back. Each payload takes a little longer either way. See CloudNativePG's documentation on synchronous replication for the details.

**The read-only roles.** CloudNativePG's app user owns the database but is no superuser and has no `CREATEROLE`, so the receiver can't create `grafana_ro` and `dash_ro` there as it does with Docker Compose. The chart lets CloudNativePG do it:

- `managed.roles` in the `Cluster` creates both with `LOGIN` and the password from a Secret each (`<release>-life-dashboard-grafana-ro` and `-dash-ro`, type `kubernetes.io/basic-auth`). The chart generates those, or `postgres.cnpg.roles.grafanaRoSecret` and `dashRoSecret` name your own. A changed password there reaches Postgres within seconds.
- The `Cluster`'s first start (`postInitApplicationSQL`) gives them what managed roles can't: the statement timeouts of 30 and 15 seconds and read-only sessions.
- The receiver sees that it may not alter them, says so in its log (`grafana_ro is managed outside the receiver`), and grants them their reads as it does everywhere: `grafana_ro` the `records` and `buckets` tables and the `dash` views, `dash_ro` the views only. A role that lacks its settings gets a warning in the log, with the `ALTER ROLE` that adds them. It sends no role statement it may not run, so no refused statement with a password ends up in the Postgres log.

Grafana and the MCP server connect to `-rw` with TLS (`sslmode=require`), which CloudNativePG serves with its own certificate authority.

### bundled

For a cluster without CloudNativePG: one Postgres pod in a StatefulSet, with the stack's db image and a volume, much like Docker Compose.

```yaml
postgres:
  mode: bundled
  bundled:
    storage:
      size: 5Gi
      storageClass: ""
```

It's one instance with no replica, so a node that goes down takes the database with it until the pod runs again, and a lost volume is lost data. There are no backups of its own: take dumps, see [Backups](#backups). The owner's password is `POSTGRES_PASSWORD` in the release's Secret; Postgres sets it once, when it creates the database, so don't change it afterwards. The receiver connects as a superuser here, so it creates `grafana_ro` and `dash_ro` and sets their passwords, as with Docker Compose.

At every start the database logs `chmod: /var/run/postgresql: Operation not permitted`. That's harmless: the image's entrypoint tries to set the mode of that folder, which is an `emptyDir` it doesn't own under the restricted settings, and Postgres starts normally.

### external

Your own Postgres 18, managed or not. Put the owner's connection URL in a Secret and tell Grafana and the MCP server where the database is:

```bash
kubectl -n life-dashboard create secret generic lifedash-database \
  --from-literal=DATABASE_URL='postgresql://lifedash:<password>@db.example.com:5432/lifedash?sslmode=require'
```

```yaml
postgres:
  mode: external
  external:
    existingSecret: lifedash-database
    urlKey: DATABASE_URL
    host: db.example.com      # for Grafana and the MCP server
    port: 5432
    database: lifedash
    sslmode: require
```

The user in the URL must own the database. What happens to `grafana_ro` and `dash_ro` depends on what else it may do:

- **A superuser** creates them when they're missing and sets their passwords and settings at every start, as with Docker Compose. A password goes to Postgres as a SCRAM-SHA-256 verifier, never as plain text.
- **A user with `CREATEROLE` but no superuser**, as on most managed Postgres services, does the same for a role it created itself. A role that someone else created stays as it is: from Postgres 16 on, such a user may only alter a role it holds `ADMIN OPTION` on.
- **A user with neither** may not create them. Create them first, as a user that may:

```sql
CREATE ROLE grafana_ro LOGIN PASSWORD '<GRAFANA_DB_PASSWORD>';
ALTER ROLE grafana_ro SET statement_timeout = '30s';
ALTER ROLE grafana_ro SET default_transaction_read_only = on;
CREATE ROLE dash_ro LOGIN PASSWORD '<DASH_DB_PASSWORD>';
ALTER ROLE dash_ro SET statement_timeout = '15s';
ALTER ROLE dash_ro SET default_transaction_read_only = on;
```

The receiver then grants them their reads. Without the roles, it stops at its start with a message that says how to create them. A role the receiver may not alter keeps its password, so give Grafana and the MCP server that same password: set `secrets.grafanaDbPassword` and `dashDbPassword`, or the keys `GRAFANA_DB_PASSWORD` and `DASH_DB_PASSWORD` in your own Secret, instead of letting the chart generate them.

**Several releases on one Postgres server.** `grafana_ro` and `dash_ro` belong to the whole server, not to one database. Two releases on the same server, each with a database of its own, share them: a receiver that may set their passwords sets its own, and the other release's Grafana can no longer log in. Give every release the same `GRAFANA_DB_PASSWORD` and `DASH_DB_PASSWORD`, or create the roles yourself and connect each receiver as an owner that may not alter them. Either way the roles can read every database that granted them their reads; for data that has to stay apart, use a server per release.

If the receiver can't log in, it says why and stops at once, with the server's own message (`the database refused the connection: password authentication failed for user ...`, a role or database that doesn't exist, a missing `pg_hba.conf` entry). A server it can't reach yet is retried for a few minutes, with each new reason in its log once.

## Secrets

The stack needs a few secrets: `WEBHOOK_SECRET` (the HMAC secret of the apps), `GRAFANA_PASSWORD` (Grafana's `admin`), `GRAFANA_DB_PASSWORD` and `DASH_DB_PASSWORD` (the read-only roles), `API_TOKEN`, `MCP_TOKEN`, and in bundled mode `POSTGRES_PASSWORD`.

By default the chart generates each one once, 48 random letters and digits, in a Secret named after the release. On every upgrade it reads them back from the cluster (Helm's `lookup`), so they stay the same. A value under `secrets` in your values file wins over a generated one, for instance to rotate the API token:

```yaml
secrets:
  apiToken: "<openssl rand -hex 32>"
```

Read one back with:

```bash
kubectl -n life-dashboard get secret lifedash-life-dashboard -o jsonpath='{.data.WEBHOOK_SECRET}' | base64 -d; echo
```

Or bring your own Secret, made by hand, Sealed Secrets, External Secrets or SOPS:

```yaml
secrets:
  existingSecret: lifedash-secrets
```

It needs the keys `WEBHOOK_SECRET`, `GRAFANA_PASSWORD`, `API_TOKEN` and `MCP_TOKEN`; `GRAFANA_DB_PASSWORD` and `DASH_DB_PASSWORD` unless the mode is `cnpg` (there the role passwords live in the two basic-auth Secrets above); `POSTGRES_PASSWORD` in bundled mode; and optionally `PAIRING_TOKEN`. API, MCP and pairing tokens need at least 16 characters.

Each pod gets only the secrets it uses: the receiver the webhook secret, `DASH_DB_PASSWORD` (the API reads as `dash_ro`), `GRAFANA_DB_PASSWORD` only where it sets that role's password (not on CloudNativePG), `API_TOKEN` only with the API on, and `PAIRING_TOKEN` when there is one; Grafana its admin password and `grafana_ro`'s; the MCP server its token and `dash_ro`'s; the bundled Postgres its own.

The pods carry a checksum of the generated values and the names of the Secrets they read. It changes only when a value or a name really changes, so an upgrade or a rollback that changes nothing else restarts nothing, and a new value restarts the pods that read it. A value you edit in the generated Secret in the cluster is read back, and restarts them at the next upgrade. With `existingSecret` or your own role Secrets, the checksum covers their names, not what's in them: after you change a value there, restart the pods yourself, such as `kubectl -n life-dashboard rollout restart deployment -l app.kubernetes.io/instance=lifedash`.

`lookup` only works when Helm talks to the cluster. `helm template`, `helm install --dry-run` and Argo CD render without it, so they'd generate new values on every render: use `existingSecret` there, see [Argo CD and Flux](#argo-cd-and-flux).

## Pair a phone

Set `receiver.publicUrl` to the address your phone reaches the receiver on, such as `https://lifedash.example.com`; the pairing code holds it plus `/webhook/<label>`. The code also holds the webhook secret, so on Kubernetes, where every pod has a private address and logs are often collected elsewhere, the chart switches both the pairing block in the log (`receiver.pairing.log`) and the `/pair` page (`receiver.pairing.page`) off. Print the code on your own terminal instead:

```bash
kubectl -n life-dashboard exec deploy/lifedash-life-dashboard-receiver -- python pairing.py phone
```

`phone` is the label: give each phone its own, 1 to 32 lowercase letters, digits, `-` and `_`. It prints the same block as the log would, with the QR code, the URL and the secret to type by hand. Scan it with the app, as in [the setup guide](install.md#pair-the-app). Without `receiver.publicUrl` it says what to set.

If you'd rather scan from a browser, switch the page on while you pair, with `receiver.pairing.page: true`, and open it through a port-forward from your own computer:

```bash
kubectl -n life-dashboard port-forward svc/lifedash-life-dashboard-receiver 8080:8080
```

Then open `http://localhost:8080/pair`. A port-forward arrives at the receiver from its own loopback address, so the page answers. Switch it off again afterwards: `/pair` answers every request from a private address that carries no proxy header, with or without a pairing token, and inside a cluster that's every pod, and also a request that a `LoadBalancer` or `NodePort` Service passes on with `externalTrafficPolicy: Cluster`, from wherever it came.

Through an Ingress or a Gateway, which add `X-Forwarded-For`, `/pair` answers only with `?token=` and `secrets.pairingToken`. `receiver.expose.pair` routes it there, and the chart refuses to render it without `receiver.pairing.page: true` and a pairing token (`secrets.pairingToken`, or `PAIRING_TOKEN` in your own Secret). Prefer the port-forward: the receiver hides the token in its own log, but the ingress controller's access log, any proxy in between and your browser history keep the URL with `?token=` in it.

## Check the install

`helm test` checks that the parts answer. To see what's wrong when something is, run the doctor in the receiver:

```bash
kubectl -n life-dashboard exec deploy/lifedash-life-dashboard-receiver -- python doctor.py
```

It prints a `PASS`, `WARN` or `FAIL` line per check, with the fix: the webhook secret, `receiver.publicUrl`, the time zone, the database and its migrations, `grafana_ro` and `dash_ro`, the API when it's on, and when each phone last sent data. On CloudNativePG, where the operator owns the roles' passwords, it checks that the roles exist and may read. It never prints a secret. [Troubleshooting](operations.md#troubleshooting) lists every check; the pods' own state is `kubectl -n life-dashboard get pods`.

## Reach it from outside

The phones need `/webhook` on the receiver, over HTTPS for an iPhone. The chart has an Ingress and a Gateway API `HTTPRoute` for the receiver and for Grafana, both off by default. For the receiver they route only `/webhook`; `/api` and `/pair` are opt-in:

```yaml
receiver:
  publicUrl: https://lifedash.example.com
  expose:
    webhook: true
    api: false     # the read-only API, behind its token
    pair: false    # needs pairing.page and a pairing token; better: kubectl exec or a port-forward
  ingress:
    enabled: true
    className: nginx
    annotations:
      nginx.ingress.kubernetes.io/proxy-body-size: 32m
      cert-manager.io/cluster-issuer: letsencrypt
    hosts: [lifedash.example.com]
    tls:
      - secretName: lifedash-tls
        hosts: [lifedash.example.com]
```

Or with the Gateway API, on a Gateway that terminates TLS:

```yaml
receiver:
  httpRoute:
    enabled: true
    parentRefs:
      - name: my-gateway
        namespace: gateway-system
        sectionName: https
    hostnames: [lifedash.example.com]
```

- Allow request bodies of 32 MB: a backfill sends large payloads, and the receiver turns away anything larger with a 413. ingress-nginx needs the annotation above; Traefik has no limit by default.
- `grafana.ingress` and `grafana.httpRoute` route all of Grafana. Keep Grafana on your own network or behind a VPN or an authenticating proxy, as [Exposing it safely](operations.md#exposing-it-safely) says, and set `grafana.rootUrl` to its address.
- `receiver.service.type: LoadBalancer` works for a LAN address from MetalLB or similar, but it exposes every path, `/pair` too while the page is on; see [Pair a phone](#pair-a-phone).

## The API and the MCP server

```yaml
receiver:
  api:
    enabled: true   # the read-only API under /api/ (docs/api.md)
mcp:
  enabled: true     # the MCP server on port 8090 (docs/mcp.md)
```

Their tokens are `API_TOKEN` and `MCP_TOKEN` in the Secret. Both read as `dash_ro`. Reach them with a port-forward, or route `/api` with `receiver.expose.api`. The MCP server has no route of its own: for a client outside the cluster, use a port-forward, a VPN, or an Ingress of your own with TLS.

```bash
kubectl -n life-dashboard port-forward svc/lifedash-life-dashboard-mcp 8090:8090
```

## Backups

### cnpg

CloudNativePG backs up to object storage (S3 or compatible, Azure Blob, Google Cloud Storage) through the Barman Cloud plugin: a base backup on a schedule, and the WAL in between, so you can restore to any moment. [Install the plugin](#install-cloudnativepg) first, then:

```yaml
postgres:
  cnpg:
    backup:
      enabled: true
      serverName: ""            # the folder in the bucket; empty: the Cluster's name
      objectStore:
        retentionPolicy: 30d
        configuration:
          destinationPath: s3://my-bucket/lifedash/
          endpointURL: https://s3.example.com      # leave out for AWS S3
          s3Credentials:
            accessKeyId:
              name: backup-credentials
              key: ACCESS_KEY_ID
            secretAccessKey:
              name: backup-credentials
              key: ACCESS_SECRET_KEY
          wal:
            compression: gzip
          data:
            compression: gzip
      scheduled:
        enabled: true
        schedule: "0 0 3 * * *"   # six fields, seconds first: daily at 03:00
        immediate: true           # one right away as well
```

`configuration` is the `ObjectStore`'s configuration as the plugin documents it, so Azure and Google Cloud Storage credentials go there too. Create the credentials Secret yourself. `objectStore.create: false` with `objectStore.name` uses an `ObjectStore` you already have. Check:

```bash
kubectl -n life-dashboard get backups,scheduledbackups
kubectl -n life-dashboard get cluster lifedash-life-dashboard-postgres \
  -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.message}{"\n"}{end}'
```

`ContinuousArchiving=True` means the WAL is going out. When you switch backups on for a Cluster that's already running, the operator restarts its pods to add the plugin, and the `immediate` backup can fail while that happens; the next scheduled one works, or start one by hand with `kubectl cnpg backup lifedash-life-dashboard-postgres -m plugin --plugin-name barman-cloud.cloudnative-pg.io`.

### bundled and external

A dump works in every mode; in bundled mode:

```bash
kubectl -n life-dashboard exec lifedash-life-dashboard-postgres-0 -- \
  pg_dump -U lifedash -Fc lifedash > lifedash-$(date +%F).dump
```

Restore it with `pg_restore -U lifedash -d lifedash --clean --if-exists` through `kubectl exec -i`, and restart the receiver afterwards (`kubectl rollout restart deployment/lifedash-life-dashboard-receiver`) so it rebuilds the views and the grants. For an external database, use your provider's backups.

## Restore

A CloudNativePG restore is a new `Cluster` that starts from the backup instead of an empty database. Install a new release, or the same one after an uninstall and with the old `Cluster` deleted, with:

```yaml
postgres:
  cnpg:
    recovery:
      enabled: true
      objectStoreName: lifedash-life-dashboard-postgres-backup   # the ObjectStore with the backup
      serverName: lifedash-life-dashboard-postgres               # the old Cluster's folder
      targetTime: ""          # such as "2026-10-16 08:00:00+00"; empty: everything there is
    backup:
      enabled: true
      serverName: lifedash-restored   # a new folder: the plugin won't write over the old one
      objectStore:
        configuration: {}   # the same bucket and credentials as before
```

The roles come back from the backup, with their settings; CloudNativePG gives them the passwords of the new release's Secrets. The receiver runs the migrations a newer version needs, as on any start.

## Upgrades

```bash
helm upgrade lifedash oci://ghcr.io/owen282000/charts/life-dashboard -n life-dashboard \
  --version <new version> -f my-values.yaml --wait --timeout 10m
```

From a clone, `helm upgrade lifedash charts/life-dashboard ...` with the same options. A new chart version is a new version of the stack, with its images; the receiver runs the database migrations at its start. The generated secrets, the data and Grafana's volume stay. Within a major version, your values file keeps working, see [Values that stay stable](#values-that-stay-stable). The [changelog](../CHANGELOG.md) says what's new.

A rollout doesn't cut off a payload halfway: the receiver and the MCP server wait 5 seconds before they get `SIGTERM` (a `preStop` hook), so their Service stops sending them new requests first, and on `SIGTERM` the receiver stops taking connections and gives the requests in flight up to 10 seconds to finish. With `receiver.replicas: 2` and `receiver.podDisruptionBudget.enabled: true`, a node drain keeps one receiver running. With one replica, a PodDisruptionBudget of `minAvailable: 1` blocks the drain until you scale up.

`helm rollback lifedash <revision>` goes back to an earlier chart version and its images, with the same secrets. The database stays as the newer version left it: migrations only move forward.

To upgrade CloudNativePG, apply the manifest of its new release; it updates the instance manager in the running pods. Minor versions of Postgres 18 come with a new `postgres.cnpg.imageName`, rolled out one instance at a time with a switchover.

## Uninstall, and what happens to your data

```bash
helm uninstall lifedash -n life-dashboard
```

By default, an uninstall never deletes data:

| What | On `helm uninstall` |
|---|---|
| Receiver, Grafana and MCP pods, Services, routes, policies | Deleted |
| CloudNativePG `Cluster` (with `postgres.cnpg.keepOnUninstall: true`), its volumes, `ObjectStore` and `ScheduledBackup` | Kept, and Postgres keeps running |
| Bundled Postgres volume (`data-<release>-life-dashboard-postgres-0`) | Kept (a StatefulSet's volumes always are) |
| Grafana's volume (`grafana.persistence.keepOnUninstall: true`) | Kept |
| The generated Secrets (`secrets.keepOnUninstall: true`) | Kept, so a reinstall finds passwords that match the data |
| The demo Job and the `helm test` pods | Left behind; delete them with `kubectl delete jobs,pods -l app.kubernetes.io/instance=lifedash` |
| Backups in object storage | Never touched by Kubernetes |

`helm install` with the same release name in the same namespace takes the kept parts over again, with the data. To remove everything, data included:

```bash
helm uninstall lifedash -n life-dashboard
kubectl -n life-dashboard delete cluster lifedash-life-dashboard-postgres   # deletes its volumes
kubectl -n life-dashboard delete objectstore,scheduledbackup -l app.kubernetes.io/instance=lifedash
kubectl -n life-dashboard delete pvc,secret,job,pod -l app.kubernetes.io/instance=lifedash
kubectl -n life-dashboard delete pvc data-lifedash-life-dashboard-postgres-0   # bundled mode
```

Depending on the StorageClass's reclaim policy, the volumes themselves may stay as `Released` PersistentVolumes; `kubectl get pv` shows them.

## Argo CD and Flux

**Argo CD** renders the chart with `helm template`, so `lookup` returns nothing and every sync would generate new secrets: Grafana and the read-only roles would get new passwords, and in bundled mode the receiver would lose its database. With Argo CD:

- Use `secrets.existingSecret`, and in cnpg mode also `postgres.cnpg.roles.grafanaRoSecret` and `dashRoSecret`, filled by Sealed Secrets, External Secrets or SOPS. Then the chart generates nothing.
- Argo CD runs the demo Job as a `PostSync` hook, and doesn't run the `helm test` pods; run `helm test` from your computer, or the same checks by hand.
- `helm.sh/resource-policy: keep` is Helm's. To keep the `Cluster` when Argo CD prunes, add `postgres.cnpg.annotations: {argocd.argoproj.io/sync-options: Delete=false}`.

**Flux** installs with Helm itself, so `lookup` works and generated secrets stay. Set `spec.test.enable: true` on the `HelmRelease` to run the tests after each upgrade, and give it `timeout: 10m` for the first install, while CloudNativePG creates the database. An existing Secret is still the better choice when the cluster's state lives in Git.

Both can pull the chart from GHCR (`oci://ghcr.io/owen282000/charts`).

## NetworkPolicies

Off by default, because they need a network plugin that enforces them (most do: Calico, Cilium, and kind's own). With `networkPolicy.enabled: true`:

- **Into the receiver, Grafana and the MCP server**: port 8080, 3000 and 8090 from anywhere the cluster's network lets through, or only from the peers in `networkPolicy.receiver.from`, `.grafana.from` and `.mcp.from`, such as your ingress controller's namespace. The demo and the test pods of the release are always allowed.
- **Out of those three**: DNS and the database, nothing else. DNS goes only to the peers in `networkPolicy.dns.to`, by default the cluster DNS (pods labelled `k8s-app: kube-dns` in `kube-system`); an empty list allows port 53 anywhere, for a node-local DNS cache or a DNS that's labelled differently. The database is the CloudNativePG instances of their own `Cluster`, the bundled Postgres, or `postgres.external.port` anywhere (narrowed with `networkPolicy.externalDatabase.to`). Grafana gets more with `networkPolicy.grafanaEgress`, for instance SMTP for alerts.
- **Into the bundled Postgres**: 5432 from this release's receiver, Grafana and MCP server and from the peers in `networkPolicy.postgres.from`, plus whole rules of your own in `networkPolicy.postgres.extraIngress`. Nothing goes out.
- **Into the CloudNativePG instances**: 5432 from the same three and `networkPolicy.postgres.from`; 5432 and 8000 from the other instances of the `Cluster`; the instance manager's 8000 from the operator, which `networkPolicy.postgres.cnpgOperator` finds (by default pods labelled `app.kubernetes.io/name: cloudnative-pg` in `cnpg-system`; change it if the operator runs elsewhere); and the metrics port 9187 only through `networkPolicy.postgres.extraIngress`. Their way out stays open: they need the Kubernetes API, each other and the object store.

```yaml
networkPolicy:
  enabled: true
  receiver:
    from:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: ingress-nginx
  postgres:
    from:                         # a backup tool in another namespace
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: tools
```

Two things to know about how they work:

- **They only add what's allowed.** A NetworkPolicy can't take away what another policy allows. If your cluster already has a policy that lets every pod reach every other one, such as a cluster-wide Cilium policy like `allow-intra-cluster-traffic`, then any pod in the cluster can still reach the receiver and the database, whatever the chart's policies say. Check the cluster-wide policies of your network plugin (`kubectl get ciliumclusterwidenetworkpolicies` on Cilium) before you count on the chart's.
- **Cilium's own Ingress and Gateway** don't send traffic from a pod in a namespace: it comes from Cilium's `reserved:ingress` identity, so a `namespaceSelector` in `networkPolicy.receiver.from` never matches it and the phones get no answer. Leave `from` empty there, or allow that identity with a `CiliumNetworkPolicy` of your own (`fromEntities: [ingress]`). An ingress controller that runs as pods, such as ingress-nginx or Traefik, does match a `namespaceSelector`.

## Security

- The pods meet the `restricted` Pod Security Standard: non-root users (65534 for the receiver, the MCP server and the test pods, 472 for Grafana, 70 for the bundled Postgres), a read-only root file system with an `emptyDir` for `/tmp`, no privilege escalation, all capabilities dropped, `RuntimeDefault` seccomp. `*.podSecurityContext` and `*.securityContext` change them.
- Grafana's image puts its user in group 0 as well. From Kubernetes 1.33 on, the chart sets `supplementalGroupsPolicy: Strict` on Grafana's pod, so it runs with the groups the chart sets and nothing the image adds. A container runtime that doesn't support that yet refuses the pod; set `grafana.podSecurityContext.supplementalGroupsPolicy: Merge` there.
- No service account token is mounted, and the service account has no role.
- Requests and memory limits are set for every pod, with no CPU limits, so a backfill isn't throttled.
- Grafana's data source reads as `grafana_ro`, and the API and the MCP server as `dash_ro`, never as the owner. Each pod gets only the secrets it uses, see [Secrets](#secrets). The rest of [Exposing it safely](operations.md#exposing-it-safely) holds as well.
- The pairing block and the `/pair` page, which show the webhook secret, are off; see [Pair a phone](#pair-a-phone).
- The secrets reach the pods as environment variables. Anyone who may `kubectl exec` into a pod in the namespace can read them, so keep that right to yourself. Grafana can read a setting from a file instead (the `__FILE` variants of its settings); the receiver and the MCP server can't yet.

## Try it with the demo data

```bash
helm install demo charts/life-dashboard -n lifedash-demo --create-namespace \
  --set demo.enabled=true --set receiver.api.enabled=true --set mcp.enabled=true \
  --wait --timeout 10m
helm test demo -n lifedash-demo --logs
```

The demo is a Job that posts 60 days of made-up data for `demo-android` and `demo-iphone` after every install and upgrade, like the `demo` profile of Docker Compose. Don't switch it on in your real install. When you're done, uninstall and delete the namespace, which removes the kept parts with it:

```bash
helm uninstall demo -n lifedash-demo
kubectl delete namespace lifedash-demo
```

## Tested on

- **kind**, in CI on every change: CloudNativePG 1.30.1, the chart installed with CloudNativePG and with the bundled Postgres and NetworkPolicies, under the `restricted` Pod Security level, `helm test` after each install and after an upgrade. Before the release, with every NetworkPolicy on: a failover in about 2 seconds in three runs, and 919 payloads through two rollouts without a failure.
- **A real cluster** before the 1.3.0 release: RKE2 with Kubernetes 1.36, Cilium enforcing NetworkPolicies, the `restricted` Pod Security level enforced in every namespace by default, and Longhorn for the volumes. All three Postgres modes, the API, the MCP server and the NetworkPolicies. With a client sending payloads all along: a failover had a new primary in about 26 seconds, a switchover took about 10 seconds, a node drain held up writes for about 20 seconds, and of 4,639 payloads sent across a failover, a node drain, an upgrade, a rollback, an uninstall and a reinstall, none was lost.

## Values that stay stable

Within a major version, the values in [docs/compatibility.md](compatibility.md#the-helm-chart) keep their names, meaning and defaults, so your values file keeps working.
