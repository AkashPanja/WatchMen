"""Shared fixtures for Watchmen tests."""

from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: Generator[None, None, None],
) -> Generator[None, None, None]:
    """Enable loading custom integrations in tests."""
    yield
