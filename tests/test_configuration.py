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


def test_connection_secret_is_external_and_revisions_invalidate_tests(tmp_path: Path) -> None:
    service, secrets, session = make_service(tmp_path)
    try:
        connection = service.create_connection(
            name="Volc",
            protocol="volc_bigasr_flash",
            base_url="https://openspeech.bytedance.com",
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

        updated = service.update_connection(connection.id, name="Volc 2", secret="replacement")
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
    session.close()
    session.bind.dispose()


def test_profiles_enforce_protocol_and_upload_consent_revision(tmp_path: Path) -> None:
    service, _, session = make_service(tmp_path)
    try:
        cloud = service.create_connection(
            name="Cloud ASR",
            protocol="volc_bigasr_flash",
            base_url="https://openspeech.bytedance.com",
            parameters={},
            secret="key",
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
            name="Flash",
            purpose="cloud_asr",
            connection_id=cloud.id,
            model="bigmodel",
            context_length=8192,
            options={"language": "zh-CN"},
        )
        service.record_profile_test(profile.id, ok=True, message="ready")
        authorized = service.authorize_cloud_upload(profile.id)
        assert authorized.upload_authorized is True

        changed = service.update_profile(profile.id, model="bigmodel-v2")
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
            name="Cloud", protocol="volc_bigasr_flash",
            base_url="https://openspeech.bytedance.com", parameters={}
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
