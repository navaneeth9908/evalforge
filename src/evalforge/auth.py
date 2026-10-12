"""Fail-closed bearer authentication primitives for the HTTP control plane."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

AccessRole = Literal["reader", "editor", "admin"]
TRUSTED_LOCAL_TENANT_ID = "__local__"
_MAX_ACCESS_CONFIGURATION_BYTES = 32_768
_MAX_ACCESS_CREDENTIALS = 100
_MAX_JSON_NESTING = 16


class AccessCredential(BaseModel):
    """Operator-supplied bearer credential bound to one tenant and role."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    token: SecretStr = Field(min_length=32, max_length=512)
    tenant_id: str = Field(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
    role: AccessRole

    @field_validator("token")
    @classmethod
    def token_is_visible_ascii(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        if any(not 0x21 <= ord(character) <= 0x7E for character in token):
            raise ValueError("access token must contain only visible ASCII characters")
        return value


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Redaction-safe identity returned after credential verification."""

    tenant_id: str
    role: AccessRole


class AuthenticationError(ValueError):
    """A request did not contain a recognized bearer credential."""


@dataclass(frozen=True)
class _CredentialDigest:
    digest: bytes
    principal: AuthenticatedPrincipal


class BearerAuthenticator:
    """Authenticate exact bearer tokens while retaining only SHA-256 digests."""

    def __init__(self, credentials: tuple[AccessCredential, ...]) -> None:
        if not credentials:
            raise ValueError("at least one access credential is required")
        entries = tuple(
            _CredentialDigest(
                digest=_token_digest(credential.token.get_secret_value()),
                principal=AuthenticatedPrincipal(
                    tenant_id=credential.tenant_id,
                    role=credential.role,
                ),
            )
            for credential in credentials
        )
        if len({entry.digest for entry in entries}) != len(entries):
            raise ValueError("access credential tokens must be unique")
        self._entries = entries

    def authenticate(self, authorization: str | None) -> AuthenticatedPrincipal:
        """Return a tenant principal for an exact bearer token."""

        if authorization is None:
            raise AuthenticationError("invalid bearer credential")
        scheme, separator, token = authorization.partition(" ")
        del authorization
        valid_header = (
            separator == " "
            and scheme.casefold() == "bearer"
            and bool(token)
            and token == token.strip()
            and " " not in token
        )
        del scheme
        if not valid_header:
            del token
            raise AuthenticationError("invalid bearer credential")
        supplied_digest = _token_digest(token)
        del token
        matched: AuthenticatedPrincipal | None = None
        for entry in self._entries:
            if hmac.compare_digest(supplied_digest, entry.digest):
                matched = entry.principal
        if matched is None:
            raise AuthenticationError("invalid bearer credential")
        return matched


def parse_access_credentials_json(raw: str | None) -> tuple[AccessCredential, ...] | None:
    """Parse bounded environment JSON, with ``None`` selecting trusted local mode."""

    if raw is None:
        return None
    try:
        credentials = _parse_access_credentials_json(raw)
    except (
        UnicodeEncodeError,
        json.JSONDecodeError,
        RecursionError,
        TypeError,
        ValueError,
        ValidationError,
    ):
        credentials = None
    del raw
    if credentials is None:
        raise ValueError("access credential configuration is invalid")
    return credentials


def _parse_access_credentials_json(raw: str) -> tuple[AccessCredential, ...]:
    if not raw or len(raw.encode("utf-8")) > _MAX_ACCESS_CONFIGURATION_BYTES:
        raise ValueError
    _validate_json_nesting(raw)
    decoded = json.loads(
        raw,
        object_pairs_hook=_unique_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(decoded, list) or not 1 <= len(decoded) <= _MAX_ACCESS_CREDENTIALS:
        raise ValueError
    credentials = tuple(AccessCredential.model_validate(item) for item in decoded)
    BearerAuthenticator(credentials)
    return credentials


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise ValueError("non-finite JSON number")


def _validate_json_nesting(raw: str) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in raw:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > _MAX_JSON_NESTING:
                raise ValueError("JSON nesting exceeds limit")
        elif character in "]}":
            depth -= 1


def _token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()
