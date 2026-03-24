"""Pytest configuration for Textual async tests.

Each test that calls asyncio.run() creates and destroys an event loop.
Textual's cleanup can race with loop teardown, causing 'Event loop is closed'
in subsequent tests. This fixture ensures each test gets a clean loop policy.
"""

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _reset_event_loop_policy():
    """Reset the asyncio event loop policy before and after each test."""
    asyncio.set_event_loop_policy(None)
    yield
    # Force a fresh policy so a closed loop from one test
    # does not leak into the next test.
    asyncio.set_event_loop_policy(None)
    # Suppress any lingering 'Event loop is closed' from Textual threads
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            asyncio.set_event_loop(asyncio.new_event_loop())
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
