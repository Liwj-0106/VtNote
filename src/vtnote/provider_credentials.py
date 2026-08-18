"""Versioned, provider-specific atomic credential bundle schemas."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    SecretStr,
    ValidationError,
    field_validator,
)


class CredentialReentryRequired(ValueError):
    """Stored credentials cannot be safely interpreted as a complete bundle."""


class _CredentialBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("*")
    @classmethod
    def nonempty_secret(cls, value: object) -> object:
        if isinstance(value, SecretStr) and not value.get_secret_value().strip():
            raise ValueError("credential field cannot be empty")
        return value


class TencentCredentialBundle(_CredentialBundle):
    schema_version: Literal[1] = 1
    secret_id: SecretStr
    secret_key: SecretStr


class BailianCredentialBundle(_CredentialBundle):
    schema_version: Literal[1] = 1
    api_key: SecretStr


class TokenHubCredentialBundle(_CredentialBundle):
    schema_version: Literal[1] = 1
    api_key: SecretStr


CredentialBundle = (
    TencentCredentialBundle | BailianCredentialBundle | TokenHubCredentialBundle
)

_SCHEMAS: dict[str, type[_CredentialBundle]] = {
    "tencent_recording_asr": TencentCredentialBundle,
    "aliyun_bailian": BailianCredentialBundle,
    "tencent_tokenhub": TokenHubCredentialBundle,
}
_FIELDS = {
    "tencent_recording_asr": ("secret_id", "secret_key"),
    "aliyun_bailian": ("api_key",),
    "tencent_tokenhub": ("api_key",),
}


def _schema(protocol: str) -> type[_CredentialBundle]:
    try:
        return _SCHEMAS[protocol]
    except KeyError:
        raise ValueError("unsupported credential protocol") from None


def serialize_credential_bundle(
    protocol: str,
    fields: dict[str, object],
) -> str:
    schema = _schema(protocol)
    try:
        bundle = schema.model_validate({"schema_version": 1, **fields})
    except ValidationError as error:
        raise ValueError("invalid credential bundle") from error
    payload = {"schema_version": 1}
    for name in _FIELDS[protocol]:
        value = getattr(bundle, name)
        assert isinstance(value, SecretStr)
        payload[name] = value.get_secret_value()
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def parse_credential_bundle(
    protocol: str,
    stored: str,
) -> CredentialBundle:
    schema = _schema(protocol)
    try:
        payload = json.loads(stored)
        return schema.model_validate(payload)  # type: ignore[return-value]
    except (TypeError, ValueError, ValidationError, json.JSONDecodeError):
        raise CredentialReentryRequired(
            "stored credential bundle requires re-entry"
        ) from None


def configured_credential_fields(
    protocol: str,
    stored: str | None,
) -> dict[str, bool]:
    fields = _FIELDS.get(protocol)
    if fields is None:
        raise ValueError("unsupported credential protocol")
    if stored is None:
        return {name: False for name in fields}
    try:
        parse_credential_bundle(protocol, stored)
    except CredentialReentryRequired:
        return {name: False for name in fields}
    return {name: True for name in fields}
