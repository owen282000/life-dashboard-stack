{{/* Names */}}

{{- define "life-dashboard.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "life-dashboard.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* A component's resource name: <fullname>-<component>. */}}
{{- define "life-dashboard.componentName" -}}
{{- $root := index . 0 -}}
{{- printf "%s-%s" (include "life-dashboard.fullname" $root | trunc 50 | trimSuffix "-") (index . 1) -}}
{{- end -}}

{{/* Labels */}}

{{- define "life-dashboard.labels" -}}
{{- $root := index . 0 -}}
helm.sh/chart: {{ printf "%s-%s" $root.Chart.Name $root.Chart.Version | replace "+" "_" }}
{{ include "life-dashboard.selectorLabels" . }}
app.kubernetes.io/version: {{ $root.Chart.AppVersion | quote }}
app.kubernetes.io/part-of: life-dashboard
app.kubernetes.io/managed-by: {{ $root.Release.Service }}
{{- end -}}

{{- define "life-dashboard.selectorLabels" -}}
{{- $root := index . 0 -}}
app.kubernetes.io/name: {{ include "life-dashboard.name" $root }}
app.kubernetes.io/instance: {{ $root.Release.Name }}
app.kubernetes.io/component: {{ index . 1 }}
{{- end -}}

{{- define "life-dashboard.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "life-dashboard.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* An image reference from a component's image values, the global registry and tag, and appVersion. */}}
{{- define "life-dashboard.image" -}}
{{- $root := index . 0 -}}
{{- $img := index . 1 -}}
{{- $registry := $root.Values.image.registry -}}
{{- $repo := ternary (printf "%s/%s" $registry $img.repository) $img.repository (ne $registry "") -}}
{{- if $img.digest -}}
{{- printf "%s@%s" $repo $img.digest -}}
{{- else -}}
{{- printf "%s:%s" $repo (default (default $root.Chart.AppVersion $root.Values.image.tag) $img.tag) -}}
{{- end -}}
{{- end -}}

{{/* Secrets */}}

{{- define "life-dashboard.secretName" -}}
{{- default (include "life-dashboard.fullname" .) .Values.secrets.existingSecret -}}
{{- end -}}

{{/*
A secret value: the one given in values, else the one already in the cluster (lookup), else
a new random one. Arguments: root, Secret name, key, given value.
*/}}
{{- define "life-dashboard.secretValue" -}}
{{- $root := index . 0 -}}
{{- $name := index . 1 -}}
{{- $key := index . 2 -}}
{{- $given := index . 3 -}}
{{- if $given -}}
{{- $given -}}
{{- else -}}
{{- $existing := lookup "v1" "Secret" $root.Release.Namespace $name -}}
{{- if and $existing (hasKey (default dict $existing.data) $key) -}}
{{- index $existing.data $key | b64dec -}}
{{- else -}}
{{- randAlphaNum 48 -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Every Secret the chart generates, as {"<Secret name>": {"<key>": "<value>"}} in JSON. It is
worked out once per render and kept in .Values, so the Secrets and the checksums on the pods
see the same values: a fresh random value is drawn once, and a value read back with lookup
changes the checksum only when the Secret in the cluster changed.
*/}}
{{- define "life-dashboard.secretData" -}}
{{- if not (hasKey .Values "__generatedSecrets") -}}
{{- $root := . -}}
{{- $s := .Values.secrets -}}
{{- $all := dict -}}
{{- if not $s.existingSecret -}}
{{- $name := include "life-dashboard.fullname" . -}}
{{- $given := dict "WEBHOOK_SECRET" $s.webhookSecret "GRAFANA_PASSWORD" $s.grafanaPassword "API_TOKEN" $s.apiToken "MCP_TOKEN" $s.mcpToken -}}
{{- if ne .Values.postgres.mode "cnpg" -}}
{{- $_ := set $given "GRAFANA_DB_PASSWORD" $s.grafanaDbPassword -}}
{{- $_ := set $given "DASH_DB_PASSWORD" $s.dashDbPassword -}}
{{- end -}}
{{- if eq .Values.postgres.mode "bundled" -}}
{{- $_ := set $given "POSTGRES_PASSWORD" $s.postgresPassword -}}
{{- end -}}
{{- $data := dict -}}
{{- range $key, $value := $given -}}
{{- $_ := set $data $key (include "life-dashboard.secretValue" (list $root $name $key $value)) -}}
{{- end -}}
{{- with $s.pairingToken -}}
{{- $_ := set $data "PAIRING_TOKEN" . -}}
{{- end -}}
{{- $_ := set $all $name $data -}}
{{- end -}}
{{- if eq .Values.postgres.mode "cnpg" -}}
{{- if not .Values.postgres.cnpg.roles.grafanaRoSecret -}}
{{- $name := include "life-dashboard.grafanaRoSecret" . -}}
{{- $_ := set $all $name (dict "username" "grafana_ro" "password" (include "life-dashboard.secretValue" (list $root $name "password" $s.grafanaDbPassword))) -}}
{{- end -}}
{{- if not .Values.postgres.cnpg.roles.dashRoSecret -}}
{{- $name := include "life-dashboard.dashRoSecret" . -}}
{{- $_ := set $all $name (dict "username" "dash_ro" "password" (include "life-dashboard.secretValue" (list $root $name "password" $s.dashDbPassword))) -}}
{{- end -}}
{{- end -}}
{{- $_ := set .Values "__generatedSecrets" $all -}}
{{- end -}}
{{- toJson (get .Values "__generatedSecrets") -}}
{{- end -}}

{{/* A checksum for the pods: the generated values, and the names of Secrets they read. */}}
{{- define "life-dashboard.secretChecksum" -}}
{{- $refs := dict "existing" .Values.secrets.existingSecret "roles" .Values.postgres.cnpg.roles "mode" .Values.postgres.mode "external" .Values.postgres.external.existingSecret -}}
{{- printf "%s|%s" (include "life-dashboard.secretData" .) (toJson $refs) | sha256sum -}}
{{- end -}}

{{/* Postgres */}}

{{- define "life-dashboard.cnpgCluster" -}}
{{- default (include "life-dashboard.componentName" (list . "postgres")) .Values.postgres.cnpg.clusterName -}}
{{- end -}}

{{- define "life-dashboard.grafanaRoSecret" -}}
{{- default (include "life-dashboard.componentName" (list . "grafana-ro")) .Values.postgres.cnpg.roles.grafanaRoSecret -}}
{{- end -}}

{{- define "life-dashboard.dashRoSecret" -}}
{{- default (include "life-dashboard.componentName" (list . "dash-ro")) .Values.postgres.cnpg.roles.dashRoSecret -}}
{{- end -}}

{{- define "life-dashboard.dbHost" -}}
{{- if eq .Values.postgres.mode "cnpg" -}}
{{- printf "%s-rw" (include "life-dashboard.cnpgCluster" .) -}}
{{- else if eq .Values.postgres.mode "bundled" -}}
{{- include "life-dashboard.componentName" (list . "postgres") -}}
{{- else -}}
{{- .Values.postgres.external.host -}}
{{- end -}}
{{- end -}}

{{- define "life-dashboard.dbPort" -}}
{{- if eq .Values.postgres.mode "external" -}}{{ .Values.postgres.external.port }}{{- else -}}5432{{- end -}}
{{- end -}}

{{- define "life-dashboard.dbName" -}}
{{- if eq .Values.postgres.mode "external" -}}{{ .Values.postgres.external.database }}{{- else -}}{{ .Values.postgres.database }}{{- end -}}
{{- end -}}

{{/* CloudNativePG serves TLS with its own CA; the bundled Postgres has none. */}}
{{- define "life-dashboard.dbSslmode" -}}
{{- if eq .Values.postgres.mode "cnpg" -}}require{{- else if eq .Values.postgres.mode "bundled" -}}disable{{- else -}}{{ .Values.postgres.external.sslmode }}{{- end -}}
{{- end -}}

{{/* The env entries for the passwords of grafana_ro and dash_ro. */}}
{{- define "life-dashboard.roPasswordEnv" -}}
{{- $root := index . 0 -}}
{{- $which := index . 1 -}}
{{- if eq $root.Values.postgres.mode "cnpg" }}
- name: {{ ternary "GRAFANA_DB_PASSWORD" "DASH_DB_PASSWORD" (eq $which "grafana") }}
  valueFrom:
    secretKeyRef:
      name: {{ ternary (include "life-dashboard.grafanaRoSecret" $root) (include "life-dashboard.dashRoSecret" $root) (eq $which "grafana") }}
      key: password
{{- else }}
- name: {{ ternary "GRAFANA_DB_PASSWORD" "DASH_DB_PASSWORD" (eq $which "grafana") }}
  valueFrom:
    secretKeyRef:
      name: {{ include "life-dashboard.secretName" $root }}
      key: {{ ternary "GRAFANA_DB_PASSWORD" "DASH_DB_PASSWORD" (eq $which "grafana") }}
{{- end }}
{{- end -}}

{{/* Pod and container security */}}

{{- define "life-dashboard.podSecurityContext" -}}
{{- $uid := index . 0 -}}
{{- $override := index . 1 -}}
{{- $base := dict "runAsNonRoot" true "runAsUser" $uid "runAsGroup" $uid "fsGroup" $uid "fsGroupChangePolicy" "OnRootMismatch" "seccompProfile" (dict "type" "RuntimeDefault") -}}
{{- toYaml (mergeOverwrite $base (default dict $override)) -}}
{{- end -}}

{{- define "life-dashboard.containerSecurityContext" -}}
{{- $base := dict "allowPrivilegeEscalation" false "readOnlyRootFilesystem" true "privileged" false "capabilities" (dict "drop" (list "ALL")) "seccompProfile" (dict "type" "RuntimeDefault") -}}
{{- toYaml (mergeOverwrite $base (default dict .)) -}}
{{- end -}}

{{- define "life-dashboard.imagePullSecrets" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
{{- range . }}
  - name: {{ ternary .name . (kindIs "map" .) }}
{{- end }}
{{- end }}
{{- end -}}

{{/* Checks that stop a render with a clear message. */}}
{{- define "life-dashboard.validate" -}}
{{- $pg := .Values.postgres -}}
{{- if and (eq $pg.mode "external") (not $pg.external.existingSecret) -}}
{{- fail "postgres.mode external needs postgres.external.existingSecret: a Secret with the database URL" -}}
{{- end -}}
{{- if and (eq $pg.mode "external") (not $pg.external.host) -}}
{{- fail "postgres.mode external needs postgres.external.host, where Grafana and the MCP server connect" -}}
{{- end -}}
{{- if and (eq $pg.mode "cnpg") $pg.cnpg.backup.enabled (not $pg.cnpg.backup.objectStore.create) (not $pg.cnpg.backup.objectStore.name) -}}
{{- fail "postgres.cnpg.backup needs objectStore.create with a configuration, or the name of an existing ObjectStore" -}}
{{- end -}}
{{- if and (eq $pg.mode "cnpg") $pg.cnpg.backup.enabled $pg.cnpg.backup.objectStore.create (not $pg.cnpg.backup.objectStore.configuration) -}}
{{- fail "postgres.cnpg.backup.objectStore.configuration is empty: give at least destinationPath and credentials" -}}
{{- end -}}
{{- if and (eq $pg.mode "cnpg") $pg.cnpg.recovery.enabled (not $pg.cnpg.recovery.objectStoreName) -}}
{{- fail "postgres.cnpg.recovery needs objectStoreName: the ObjectStore that holds the backup" -}}
{{- end -}}
{{- $r := .Values.receiver -}}
{{- if and (or $r.ingress.enabled $r.httpRoute.enabled) $r.expose.pair -}}
{{- if not $r.pairing.page -}}
{{- fail "receiver.expose.pair routes /pair, but receiver.pairing.page is off" -}}
{{- end -}}
{{- if and (not .Values.secrets.pairingToken) (not .Values.secrets.existingSecret) -}}
{{- fail "receiver.expose.pair routes /pair through a proxy, where it answers only with ?token=: set secrets.pairingToken, or pair through kubectl exec or a port-forward" -}}
{{- end -}}
{{- end -}}
{{- if and .Values.secrets.pairingToken (lt (len .Values.secrets.pairingToken) 16) -}}
{{- fail "secrets.pairingToken needs at least 16 characters" -}}
{{- end -}}
{{- end -}}
