from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from vtnote.configuration import ConfigurationService, InvalidConfiguration
from vtnote.database import initialize_database
from vtnote.models import ProviderConnectionRecord
from vtnote.secrets import MemorySecretStore


def make_service(tmp_path: Path) -> tuple[ConfigurationService, MemorySecretStore, Session]:
    engine = initialize_database(tmp_path / "vtnote.db")
    session = Session(engine)
    secrets = MemorySecretStore()
    return ConfigurationService(session, secrets), secrets, session


def test_tencent_connection_forces_canonical_api_and_guangzhou_region(
    tmp_path: Path,
) -> None:
    service, secrets, session = make_service(tmp_path)
    try:
        connection = service.create_connection(
            name="Tencent ASR",
            protocol="tencent_recording_asr",
            base_url="https://asr.tencentcloudapi.com/",
            parameters={},
            credentials={
                "secret_id": "AKID-example",
                "secret_key": "secret-key",
            },
        )

        assert connection.base_url == "https://asr.tencentcloudapi.com"
        assert connection.parameters == {
            "asr_region": "ap-guangzhou",
            "cos_configured": False,
        }
        assert connection.has_secret
        assert connection.configured_fields == {
            "secret_id": True,
            "secret_key": True,
        }
        assert "AKID-example" not in repr(connection)
        assert "secret-key" not in repr(connection)
        assert secrets.values_count == 1

        with pytest.raises(InvalidConfiguration):
            service.create_connection(
                name="Wrong endpoint",
                protocol="tencent_recording_asr",
                base_url="https://asr.ap-shanghai.tencentcloudapi.com",
                parameters={},
                credentials={
                    "secret_id": "AKID",
                    "secret_key": "key",
                },
            )
    finally:
        session.bind.dispose()
        session.close()


def test_new_legacy_volc_connections_are_rejected(tmp_path: Path) -> None:
    service, _, session = make_service(tmp_path)
    try:
        with pytest.raises(InvalidConfiguration, match="unsupported"):
            service.create_connection(
                name="Legacy Volc",
                protocol="volc_bigasr_flash",
                base_url="https://openspeech.bytedance.com",
                parameters={},
                secret="legacy-key",
            )
    finally:
        session.bind.dispose()
        session.close()


def test_tencent_credential_rotation_and_clear_are_atomic(tmp_path: Path) -> None:
    service, secrets, session = make_service(tmp_path)
    try:
        connection = service.create_connection(
            name="Tencent",
            protocol="tencent_recording_asr",
            base_url="https://asr.tencentcloudapi.com",
            parameters={},
            credentials={"secret_id": "AKID-1", "secret_key": "key-1"},
        )
        service.record_connection_test(connection.id, ok=True, message="ready")

        with pytest.raises(InvalidConfiguration):
            service.update_connection(
                connection.id,
                credentials={"secret_id": "AKID-partial"},
            )
        unchanged = service.get_connection(connection.id)
        assert unchanged.revision == 1
        assert unchanged.tested is True
        assert secrets.values_count == 1

        rotated = service.update_connection(
            connection.id,
            credentials={"secret_id": "AKID-2", "secret_key": "key-2"},
        )
        assert rotated.revision == 2
        assert rotated.tested is False
        assert rotated.configured_fields == {
            "secret_id": True,
            "secret_key": True,
        }
        assert secrets.values_count == 1

        cleared = service.update_connection(connection.id, clear_secret=True)
        assert cleared.revision == 3
        assert cleared.has_secret is False
        assert cleared.configured_fields == {
            "secret_id": False,
            "secret_key": False,
        }
    finally:
        session.bind.dispose()
        session.close()


