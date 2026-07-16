"""Pytest config for script tests.

Adds the `scripts/` directory to sys.path so tests can import helpers
that live outside the main package.
"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))