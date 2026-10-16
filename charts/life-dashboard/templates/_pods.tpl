{{/* The pod spec of a short-lived pod on the receiver image: the demo and helm test. */}}
{{- define "life-dashboard.toolPodSpec" -}}
{{- $root := index . 0 -}}
{{- $name := index . 1 -}}
{{- $env := index . 2 -}}
{{- $script := index . 3 -}}
restartPolicy: Never
serviceAccountName: {{ include "life-dashboard.serviceAccountName" $root }}
automountServiceAccountToken: false
enableServiceLinks: false
{{- include "life-dashboard.imagePullSecrets" $root }}
securityContext:
  {{- include "life-dashboard.podSecurityContext" (list 65534 dict) | nindent 2 }}
containers:
  - name: {{ $name }}
    image: {{ include "life-dashboard.image" (list $root $root.Values.receiver.image) }}
    imagePullPolicy: {{ $root.Values.image.pullPolicy }}
    command: ["python", "-c", {{ $script | quote }}]
    env:
      - name: PYTHONDONTWRITEBYTECODE
        value: "1"
      {{- $env | nindent 6 }}
    resources:
      requests:
        cpu: 10m
        memory: 64Mi
      limits:
        memory: 256Mi
    securityContext:
      {{- include "life-dashboard.containerSecurityContext" dict | nindent 6 }}
    volumeMounts:
      - name: tmp
        mountPath: /tmp
volumes:
  - name: tmp
    emptyDir:
      sizeLimit: 64Mi
{{- end -}}

{{/* Python for the pods above: wait for a URL, fetch one, and fail with a message. */}}
{{- define "life-dashboard.pyHelpers" -}}
import base64, json, os, sys, time, urllib.error, urllib.request

def fetch(url, headers=None, data=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")

def wait(url, seconds=600):
    deadline = time.time() + seconds
    while True:
        try:
            if fetch(url)[0] == 200:
                return
        except OSError:
            pass
        if time.time() > deadline:
            sys.exit(f"{url} did not answer 200 within {seconds} s")
        time.sleep(3)

def check(ok, message):
    print(("ok   " if ok else "FAIL ") + message, flush=True)
    if not ok:
        sys.exit(1)
{{- end -}}
