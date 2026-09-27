"""ASGI application factory for container and process deployments."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from evalforge.api import create_app

DEFAULT_DATABASE_PATH = Path("/var/lib/evalforge/evalforge.db")


def create_app_from_environment() -> FastAPI:
    """Create the API using the operator-configured persistent database path."""
    configured_path = os.environ.get("EVALFORGE_DATABASE_PATH")
    database_path = DEFAULT_DATABASE_PATH if configured_path is None else Path(configured_path)
    empty_configuration = configured_path is not None and not configured_path
    if empty_configuration or not database_path.is_absolute():
        raise RuntimeError("EVALFORGE_DATABASE_PATH must be a non-empty absolute path")
    return create_app(database_path)
