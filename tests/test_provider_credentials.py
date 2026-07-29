from __future__ import annotations

import json

import pytest
from pydantic import SecretStr, ValidationError

from vtnote.provider_credentials import (
    CredentialReentryRequired,
    TencentCredentialBundle,
    configured_credential_fields,
    parse_credential_bundle,
    serialize_credential_bundle,
)


def test_tencent_bundle_accepts_exact_atomic_pair_and_redacts_repr() -> None:
    bundle = TencentCredentialBundle(
        secret_id=SecretStr("AKID-example"),
        secret_key=SecretStr("secret-key"),
    )

    assert bundle.schema_version == 1
    assert "AKID-example" not in repr(bundle)
    assert "secret-key" not in repr(bundle)


@pytest.mark.parametrize(
    "extra",
    [
        {"token": "temporary"},
        {"session_token": "temporary"},
        {"security_token": "temporary"},
        {"expired_time": 123},
    ],
)
def test_tencent_bundle_rejects_sts_and_unknown_fields(
    extra: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TencentCredentialBundle(
            secret_id=SecretStr("AKID-example"),
            secret_key=SecretStr("secret-key"),
            **extra,
        )


def test_bundle_serialization_is_versioned_and_round_trips_without_partial_fields() -> None:
    stored = serialize_credential_bundle(
        "tencent_recording_asr",
        {"secret_id": "AKID-example", "secret_key": "secret-key"},
    )

    assert json.loads(stored) == {
        "schema_version": 1,
        "secret_id": "AKID-example",
        "secret_key": "secret-key",
    }
    parsed = parse_credential_bundle("tencent_recording_asr", stored)
    assert parsed.secret_id.get_secret_value() == "AKID-example"
    assert parsed.secret_key.get_secret_value() == "secret-key"
    assert configured_credential_fields(
        "tencent_recording_asr",
        stored,
    ) == {"secret_id": True, "secret_key": True}


@pytest.mark.parametrize(
    "stored",
    [
        "legacy-plaintext-api-key",
        '{"schema_version":1,"secret_id":"only-one"}',
        '{"schema_version":2,"secret_id":"a","secret_key":"b"}',
        '{"schema_version":1,"secret_id":"a","secret_key":"b","token":"c"}',
        "not-json",
    ],
)
def test_malformed_or_legacy_tencent_entry_requires_full_reentry(
    stored: str,
) -> None:
    with pytest.raises(CredentialReentryRequired):
        parse_credential_bundle("tencent_recording_asr", stored)
    assert configured_credential_fields(
        "tencent_recording_asr",
        stored,
    ) == {"secret_id": False, "secret_key": False}


def test_bailian_single_api_key_uses_the_same_versioned_bundle_boundary() -> None:
    stored = serialize_credential_bundle(
        "aliyun_bailian",
        {"api_key": "sk-domestic"},
    )

    parsed = parse_credential_bundle("aliyun_bailian", stored)
    assert parsed.api_key.get_secret_value() == "sk-domestic"
    assert configured_credential_fields("aliyun_bailian", stored) == {
        "api_key": True
    }
