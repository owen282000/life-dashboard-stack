# Security policy

The Life Dashboard Stack takes health and screen time data from your phones and keeps it in a database you run, with dashboards, an API and an MCP server on top. This page explains [how a payload is trusted](#how-a-payload-is-trusted), how to [report a security problem](#reporting-a-vulnerability) and [what is in scope](#scope). [Exposing it safely](docs/operations.md#exposing-it-safely) covers how to run it.

## Supported versions

Only the latest release receives security fixes. The images, the Helm chart and `install.sh` share one version.

| Version | Supported |
| ------- | --------- |
| Latest release | Yes |
| Older versions | No |

## How a payload is trusted

With `WEBHOOK_SECRET` set, every payload must carry `X-Signature: sha256=<hex>`, an HMAC-SHA256 over the raw body with that secret as the key. The receiver checks the size from `Content-Length` before it reads the body, then the signature, in constant time, before anything is parsed or stored. A missing or wrong signature gets a 401 and nothing is stored. Without `WEBHOOK_SECRET` the receiver stores unsigned posts from anyone who can reach it, and says so in its log at every start.

A signed payload it took is answered with a 200 that names the request's own signature in `writeback.in_reply_to`, as the Home Assistant integration answers, so the apps' pairing test can see that the receiver holds the same secret. That answer carries its own `X-Signature`, over its exact bytes, with a key derived from the secret (HMAC-SHA256 of `WEBHOOK_SECRET` and `life-dashboard-response-v1`) rather than the secret itself, so a captured request can never pass as an answer. It announces no write-back protocol, so the apps never take measurements from the receiver. [What a version keeps stable](docs/compatibility.md#the-payloads-the-receiver-accepts) has the format.

## Reporting a vulnerability

Please do not open a public issue for a security vulnerability.

Report it through [GitHub's private vulnerability reporting](https://github.com/owen282000/life-dashboard-stack/security/advisories/new) for this repository. If you do not have a GitHub account, email owenvogelaar@hotmail.com instead, with "Security" in the subject.

Useful in a report: the stack version, whether it runs on Docker Compose, Podman or the Helm chart, what an attacker needs (a position on the network, the webhook URL, a token, access to the host), and the steps to reproduce it.

### What happens next

- You get a reply within 7 days, confirming the report and saying whether it is in scope.
- The target for a fix depends on severity: a release within 14 days for a problem that exposes health data or secrets, within 30 days for a moderate problem, and in a regular release for a minor one. If a fix needs longer, you hear why and when to expect it.
- The fix is published as a [GitHub security advisory](https://github.com/owen282000/life-dashboard-stack/security/advisories) once the release is out, with a CVE where one applies.
- You are credited in the advisory and the changelog by the name you choose, unless you prefer not to be named.

The project has one maintainer and no bug bounty.

## Scope

The stack keeps a history of health data, so reports about any of these are especially welcome:

- **The receiver and its webhook.** A payload stored or a record deleted without a valid signature while `WEBHOOK_SECRET` is set, anything read or parsed before the size and signature checks, a signed answer that can be forged or passed off for another request, or the secret in an answer or in the log.
- **The pairing page.** `/pair` shows the signing secret, so it is answered only for a request from a private or loopback address that carries no proxy header, or one with the right `PAIRING_TOKEN`, and is a 404 for everything else. A way to see it from outside those limits, or `PAIRING_TOKEN` showing up in the receiver's log, is in scope.
- **The read-only API and the MCP server.** `/api/` and the MCP server answer only with their own token (`API_TOKEN`, `MCP_TOKEN`, at least 16 characters each). Both read the `dash` views as `dash_ro`, which can only `SELECT` from them, and neither takes SQL. A way in without the token, to write or delete through either, to read past the `dash` views, or to run SQL of your own is in scope.
- **The database roles.** `grafana_ro` reads `records`, `buckets` and the `dash` views, and `dash_ro` only the views, both in read-only transactions with a statement timeout. Either one getting more, such as the payload ledger or a write, is in scope.
- **The Helm chart's defaults.** Every pod runs as non-root with a read-only root file system, no capabilities and the `RuntimeDefault` seccomp profile, so the chart installs under the `restricted` Pod Security level; the chart's own pods get no service account token; and only `/webhook` is routed by default, with the pairing page and the pairing block in the log off. With `networkPolicy.enabled: true`, the NetworkPolicies should let through only what [kubernetes.md](docs/kubernetes.md#networkpolicies) lists. A default that breaks one of these is in scope.
- **`install.sh`.** It writes `.env` with a random `WEBHOOK_SECRET`, passwords and tokens from the kernel's random generator, readable only by you (mode 600), and never overwrites an existing `.env`. A weak or predictable secret, a `.env` or temporary file others can read, or a secret printed where it doesn't belong is in scope.
- **The images.** The receiver, MCP, Grafana and db images are scanned with Trivy in CI. A known vulnerability that a release ships while a fixed version exists is in scope.

Out of scope: a receiver or Grafana reached from the internet against the advice in [Exposing it safely](docs/operations.md#exposing-it-safely), such as Grafana without a VPN, the receiver without TLS or without `WEBHOOK_SECRET`, or `/pair` behind a proxy that sends no forwarding header; problems that need access to the host, the cluster's Secrets or the database owner; and Grafana, Postgres and CloudNativePG themselves, which have their own policies. The phone apps have their own policies, the Android app in [owen282000/life-dashboard-companion-app](https://github.com/owen282000/life-dashboard-companion-app/security/policy) and the iOS app in [owen282000/life-dashboard-companion-app-ios](https://github.com/owen282000/life-dashboard-companion-app-ios/security/policy), and so does the Home Assistant integration, in [owen282000/life-dashboard-ha](https://github.com/owen282000/life-dashboard-ha/security/policy).
