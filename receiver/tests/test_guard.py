"""tools/pg-guard.sh with throwaway Docker volumes.

An old volume with a database stops the start; an empty one, the restore marker or
IGNORE_OLD_VOLUME=1 let it through. "true" stands in for postgres: the image's entrypoint
runs any other command as given.
"""

import subprocess
import uuid

import pytest

from conftest import REPO_DIR, _compose_image

GUARD = REPO_DIR / "tools" / "pg-guard.sh"


def docker(*args, check=True):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


@pytest.fixture
def volumes():
    tag = uuid.uuid4().hex[:8]
    names = {"old": f"ld-a-test-old-{tag}", "new": f"ld-a-test-new-{tag}"}
    for name in names.values():
        docker("volume", "create", "--label", "com.docker.compose.project=ld-a-test", name)
    yield names
    for name in names.values():
        docker("volume", "rm", "-f", name, check=False)


def write(volume, path, text):
    docker("run", "--rm", "-v", f"{volume}:/v", "--entrypoint", "sh", _compose_image(),
           "-c", f"printf '{text}' > /v/{path}")


def run_guard(vols, *env):
    args = ["run", "--rm", "-v", f"{vols['old']}:/old:ro", "-v", f"{vols['new']}:/var/lib/postgresql",
            "-v", f"{GUARD}:/usr/local/bin/pg-guard.sh:ro"]
    for e in env:
        args += ["-e", e]
    args += ["--entrypoint", "/bin/sh", _compose_image(), "/usr/local/bin/pg-guard.sh", "true"]
    return docker(*args, check=False)


def test_empty_old_volume_passes(volumes):
    assert run_guard(volumes).returncode == 0


def test_old_volume_with_data_fails_with_a_clear_message(volumes):
    write(volumes["old"], "PG_VERSION", "16\\n")
    result = run_guard(volumes)
    assert result.returncode == 1
    assert "tools/upgrade-postgres.sh" in result.stderr
    assert "Postgres 16 volume" in result.stderr


def test_marker_passes(volumes):
    write(volumes["old"], "PG_VERSION", "16\\n")
    write(volumes["new"], ".restored-from-16", "2026-10-04\\n")
    assert run_guard(volumes).returncode == 0


def test_ignore_old_volume_passes(volumes):
    write(volumes["old"], "PG_VERSION", "16\\n")
    assert run_guard(volumes, "IGNORE_OLD_VOLUME=1").returncode == 0
