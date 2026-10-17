# Releasing

For the maintainer: how a version is tagged and published. Building and testing the stack is in [operations.md](operations.md#build-from-source); what a version keeps stable is in [compatibility.md](compatibility.md).

## Version tags

The git tag is the version. Tags are strict semver without a prefix (`1.1.0`, not `v1.1.0`) and must be higher than the previous one. Everything a release publishes carries that one version: the images, the release files, the Helm chart, whose `version` and `appVersion` in `charts/life-dashboard/Chart.yaml` are both the tag, and the MCP server's entry in the MCP Registry, whose `version` in [server.json](../server.json) is the tag too. There is no separate chart or server version.

To catch a bad tag before it reaches CI, enable the repository's pre-push hook once per clone:

```bash
git config core.hooksPath .githooks
```

Semver follows the contracts in [compatibility.md](compatibility.md): the payloads the receiver accepts, the database and the `dash` views, the read-only roles, the settings and the ports, and the chart's values and resource names. Changing one of those is a major version; adding to them is a minor; a fix that changes none of them is a patch.

## Cutting a release

1. Make sure `## [Unreleased]` in [CHANGELOG.md](../CHANGELOG.md) says everything that changed.
2. Prepare the release files and commit them:

   ```bash
   scripts/prepare-release.sh 1.1.0
   ```

   This moves the Unreleased entries under `## [1.1.0] - <date>`, points every image default in `docker-compose.yml` at `1.1.0`, sets `VERSION` in `install.sh` to `1.1.0`, sets `version` and `appVersion` in `charts/life-dashboard/Chart.yaml` to `1.1.0`, along with the four images in its `artifacthub.io/images`, writes the new section's entries into its `artifacthub.io/changes` (and `artifacthub.io/containsSecurityUpdates` when there is a Security part), sets `version` and the image tag in `server.json` and the versions `mcp/server.py` reports and `receiver/receiver.py` names in its answer to `1.1.0`, and updates the version links at the bottom of the changelog.

3. Tag and push:

   ```bash
   git tag 1.1.0
   git push origin 1.1.0
   ```

The tag starts the [release workflow](../.github/workflows/release.yml). It checks that the tag is strict semver and higher than the last one, that `docker-compose.yml` defaults every image to the tag and `install.sh` carries it, that the chart's `version`, `appVersion` and listed images are the tag and that version isn't on GHCR yet, that `server.json` has the tag as its version and image tag under the name in the MCP image's `io.modelcontextprotocol.server.name` label and `mcp/server.py` reports it, that `receiver/receiver.py` names it, and that the changelog has a section for it. It then builds and pushes the images for amd64 and arm64 with build provenance and an SBOM, tagged `1.1.0`, `1.1`, `1` and `latest`, packages the chart and pushes it to `oci://ghcr.io/owen282000/charts/life-dashboard` as `1.1.0` with a build attestation, publishes `server.json` to the [MCP Registry](#the-mcp-registry), and creates the GitHub release with `docker-compose.yml`, `env.example` and `install.sh` attached. `install.sh` fetches the other two of its own version, so `releases/latest/download/install.sh` always installs one release's files together. The notes are the version's changelog section plus an Install part with the one-line install, the Compose files, the four images and the `helm install` line for that version.

The first time an image or the chart is published, GHCR makes its package private: set it to public once in the package settings. For the chart that's the package `charts/life-dashboard`, after the first release that contains it.

## The MCP Registry

The MCP server is listed in the official [MCP Registry](https://registry.modelcontextprotocol.io) as `io.github.owen282000/life-dashboard`, which MCP clients and catalogues read. The entry is [server.json](../server.json): the image `ghcr.io/owen282000/life-dashboard-mcp:<version>` run with `docker` over stdio, on the stack's Docker network, with the `PG*` settings for `dash_ro`. The registry only accepts the image if its label `io.modelcontextprotocol.server.name` (set in [mcp/Dockerfile](../mcp/Dockerfile)) equals the name, and only if the image is public.

The `mcp-registry` job of the release workflow publishes it after the images, with [`mcp-publisher`](https://github.com/modelcontextprotocol/registry/tree/main/docs/modelcontextprotocol-io) and GitHub OIDC: the workflow's token proves the namespace `io.github.owen282000`, so there is no secret to set and nothing to register. A version that is already in the registry is skipped, so the job can be run again. A published version can't be changed or removed, only marked deprecated with `mcp-publisher status`.

The first release with the label (the one after 1.0.1) is the first one listed. To check it: `curl -s "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.owen282000"`.

## Artifact Hub

The chart is registered on [Artifact Hub](https://artifacthub.io/packages/search?repo=life-dashboard) as the Helm repository `life-dashboard` (`oci://ghcr.io/owen282000/charts/life-dashboard`), which reads the chart straight from GHCR. The annotations in `Chart.yaml` give it the category, the images, links, screenshots and each version's changes.

[charts/artifacthub-repo.yml](../charts/artifacthub-repo.yml) holds the repository's ID, which makes it a verified publisher, and its owner. Artifact Hub reads it from the tag `artifacthub.io` of the chart's repository, so the chart job of the release workflow pushes it there with [ORAS](https://oras.land) after each chart. Artifact Hub picks it up when it next processes the repository, which it does when a new version appears.

To push it now, for the charts already published, without waiting for a release: from the repository root, with ORAS and a GitHub token that may write packages (`write:packages`),

```bash
echo "$GITHUB_TOKEN" | oras login ghcr.io -u owen282000 --password-stdin
cd charts && oras push ghcr.io/owen282000/charts/life-dashboard:artifacthub.io \
  --config /dev/null:application/vnd.cncf.artifacthub.config.v1+yaml \
  artifacthub-repo.yml:application/vnd.cncf.artifacthub.repository-metadata.layer.v1.yaml
```

The verified badge follows the next time Artifact Hub processes the repository.

## Versions pinned by hand

Dependabot updates the GitHub Actions, the Dockerfiles' base images and the Python packages. These versions it can't see, so check them before each release and keep the copies equal:

- **CloudNativePG operator** (`1.30.1`): `CNPG_VERSION` in [test.yml](../.github/workflows/test.yml), and in [kubernetes.md](kubernetes.md) twice: the install command and Tested on.
- **Barman Cloud plugin** (`v0.15.1`) and **cert-manager** (`v1.21.2`): [kubernetes.md](kubernetes.md).
- **CloudNativePG Postgres image** (`18.6-standard-trixie`): `postgres.cnpg.imageName` in [values.yaml](../charts/life-dashboard/values.yaml) and [kubernetes.md](kubernetes.md). Keep it on the same Postgres minor as [db/Dockerfile](../db/Dockerfile).
- **Tools in the workflows**: Helm 4 and Helm 3 (`env` in test.yml and `HELM_VERSION` in [release.yml](../.github/workflows/release.yml)), chart-testing, kind, the `kindest/node` image, Calico, and the kubeconform, actionlint and shellcheck images, all in the `env` of test.yml.
- **Python** (`3.14.8`): `python-version` in test.yml, equal to the Dockerfiles of the receiver and the MCP server.
- **ORAS** (`1.3.4`): `ORAS_VERSION` and its `ORAS_SHA256` (of `oras_<version>_linux_amd64.tar.gz`) in the chart job of release.yml.
- **mcp-publisher** (`v1.8.1`): `MCP_PUBLISHER_VERSION` and its `MCP_PUBLISHER_SHA256` (of `mcp-publisher_linux_amd64.tar.gz`, from the release's checksums file) in release.yml. The registry refuses an `mcp-publisher` that is too old with `invalid audience`.
