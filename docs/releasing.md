# Releasing

For the maintainer: how a version is tagged and published. Building and testing the stack is in the [README](../README.md#build-from-source); what a version keeps stable is in [compatibility.md](compatibility.md).

## Version tags

The git tag is the version. Tags are strict semver without a prefix (`1.1.0`, not `v1.1.0`) and must be higher than the previous one. The images, the release files and, once there is one, the Helm chart all carry that one version.

To catch a bad tag before it reaches CI, enable the repository's pre-push hook once per clone:

```bash
git config core.hooksPath .githooks
```

Semver follows the contracts in [compatibility.md](compatibility.md): the payloads the receiver accepts, the database and the `dash` views, the read-only roles, the settings and the ports. Changing one of those is a major version; adding to them is a minor; a fix that changes none of them is a patch.

## Cutting a release

1. Make sure `## [Unreleased]` in [CHANGELOG.md](../CHANGELOG.md) says everything that changed.
2. Prepare the release files and commit them:

   ```bash
   scripts/prepare-release.sh 1.1.0
   ```

   This moves the Unreleased entries under `## [1.1.0] - <date>`, points every image default in `docker-compose.yml` at `1.1.0` and updates the version links at the bottom of the changelog.

3. Tag and push:

   ```bash
   git tag 1.1.0
   git push origin 1.1.0
   ```

The tag starts the [release workflow](../.github/workflows/release.yml). It checks that the tag is strict semver and higher than the last one, that `docker-compose.yml` defaults every image to the tag and that the changelog has a section for it. It then builds and pushes the images for amd64 and arm64 with build provenance and an SBOM, tagged `1.1.0`, `1.1`, `1` and `latest`, and creates the GitHub release with `docker-compose.yml` and `env.example` attached and the version's changelog section as the notes.

The first time an image is published, GHCR makes its package private: set it to public once in the package settings.
