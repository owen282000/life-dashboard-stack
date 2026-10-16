# Kubernetes

The stack also comes as a Helm chart, `life-dashboard`, for a Kubernetes cluster of your own. It runs the same four images as Docker Compose: the receiver, Grafana with the six dashboards, the optional MCP server, and Postgres in one of three ways. Pick CloudNativePG if you can: it gives you replicas, failover and backups to object storage. The chart works with Helm 3 and Helm 4.

## Contents

- [What the chart runs](#what-the-chart-runs)
- [Install CloudNativePG](#install-cloudnativepg)
- [Install the chart](#install-the-chart)
- [Postgres: three modes](#postgres-three-modes)
- [Secrets](#secrets)
- [Pair a phone](#pair-a-phone)
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
- [Values that stay stable](#values-that-stay-stable)

## What the chart runs

| Part | Kind | Port | Notes |
|---|---|---|---|
| Receiver | Deployment and Service | 8080 | Runs the migrations at every start; several replicas share them safely. Probes on `/health`. |
| Grafana | Deployment (one pod) and Service | 3000 | The six dashboards come with the image. The data source points at this release's Postgres. A volume for `/var/lib/grafana` is on by default. |
| MCP server | Deployment and Service, with `mcp.enabled` | 8090 | Off by default, like the `mcp` profile. |
| Postgres | CloudNativePG `Cluster`, a StatefulSet, or none | 5432 | See [the three modes](#postgres-three-modes). |

Every pod runs as a non-root user with a read-only root file system, all capabilities dropped and the `RuntimeDefault` seccomp profile, so the chart installs in a namespace with the `restricted` Pod Security level. No pod gets a service account token: none of them talks to the Kubernetes API.

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

From a clone of the repository:

```bash
kubectl create namespace life-dashboard
helm install lifedash charts/life-dashboard -n life-dashboard \
  --set timezone=Europe/Amsterdam \
  --set receiver.publicUrl=https://lifedash.example.com \
  --wait --timeout 10m
```

Once the chart is released, it's on GHCR as an OCI chart, and you install it without a clone:

```bash
helm install lifedash oci://ghcr.io/owen282000/charts/life-dashboard -n life-dashboard \
  --version <chart version> -f my-values.yaml --wait --timeout 10m
```

Put your settings in a values file rather than in `--set`, so an upgrade uses the same ones. `helm show values` prints every value with a comment, and `values.schema.json` refuses a value with a typo before anything is installed. The chart's version is its own; its `appVersion` is the version of the stack, and the images default to it. `image.tag` pins all four images to another version.

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

### cnpg

The default. The chart creates a CloudNativePG `Cluster` named `<release>-life-dashboard-postgres`, and the operator does the rest: the database `lifedash` with its owner `lifedash`, a Secret `<cluster>-app` with the owner's connection URL, which the receiver reads, and the Services `<cluster>-rw` (the primary), `-ro` and `-r`.

```yaml
postgres:
  mode: cnpg
  cnpg:
    instances: 3          # one primary, two replicas; 1 is the default
    storage:
      size: 10Gi
      storageClass: ""    # empty: the cluster's default
    walStorage:
      enabled: false      # WAL on a volume of its own
    parameters: {}        # postgresql.conf, such as shared_buffers: 256MB
    affinity: {}          # CloudNativePG's own affinity section
```

It runs CloudNativePG's own Postgres 18 image, `ghcr.io/cloudnative-pg/postgresql:18.6-standard-trixie`, not the stack's db image. The db image only adds a guard for an old Docker volume of Postgres 16, which a new cluster never has, and CloudNativePG builds its images for its instance manager, with the time zone data the receiver checks `TZ` against. `postgres.cnpg.imageName` picks another; keep it at major 18.

With more than one instance, a failover is a few seconds without data: the receiver connects per request to `-rw`, which follows the new primary, so it needs no restart. A payload that arrives in those seconds gets a 500, and the app sends it again.

**The read-only roles.** CloudNativePG's app user owns the database but is no superuser and may not create roles, so the receiver can't create `grafana_ro` and `dash_ro` there as it does with Docker Compose. The chart lets CloudNativePG do it:

- `managed.roles` in the `Cluster` creates both with `LOGIN` and the password from a Secret each (`<release>-life-dashboard-grafana-ro` and `-dash-ro`, type `kubernetes.io/basic-auth`). The chart generates those, or `postgres.cnpg.roles.grafanaRoSecret` and `dashRoSecret` name your own. A changed password there reaches Postgres within seconds.
- The `Cluster`'s first start (`postInitApplicationSQL`) gives them what managed roles can't: the statement timeouts of 30 and 15 seconds and read-only sessions.
- The receiver sees that it may not alter them, says so in its log (`grafana_ro is managed outside the receiver`), and grants them their reads as it does everywhere: `grafana_ro` the `records` and `buckets` tables and the `dash` views, `dash_ro` the views only. A role that lacks its settings gets a warning in the log, with the `ALTER ROLE` that adds them.

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

It's one instance with no replica, so a node that goes down takes the database with it until the pod runs again, and a lost volume is lost data. There are no backups of its own: take dumps, see [Backups](#backups). The owner's password is `POSTGRES_PASSWORD` in the release's Secret; Postgres sets it once, when it creates the database, so don't change it afterwards.

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

The user in the URL must own the database. If it's a superuser, or may create roles, the receiver creates `grafana_ro` and `dash_ro` as with Docker Compose. If not (most managed databases), create them first, as a user that may:

```sql
CREATE ROLE grafana_ro LOGIN PASSWORD '<GRAFANA_DB_PASSWORD>';
ALTER ROLE grafana_ro SET statement_timeout = '30s';
ALTER ROLE grafana_ro SET default_transaction_read_only = on;
CREATE ROLE dash_ro LOGIN PASSWORD '<DASH_DB_PASSWORD>';
ALTER ROLE dash_ro SET statement_timeout = '15s';
ALTER ROLE dash_ro SET default_transaction_read_only = on;
```

The receiver then grants them their reads. Without the roles, it stops at its start with a message that says so.

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

It needs the keys `WEBHOOK_SECRET`, `GRAFANA_PASSWORD`, `API_TOKEN` and `MCP_TOKEN`; `GRAFANA_DB_PASSWORD` and `DASH_DB_PASSWORD` unless the mode is `cnpg` (there the role passwords live in the two basic-auth Secrets above); `POSTGRES_PASSWORD` in bundled mode; and optionally `PAIRING_TOKEN`. API and MCP tokens need at least 16 characters.

`lookup` only works when Helm talks to the cluster. `helm template`, `helm install --dry-run` and Argo CD render without it, so they'd generate new values on every render: use `existingSecret` there, see [Argo CD and Flux](#argo-cd-and-flux).

## Pair a phone

Set `receiver.publicUrl` to the address your phone reaches the receiver on, such as `https://lifedash.example.com`; the pairing code holds it plus `/webhook/<label>`. The receiver prints the code in its log at every start (`kubectl logs deploy/lifedash-life-dashboard-receiver`), and `/pair` shows it as a page. The easiest way to open that page is a port-forward from your own computer:

```bash
kubectl -n life-dashboard port-forward svc/lifedash-life-dashboard-receiver 8080:8080
```

Then open `http://localhost:8080/pair` and scan the code with the app, as in the [README](../README.md#pair-the-app). A port-forward arrives at the receiver from its own loopback address, so the page answers.

`/pair` answers a request from a private address that carries no proxy header. Inside a cluster, every pod has a private address, and so does a request that a `LoadBalancer` or `NodePort` Service passes on with `externalTrafficPolicy: Cluster`, from wherever it came. If such a Service makes the receiver reachable from the internet, set `receiver.pairing.page: false`. An Ingress or a Gateway adds `X-Forwarded-For`, so through them `/pair` answers only with `?token=` and `secrets.pairingToken`. `receiver.pairing.log: false` keeps the secret out of the log.

## Reach it from outside

The phones need `/webhook` on the receiver, over HTTPS for an iPhone. The chart has an Ingress and a Gateway API `HTTPRoute` for the receiver and for Grafana, both off by default. For the receiver they route only `/webhook`; `/api` and `/pair` are opt-in:

```yaml
receiver:
  publicUrl: https://lifedash.example.com
  expose:
    webhook: true
    api: false     # the read-only API, behind its token
    pair: false    # only with secrets.pairingToken; better: a port-forward
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
- `grafana.ingress` and `grafana.httpRoute` route all of Grafana. Keep Grafana on your own network or behind a VPN or an authenticating proxy, as the [README](../README.md#exposing-it-safely) says, and set `grafana.rootUrl` to its address.
- `receiver.service.type: LoadBalancer` works for a LAN address from MetalLB or similar, but it exposes every path, `/pair` included; see [Pair a phone](#pair-a-phone).

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
helm upgrade lifedash charts/life-dashboard -n life-dashboard -f my-values.yaml --wait --timeout 10m
```

From GHCR, `helm upgrade lifedash oci://ghcr.io/owen282000/charts/life-dashboard --version <new version> ...`. A new chart version usually brings a new `appVersion` and with it new images; the receiver runs the database migrations at its start. The generated secrets, the data and Grafana's volume stay. Within a major version of the chart, your values file keeps working, see [Values that stay stable](#values-that-stay-stable). The [changelog](../CHANGELOG.md) says what's new.

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

Both can pull the chart from GHCR once it's released (`oci://ghcr.io/owen282000/charts`).

## NetworkPolicies

Off by default, because they need a network plugin that enforces them (most do: Calico, Cilium, and kind's own). With `networkPolicy.enabled: true`:

- The receiver accepts port 8080, Grafana 3000 and the MCP server 8090 from anyone, or only from the peers in `networkPolicy.receiver.from`, `.grafana.from` and `.mcp.from`, such as your ingress controller's namespace. The demo and the test pods of the release are always allowed.
- Those three may only reach DNS and the database: the CloudNativePG pods of their own Cluster, the bundled Postgres, or `postgres.external.port` (narrowed with `networkPolicy.externalDatabase.to`). Grafana gets more with `networkPolicy.grafanaEgress`, for instance SMTP for alerts.
- The bundled Postgres accepts only the receiver, Grafana and the MCP server, and reaches nothing.

```yaml
networkPolicy:
  enabled: true
  receiver:
    from:
      - namespaceSelector:
          matchLabels:
            kubernetes.io/metadata.name: ingress-nginx
```

The chart doesn't put policies on CloudNativePG's pods: the operator has to reach them, and the instances each other. If your namespace denies traffic by default, follow CloudNativePG's own guidance for those.

## Security

- The pods meet the `restricted` Pod Security Standard: non-root users (65534 for the receiver, the MCP server and the test pods, 472 for Grafana, 70 for the bundled Postgres), a read-only root file system with an `emptyDir` for `/tmp`, no privilege escalation, all capabilities dropped, `RuntimeDefault` seccomp. `*.podSecurityContext` and `*.securityContext` change them.
- No service account token is mounted, and the service account has no role.
- Requests and memory limits are set for every pod, with no CPU limits, so a backfill isn't throttled.
- Grafana's data source reads as `grafana_ro`, and the API and the MCP server as `dash_ro`, never as the owner. The rest of [Exposing it safely](../README.md#exposing-it-safely) holds as well.

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

## Values that stay stable

Within a major version of the chart, the values in [docs/compatibility.md](compatibility.md#the-helm-chart) keep their names, meaning and defaults, so your values file keeps working.
