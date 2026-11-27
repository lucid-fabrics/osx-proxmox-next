"""The conftest fixture puts the SIGTERM handler back after every test (driver_cli installs one that raises)."""
from __future__ import annotations

import signal

AT_IMPORT = signal.getsignal(signal.SIGTERM)    # what pytest itself runs with, before any test body


def test_a_test_may_install_a_sigterm_handler():
    signal.signal(signal.SIGTERM, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert signal.getsignal(signal.SIGTERM) is not AT_IMPORT


def test_the_next_test_sees_the_original_sigterm_handler():
    assert signal.getsignal(signal.SIGTERM) is AT_IMPORT