@pytest.mark.parametrize(
    "parameters",
    [
        {
            "cos_bucket": "bucket-1250000000",
            "cos_region": "ap-shanghai",
            "cos_prefix": "vtnote-runtime",
            "cos_private": True,
        },
        {
            "cos_bucket": "INVALID_BUCKET",
            "cos_region": "ap-guangzhou",
            "cos_prefix": "vtnote-runtime",
            "cos_private": True,
        },
        {
            "cos_bucket": "bucket-1250000000",
            "cos_region": "ap-guangzhou",
            "cos_prefix": "../escape",
            "cos_private": True,
        },
        {
            "cos_bucket": "bucket-1250000000",
            "cos_region": "ap-guangzhou",
            "cos_prefix": "vtnote-runtime",
            "cos_private": False,
        },
    ],
)
def test_cos_config_rejects_public_non_guangzhou_or_malformed_bucket_and_prefix(
    tmp_path: Path,
    parameters: dict[str, object],
) -> None:
    service, _, session = make_service(tmp_path)
    try:
        with pytest.raises(InvalidConfiguration):
            service.create_connection(
                name="Invalid COS",
                protocol="tencent_recording_asr",
                base_url="https://asr.tencentcloudapi.com",
                parameters=parameters,
                credentials={"secret_id": "AKID", "secret_key": "key"},
            )
    finally:
        session.bind.dispose()
        session.close()


def test_tencent_profile_forces_large_model_2_and_subtitle_format(
    tmp_path: Path,
) -> None:
    service, _, session = make_service(tmp_path)
    try:
        connection = service.create_connection(
            name="Tencent ASR",
            protocol="tencent_recording_asr",
            base_url="https://asr.tencentcloudapi.com",
            parameters={},
            credentials={"secret_id": "AKID", "secret_key": "key"},
        )
        profile = service.create_profile(
            name="Large model 2",
            purpose="cloud_asr",
            connection_id=connection.id,
            model="16k_zh_en_2.0",
            options={"language_scope": "zh_en_dialects"},
        )

        assert profile.model == "16k_zh_en_2.0"
        assert profile.options == {
            "language_scope": "zh_en_dialects",
            "res_text_format": 3,
            "sentence_max_length": 20,
        }
        with pytest.raises(InvalidConfiguration):
            service.update_profile(profile.id, model="16k_zh")
        with pytest.raises(InvalidConfiguration):
            service.update_profile(
                profile.id,
                options={"language_scope": "auto"},
            )
    finally:
        session.bind.dispose()
        session.close()


def test_connection_secret_is_external_and_revisions_invalidate_tests(tmp_path: Path) -> None:
    service, secrets, session = make_service(tmp_path)
    try:
        connection = service.create_connection(
            name="Chat",
            protocol="openai_compatible",
            base_url="https://api.example.com/v1",
            parameters={},
            secret="top-secret",
        )
        assert connection.has_secret is True
        assert "secret" not in connection.model_dump()
        assert "credential_ref" not in connection.model_dump()
        assert secrets.values_count == 1

        service.record_connection_test(connection.id, ok=True, message="credential accepted")
        tested = service.get_connection(connection.id)
        assert tested.tested is True
        assert tested.test_message == "credential accepted"

        updated = service.update_connection(connection.id, name="Chat 2", secret="replacement")
        assert updated.revision == 2
        assert updated.tested is False
        assert updated.has_secret is True
        assert secrets.values_count == 1

        cleared = service.update_connection(connection.id, clear_secret=True)
        assert cleared.revision == 3
        assert cleared.has_secret is False
    finally:
        session.bind.dispose()
        session.close()


def test_secret_change_rolls_back_when_database_commit_fails(tmp_path: Path) -> None:
    service, secrets, session = make_service(tmp_path)
    connection = service.create_connection(
        name="Chat",
        protocol="openai_compatible",
        base_url="https://api.example.com/v1",
        parameters={},
        secret="old-secret",
    )
    stored = session.get(ProviderConnectionRecord, connection.id)
    assert stored is not None
    credential_ref = stored.credential_ref

    def fail_commit(_session: Session) -> None:
        raise RuntimeError("database unavailable")

    event.listen(session, "before_commit", fail_commit, once=True)
    with pytest.raises(RuntimeError, match="database unavailable"):
        service.update_connection(connection.id, secret="new-secret")

    assert secrets.get(credential_ref) == "old-secret"
    session.refresh(stored)
    assert stored.credential_ref == credential_ref
    assert secrets.values_count == 1
    session.close()
    session.bind.dispose()


