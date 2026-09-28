"""Home Assistant integration test setup (requires pytest-homeassistant-custom-component)."""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest
from pytest_homeassistant_custom_component.common import get_test_config_dir


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow loading custom_components/evac_relay."""
    return


@pytest.fixture(autouse=True)
def fresh_blueprint_dir():
    """The test config dir is shared across runs; start each test without an installed blueprint."""
    path = Path(get_test_config_dir("blueprints", "automation", "evac_relay"))
    shutil.rmtree(path, ignore_errors=True)
    yield
    shutil.rmtree(path, ignore_errors=True)
