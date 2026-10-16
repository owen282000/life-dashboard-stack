# Releasing

For the maintainer: how a version is tagged and published. Building and testing the stack is in [operations.md](operations.md#build-from-source); what a version keeps stable is in [compatibility.md](compatibility.md).

## Version tags

The git tag is the version. Tags are strict semver without a prefix (`1.1.0`, not `v1.1.0`) and must be higher than the previous one. Everything a release publishes carries that one version: the images, the release files, and the Helm chart, whose `version` and `appVersion` in `charts/life-dashboard/Chart.yaml` are both the tag. There is no separate chart version.

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

   This moves the Unreleased entries under `## [1.1.0] - <date>`, points every image default in `docker-compose.yml` at `1.1.0`, sets `version` and `appVersion` in `charts/life-dashboard/Chart.yaml` to `1.1.0` and updates the version links at the bottom of the changelog.

3. Tag and push:

   ```bash
   git tag 1.1.0
   git push origin 1.1.0
   ```

The tag starts the [release workflow](../.github/workflows/release.yml). It checks that the tag is strict semver and higher than the last one, that `docker-compose.yml` defaults every image to the tag, that the chart's `version` and `appVersion` are the tag and that version isn't on GHCR yet, and that the changelog has a section for it. It then builds and pushes the images for amd64 and arm64 with build provenance and an SBOM, tagged `1.1.0`, `1.1`, `1` and `latest`, packages the chart and pushes it to `oci://ghcr.io/owen282000/charts/life-dashboard` as `1.1.0` with a build attestation, and creates the GitHub release with `docker-compose.yml` and `env.example` attached. The notes are the version's changelog section plus an Install part with the Compose files, the four images and the `helm install` line for that version.

The first time an image or the chart is published, GHCR makes its package private: set it to public once in the package settings. For the chart that's the package `charts/life-dashboard`, after the first release that contains it.

## Versions pinned by hand

Dependabot updates the GitHub Actions, the Dockerfiles' base images and the Python packages. These versions it can't see, so check them before each release and keep the copies equal:

- **CloudNativePG operator** (`1.30.1`): `CNPG_VERSION` in [test.yml](../.github/workflows/test.yml), and in [kubernetes.md](kubernetes.md) twice: the install command and Tested on.
- **Barman Cloud plugin** (`v0.15.1`) and **cert-manager** (`v1.21.2`): [kubernetes.md](kubernetes.md).
- **CloudNativePG Postgres image** (`18.6-standard-trixie`): `postgres.cnpg.imageName` in [values.yaml](../charts/life-dashboard/values.yaml) and [kubernetes.md](kubernetes.md). Keep it on the same Postgres minor as [db/Dockerfile](../db/Dockerfile).
- **Tools in the workflows**: Helm 4 and Helm 3 (`env` in test.yml and `HELM_VERSION` in [release.yml](../.github/workflows/release.yml)), chart-testing, kind, the `kindest/node` image, Calico, and the kubeconform, actionlint and shellcheck images, all in the `env` of test.yml.
- **Python** (`3.14.8`): `python-version` in test.yml, equal to the Dockerfiles of the receiver and the MCP server.