def test_profiles_enforce_protocol_and_upload_consent_revision(tmp_path: Path) -> None:
    service, _, session = make_service(tmp_path)
    try:
        cloud = service.create_connection(
            name="Cloud ASR",
            protocol="tencent_recording_asr",
            base_url="https://asr.tencentcloudapi.com",
            parameters={},
            credentials={"secret_id": "AKID", "secret_key": "key"},
        )
        chat = service.create_connection(
            name="Chat",
            protocol="openai_compatible",
            base_url="http://127.0.0.1:11434/v1",
            parameters={},
        )

        with pytest.raises(InvalidConfiguration, match="incompatible"):
            service.create_profile(
                name="Wrong",
                purpose="notes",
                connection_id=cloud.id,
                model="gpt",
            )

        profile = service.create_profile(
            name="Tencent",
            purpose="cloud_asr",
            connection_id=cloud.id,
            model="16k_zh_en_2.0",
            context_length=8192,
            options={"language_scope": "zh_en_dialects"},
        )
        service.record_profile_test(profile.id, ok=True, message="ready")
        authorized = service.authorize_cloud_upload(profile.id)
        assert authorized.upload_authorized is True

        changed = service.update_profile(profile.id, context_length=16384)
        assert changed.revision == 2
        assert changed.tested is False
        assert changed.upload_authorized is False

        notes = service.create_profile(
            name="Notes",
            purpose="notes",
            connection_id=chat.id,
            model="qwen",
        )
        assert notes.purpose == "notes"
    finally:
        session.bind.dispose()
        session.close()


def test_defaults_are_safe_and_notes_require_a_current_tested_profile(tmp_path: Path) -> None:
    service, _, session = make_service(tmp_path)
    try:
        defaults = service.get_defaults()
        assert defaults.asr_mode == "auto"
        assert defaults.translation_enabled is False
        assert defaults.notes_enabled is False

        connection = service.create_connection(
            name="Chat",
            protocol="openai_compatible",
            base_url="https://api.example.com/v1",
            parameters={},
            secret="key",
        )
        notes = service.create_profile(
            name="Notes", purpose="notes", connection_id=connection.id, model="gpt"
        )
        with pytest.raises(InvalidConfiguration, match="tested notes profile"):
            service.update_defaults(notes_enabled=True, notes_profile_id=notes.id)

        service.record_profile_test(notes.id, ok=True, message="ok")
        enabled = service.update_defaults(notes_enabled=True, notes_profile_id=notes.id)
        assert enabled.notes_enabled is True

        service.update_connection(connection.id, base_url="https://new.example.com/v1")
        safe = service.get_defaults()
        assert safe.notes_enabled is False
    finally:
        session.bind.dispose()
        session.close()


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.example.com/v1",
        "https://user:pass@api.example.com/v1",
        "https://api.example.com/v1#fragment",
        "https://[::1",
    ],
)
def test_provider_base_url_policy_is_distinct_and_strict(tmp_path: Path, base_url: str) -> None:
    service, _, session = make_service(tmp_path)
    try:
        with pytest.raises(InvalidConfiguration, match="base URL"):
            service.create_connection(
                name="Bad", protocol="openai_compatible", base_url=base_url, parameters={}
            )
    finally:
        session.bind.dispose()
        session.close()


def test_connection_parameters_are_protocol_whitelisted(tmp_path: Path) -> None:
    service, _, session = make_service(tmp_path)
    try:
        with pytest.raises(InvalidConfiguration, match="parameter"):
            service.create_connection(
                name="Bad",
                protocol="openai_compatible",
                base_url="https://api.example.com/v1",
                parameters={"api_key": "must-not-be-stored"},
            )
    finally:
        session.bind.dispose()
        session.close()


def test_all_loopback_http_provider_addresses_are_allowed(tmp_path: Path) -> None:
    service, _, session = make_service(tmp_path)
    try:
        connection = service.create_connection(
            name="Local",
            protocol="openai_compatible",
            base_url="http://127.0.0.2:11434/v1",
            parameters={},
        )
        assert connection.base_url == "http://127.0.0.2:11434/v1"
    finally:
        session.bind.dispose()
        session.close()


class FailingSecretStore(MemorySecretStore):
    def set(self, reference: str, value: str) -> None:
        if value == "fail":
            raise RuntimeError("credential manager unavailable")
        super().set(reference, value)


