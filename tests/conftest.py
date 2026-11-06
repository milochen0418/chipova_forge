"""Every test session gets its own data folder (FORGE_HOME), never yours."""

from __future__ import annotations

import os
import shutil
import tempfile

import pytest

_HOME = tempfile.mkdtemp(prefix="forge-tests-")
os.environ["FORGE_HOME"] = _HOME
os.environ.setdefault("REFLEX_TELEMETRY_ENABLED", "false")


def docker_ready() -> bool:
    try:
        from chipova_forge.jobs import builds, docker_runner

        return docker_runner.available() and docker_runner.image_info(builds.SIM_IMAGE) is not None
    except Exception:  # noqa: BLE001
        return False


def pytest_collection_modifyitems(config, items):  # noqa: ANN001
    if docker_ready():
        return
    skip = pytest.mark.skip(reason="needs Docker and the chipova-forge/sim:1 image")
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(skip)


@pytest.fixture()
def me():
    from chipova_forge.services.actors import Actor

    return Actor("owner@example.com", "Owner", email="owner@example.com")


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001
    shutil.rmtree(_HOME, ignore_errors=True)
