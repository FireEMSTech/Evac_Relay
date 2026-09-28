"""Load the pure-Python modules without importing Home Assistant."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "evac_relay"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"evac_relay_{name}", PKG / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def classifier() -> ModuleType:
    return _load("classifier")


@pytest.fixture(scope="session")
def twilio_sig() -> ModuleType:
    return _load("twilio_sig")
