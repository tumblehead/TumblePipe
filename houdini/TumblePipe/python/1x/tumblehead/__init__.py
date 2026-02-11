"""Tumblehead pipeline package."""

import os

# Initialize file-based logging on package import (skip in CI environments)
# CI environments are detected by WOODPECKER or CI environment variables
_is_ci = os.environ.get('CI') == 'true' or os.environ.get('WOODPECKER') == 'true'

if not _is_ci:
    from tumblehead.util.logging import setup_logging as _setup_logging
    _setup_logging()
