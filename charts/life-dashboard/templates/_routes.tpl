{{/* The paths that reach a component from outside: the receiver's opted-in ones, or all of Grafana. */}}
{{- define "life-dashboard.routePaths" -}}
{{- $root := index . 0 -}}
{{- $component := index . 1 -}}
{{- if eq $component "receiver" -}}
{{- $e := $root.Values.receiver.expose -}}
{{- $paths := list -}}
{{- if $e.webhook }}{{ $paths = append $paths "/webhook" }}{{ end -}}
{{- if $e.api }}{{ $paths = append $paths "/api" }}{{ end -}}
{{- if $e.pair }}{{ $paths = append $paths "/pair" }}{{ end -}}
{{- toJson $paths -}}
{{- else -}}
{{- toJson (list "/") -}}
{{- end -}}
{{- end -}}