def test_secret_store_failure_rolls_back_database_mutation(tmp_path: Path) -> None:
    engine = initialize_database(tmp_path / "vtnote.db")
    session = Session(engine)
    secrets = FailingSecretStore()
    service = ConfigurationService(session, secrets)
    try:
        existing = service.create_connection(
            name="Original",
            protocol="openai_compatible",
            base_url="https://api.example.com/v1",
            parameters={},
            secret="old",
        )

        with pytest.raises(RuntimeError, match="credential manager"):
            service.update_connection(existing.id, name="Changed", secret="fail")

        loaded = service.get_connection(existing.id)
        assert loaded.name == "Original"
        assert loaded.revision == 1
        assert loaded.has_secret is True

        with pytest.raises(RuntimeError, match="credential manager"):
            service.create_connection(
                name="Never committed",
                protocol="openai_compatible",
                base_url="https://api.example.com/v1",
                parameters={},
                secret="fail",
            )
        assert [item.name for item in service.list_connections()] == ["Original"]
    finally:
        session.close()
        engine.dispose()


def test_cloud_defaults_require_current_authorized_profile(tmp_path: Path) -> None:
    service, _, session = make_service(tmp_path)
    try:
        with pytest.raises(InvalidConfiguration, match="cloud ASR profile"):
            service.update_defaults(asr_mode="cloud")
    finally:
        session.bind.dispose()
        session.close()


def test_profile_can_move_only_to_a_compatible_connection(tmp_path: Path) -> None:
    service, _, session = make_service(tmp_path)
    try:
        first = service.create_connection(
            name="First", protocol="openai_compatible",
            base_url="https://first.example.com/v1", parameters={}
        )
        second = service.create_connection(
            name="Second", protocol="openai_compatible",
            base_url="https://second.example.com/v1", parameters={}
        )
        cloud = service.create_connection(
            name="Cloud", protocol="tencent_recording_asr",
            base_url="https://asr.tencentcloudapi.com", parameters={}
        )
        profile = service.create_profile(
            name="Notes", purpose="notes", connection_id=first.id, model="gpt"
        )

        moved = service.update_profile(profile.id, connection_id=second.id)
        assert moved.connection_id == second.id
        assert moved.revision == 2

        with pytest.raises(InvalidConfiguration, match="incompatible"):
            service.update_profile(profile.id, connection_id=cloud.id)
    finally:
        session.bind.dispose()
        session.close()


def test_connectivity_message_scrubs_known_secret_even_without_a_label(tmp_path: Path) -> None:
    service, _, session = make_service(tmp_path)
    try:
        connection = service.create_connection(
            name="Chat", protocol="openai_compatible",
            base_url="https://api.example.com/v1", parameters={}, secret="unique-secret-value"
        )
        tested = service.record_connection_test(
            connection.id, ok=True, message="accepted unique-secret-value"
        )
        assert tested.test_message == "accepted [redacted]"
    finally:
        session.bind.dispose()
        session.close()


def test_connectivity_message_scrubs_structured_and_bearer_credentials(tmp_path: Path) -> None:
    service, _, session = make_service(tmp_path)
    try:
        connection = service.create_connection(
            name="Chat", protocol="openai_compatible",
            base_url="https://api.example.com/v1", parameters={}, secret="known-key"
        )
        tested = service.record_connection_test(
            connection.id,
            ok=False,
            message=(
                '{"access_token":"issued-token-value","api_key":"other-key"} '
                "Authorization: Bearer bearer-value known-key"
            ),
        )
        assert tested.test_message is not None
        assert "issued-token-value" not in tested.test_message
        assert "other-key" not in tested.test_message
        assert "bearer-value" not in tested.test_message
        assert "known-key" not in tested.test_message
        stored = session.get(ProviderConnectionRecord, connection.id)
        assert stored is not None
        assert "issued-token-value" not in (stored.test_message or "")
    finally:
        session.bind.dispose()
        session.close()


def test_nested_or_secret_shaped_whitelisted_options_are_rejected(tmp_path: Path) -> None:
    service, _, session = make_service(tmp_path)
    try:
        connection = service.create_connection(
            name="Chat", protocol="openai_compatible",
            base_url="https://api.example.com/v1", parameters={}
        )
        with pytest.raises(InvalidConfiguration, match="profile option"):
            service.create_profile(
                name="Notes", purpose="notes", connection_id=connection.id,
                model="gpt", options={"temperature": {"secret": "leak"}}
            )
        with pytest.raises(InvalidConfiguration, match="local Whisper"):
            service.update_defaults(local_whisper_options={"api_key": "leak"})
    finally:
        session.bind.dispose()
        session.close()
