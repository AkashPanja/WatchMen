"""Shared fixtures for Watchmen tests."""

from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: Generator[None, None, None],
) -> Generator[None, None, None]:
    """Enable loading custom integrations in tests."""
    yield


@pytest.fixture
def hass_config_dir(hass_tmp_config_dir: str) -> str:
    """Per-test temporary config dir.

    Keeps Watchmen's on-disk state (enrolled faces, captures) isolated so
    tests do not leak photos into each other via the shared test config.
    """
    return hass_tmp_config_dir
