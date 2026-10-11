"""ASGI application factory for container and process deployments."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from evalforge.api import create_app
from evalforge.auth import BearerAuthenticator, parse_access_credentials_json

DEFAULT_DATABASE_PATH = Path("/var/lib/evalforge/evalforge.db")


class _Unset:
    pass


@dataclass(frozen=True)
class _AccessConfiguration:
    supplied: bool
    valid: bool
    authenticator: BearerAuthenticator | None


_UNSET = _Unset()
_factory_lock = threading.Lock()
_configured_app: FastAPI | None = None
_configured_database_path: Path | None = None
_access_configuration_failed = False


def _consume_access_configuration() -> _AccessConfiguration:
    raw_access_credentials = os.environ.pop("EVALFORGE_ACCESS_CREDENTIALS", _UNSET)
    if isinstance(raw_access_credentials, _Unset):
        return _AccessConfiguration(supplied=False, valid=True, authenticator=None)
    try:
        access_credentials = parse_access_credentials_json(raw_access_credentials)
        if access_credentials is None:  # pragma: no cover - raw configuration was supplied
            return _AccessConfiguration(supplied=True, valid=False, authenticator=None)
        authenticator = BearerAuthenticator(access_credentials)
    except Exception:
        return _AccessConfiguration(supplied=True, valid=False, authenticator=None)
    return _AccessConfiguration(supplied=True, valid=True, authenticator=authenticator)


def create_app_from_environment() -> FastAPI:
    """Create exactly one immutable API configuration from process environment."""
    global _access_configuration_failed, _configured_app, _configured_database_path

    with _factory_lock:
        failed_before_call = _access_configuration_failed
        if _configured_app is None and "EVALFORGE_ACCESS_CREDENTIALS" in os.environ:
            _access_configuration_failed = True
        access_configuration = _consume_access_configuration()
        if failed_before_call:
            raise RuntimeError("access credential configuration is invalid")

        configured_path = os.environ.get("EVALFORGE_DATABASE_PATH")
        database_path = DEFAULT_DATABASE_PATH if configured_path is None else Path(configured_path)
        if _configured_app is not None:
            if access_configuration.supplied or database_path != _configured_database_path:
                raise RuntimeError("server configuration cannot change after initialization")
            return _configured_app

        empty_configuration = configured_path is not None and not configured_path
        if empty_configuration or not database_path.is_absolute():
            _access_configuration_failed = True
            raise RuntimeError("EVALFORGE_DATABASE_PATH must be a non-empty absolute path")

        if not access_configuration.valid:
            _access_configuration_failed = True
            raise RuntimeError("access credential configuration is invalid")
        try:
            configured_app = create_app(
                database_path,
                access_authenticator=access_configuration.authenticator,
            )
        except BaseException:
            _access_configuration_failed = True
            raise

        _configured_app = configured_app
        _configured_database_path = database_path
        _access_configuration_failed = False
        return configured_app
