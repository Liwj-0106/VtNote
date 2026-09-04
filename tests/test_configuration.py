from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from vtnote.config import Settings
from vtnote.configuration import ConfigurationService, InvalidConfiguration
from vtnote.database import initialize_database
from vtnote.models import (
    DefaultSettingsRecord,
    ProcessorProfileRecord,
    ProviderConnectionRecord,
)
from vtnote.paths import StoragePaths
from vtnote.secrets import MemorySecretStore


def make_service(tmp_path: Path) -> tuple[ConfigurationService, MemorySecretStore, Session]:
    engine = initialize_database(tmp_path / "vtnote.db")
    session = Session(engine)
    secrets = MemorySecretStore()
    return ConfigurationService(session, secrets), secrets, session


def create_bailian_connection(
    service: ConfigurationService,
    *,
    name: str = "Chat",
    workspace_id: str = "ws-1234",
    api_key: str | None = None,
):
    kwargs = {}
    if api_key is not None:
        kwargs["credentials"] = {"api_key": api_key}
    return service.create_connection(
        name=name,
        protocol="aliyun_bailian",
        base_url=(
            f"https://{workspace_id}.cn-beijing.maas.aliyuncs.com/"
            "compatible-mode/v1"
        ),
        parameters={"workspace_id": workspace_id},
        **kwargs,
    )


def test_configuration_defaults_follow_platform_storage_roots(tmp_path: Path) -> None:
    service, _, session = make_service(tmp_path)
    try:
        defaults = service.get_defaults()
        settings = Settings()

        assert Path(defaults.local_whisper_options["model_root"]) == (
            settings.data_root / "models" / "faster-whisper"
        )
        assert Path(defaults.local_whisper_options["cache_root"]) == (
            settings.runtime_cache_root / "models" / "faster-whisper"
        )
    finally:
        session.bind.dispose()
        session.close()


def test_configuration_can_keep_large_model_assets_outside_primary_storage(
    tmp_path: Path,
) -> None:
    engine = initialize_database(tmp_path / "vtnote.db")
    session = Session(engine)
    settings = Settings(
        data_root=tmp_path / "primary-data",
        runtime_cache_root=tmp_path / "primary-cache",
        managed_assets_root=tmp_path / "managed-assets",
    )
    service = ConfigurationService(
        session,
        MemorySecretStore(),
        paths=StoragePaths.from_settings(settings),
        model_paths=StoragePaths.managed_assets_from_settings(settings),
    )
    try:
        defaults = service.get_defaults()
        assert Path(defaults.local_whisper_options["model_root"]) == (
            settings.managed_assets_root
            / "Data"
            / "models"
            / "faster-whisper"
        )
        assert Path(defaults.local_whisper_options["cache_root"]) == (
            settings.managed_assets_root
            / "Cache"
            / "models"
            / "faster-whisper"
        )
    finally:
        engine.dispose()
        session.close()


def test_configuration_repairs_stale_local_model_roots(tmp_path: Path) -> None:
    engine = initialize_database(tmp_path / "vtnote.db")
    session = Session(engine)
    settings = Settings(
        data_root=tmp_path / "primary-data",
        runtime_cache_root=tmp_path / "primary-cache",
        managed_assets_root=tmp_path / "managed-assets",
    )
    service = ConfigurationService(
        session,
        MemorySecretStore(),
        paths=StoragePaths.from_settings(settings),
        model_paths=StoragePaths.managed_assets_from_settings(settings),
    )
    try:
        service.get_defaults()
        row = session.get(DefaultSettingsRecord, 1)
        assert row is not None
        stale = dict(row.local_whisper_options)
        stale["model_root"] = str(tmp_path / "old-model")
        stale["cache_root"] = str(tmp_path / "old-cache")
        row.local_whisper_options = stale
        session.commit()

        repaired = service.get_defaults().local_whisper_options

        assert Path(repaired["model_root"]) == (
            settings.managed_assets_root / "Data" / "models" / "faster-whisper"
        )
        assert Path(repaired["cache_root"]) == (
            settings.managed_assets_root / "Cache" / "models" / "faster-whisper"
        )
    finally:
        engine.dispose()
        session.close()


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


def test_tencent_profile_forces_free_tier_model_and_subtitle_format(
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
            name="Free tier",
            purpose="cloud_asr",
            connection_id=connection.id,
            model="16k_zh",
            options={"language_scope": "zh_with_limited_english"},
        )

        assert profile.model == "16k_zh"
        assert profile.options == {
            "language_scope": "zh_with_limited_english",
            "res_text_format": 3,
            "sentence_max_length": 20,
        }
        with pytest.raises(InvalidConfiguration):
            service.update_profile(profile.id, model="16k_zh_en_2.0")
        with pytest.raises(InvalidConfiguration):
            service.update_profile(
                profile.id,
                options={"language_scope": "auto"},
            )
    finally:
        session.bind.dispose()
        session.close()


def test_bailian_connection_is_workspace_derived_and_uses_atomic_api_key(
    tmp_path: Path,
) -> None:
    service, secrets, session = make_service(tmp_path)
    try:
        connection = service.create_connection(
            name="Bailian Beijing",
            protocol="aliyun_bailian",
            base_url=None,
            parameters={"workspace_id": "ws-1234"},
            credentials={"api_key": "sk-private"},
        )

        assert connection.base_url == (
            "https://ws-1234.cn-beijing.maas.aliyuncs.com/"
            "compatible-mode/v1"
        )
        assert connection.parameters == {"workspace_id": "ws-1234"}
        assert connection.configured_fields == {"api_key": True}
        assert connection.has_secret is True
        assert "sk-private" not in repr(connection)
        assert secrets.values_count == 1

        for base_url in (
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "https://relay.example/v1",
            "https://ws-1234.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
            "https://user@ws-1234.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            "https://ws-1234.cn-beijing.maas.aliyuncs.com:443/compatible-mode/v1",
            "https://ws-1234.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/",
        ):
            with pytest.raises(InvalidConfiguration):
                service.create_connection(
                    name=f"Bad {len(base_url)}",
                    protocol="aliyun_bailian",
                    base_url=base_url,
                    parameters={"workspace_id": "ws-1234"},
                    credentials={"api_key": "key"},
                )
    finally:
        session.bind.dispose()
        session.close()


def test_bailian_profiles_have_fixed_production_contract_and_chat_consent(
    tmp_path: Path,
) -> None:
    service, _, session = make_service(tmp_path)
    try:
        connection = service.create_connection(
            name="Bailian",
            protocol="aliyun_bailian",
            base_url=(
                "https://ws-1234.cn-beijing.maas.aliyuncs.com/"
                "compatible-mode/v1"
            ),
            parameters={"workspace_id": "ws-1234"},
            credentials={"api_key": "key"},
        )
        with pytest.raises(InvalidConfiguration, match="context"):
            service.create_profile(
                name="Too small",
                purpose="translation",
                connection_id=connection.id,
                model="qwen-plus",
                context_length=8192,
                options={"max_tokens": 4096},
            )
        with pytest.raises(InvalidConfiguration, match="max_tokens"):
            service.create_profile(
                name="Too little output",
                purpose="notes",
                connection_id=connection.id,
                model="qwen-plus",
                context_length=32768,
                options={"max_tokens": 128},
            )

        profile = service.create_profile(
            name="Notes",
            purpose="notes",
            connection_id=connection.id,
            model="qwen-plus",
            context_length=32768,
            options={
                "temperature": 0.2,
                "max_tokens": 4096,
                "enable_thinking": False,
            },
        )
        assert profile.options["enable_thinking"] is False
        assert profile.capability_fingerprint is None
        assert profile.chat_data_authorized is False

        tested = service.record_profile_test(profile.id, ok=True, message="ready")
        assert tested.capability_fingerprint == {
            "schema_version": 1,
            "protocol": "aliyun_bailian",
            "endpoint": (
                "https://ws-1234.cn-beijing.maas.aliyuncs.com/"
                "compatible-mode/v1/chat/completions"
            ),
            "connection_revision": 1,
            "profile_revision": 1,
            "model": "qwen-plus",
            "response_format": "json_object",
            "enable_thinking": False,
            "options": {
                "temperature": 0.2,
                "max_tokens": 4096,
            },
        }
        assert tested.chat_data_authorized is False

        authorized = service.authorize_chat_data(profile.id)
        assert authorized.chat_data_authorized is True
        assert service.revoke_chat_data(profile.id).chat_data_authorized is False

        service.authorize_chat_data(profile.id)
        changed = service.update_profile(
            profile.id,
            options={
                "temperature": 0.3,
                "max_tokens": 4096,
                "enable_thinking": False,
            },
        )
        assert changed.tested is False
        assert changed.capability_fingerprint is None
        assert changed.chat_data_authorized is False
    finally:
        session.bind.dispose()
        session.close()


def test_new_legacy_arbitrary_chat_connections_are_rejected(tmp_path: Path) -> None:
    service, _, session = make_service(tmp_path)
    try:
        with pytest.raises(InvalidConfiguration, match="unsupported"):
            service.create_connection(
                name="Legacy chat",
                protocol="openai_compatible",
                base_url="https://api.example.com/v1",
                parameters={},
                secret="key",
            )
    finally:
        session.bind.dispose()
        session.close()


def test_legacy_chat_execution_is_blocked_before_secret_lookup(tmp_path: Path) -> None:
    class NoReadSecretStore(MemorySecretStore):
        def __init__(self) -> None:
            super().__init__()
            self.reads = 0

        def get(self, reference: str) -> str | None:
            self.reads += 1
            raise AssertionError("legacy credential must not be read")

    engine = initialize_database(tmp_path / "legacy-execution.db")
    session = Session(engine)
    connection = ProviderConnectionRecord(
        name="Legacy",
        protocol="openai_compatible",
        base_url="https://relay.example/v1",
        parameters={},
        credential_ref="connection:legacy",
    )
    profile = ProcessorProfileRecord(
        name="Legacy notes",
        purpose="notes",
        connection=connection,
        model="old",
        context_length=32768,
        options={"temperature": 0.2, "max_tokens": 4096},
    )
    session.add(profile)
    session.commit()
    store = NoReadSecretStore()
    service = ConfigurationService(session, store)
    try:
        with pytest.raises(InvalidConfiguration, match="legacy_chat_endpoint_blocked"):
            service.snapshot_profile(profile.id, include_archived=True)
        with pytest.raises(InvalidConfiguration, match="legacy_chat_endpoint_blocked"):
            service.credential_bundle_for_connection(connection.id)
        assert store.reads == 0
    finally:
        session.close()
        engine.dispose()


def test_connection_secret_is_external_and_revisions_invalidate_tests(tmp_path: Path) -> None:
    service, secrets, session = make_service(tmp_path)
    try:
        connection = create_bailian_connection(
            service,
            api_key="top-secret",
        )
        assert connection.has_secret is True
        assert "secret" not in connection.model_dump()
        assert "credential_ref" not in connection.model_dump()
        assert secrets.values_count == 1

        service.record_connection_test(connection.id, ok=True, message="credential accepted")
        tested = service.get_connection(connection.id)
        assert tested.tested is True
        assert tested.test_message == "credential accepted"

        updated = service.update_connection(
            connection.id,
            name="Chat 2",
            credentials={"api_key": "replacement"},
        )
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
    connection = create_bailian_connection(service, api_key="old-secret")
    stored = session.get(ProviderConnectionRecord, connection.id)
    assert stored is not None
    credential_ref = stored.credential_ref

    def fail_commit(_session: Session) -> None:
        raise RuntimeError("database unavailable")

    event.listen(session, "before_commit", fail_commit, once=True)
    with pytest.raises(RuntimeError, match="database unavailable"):
        service.update_connection(
            connection.id,
            credentials={"api_key": "new-secret"},
        )

    stored_secret = secrets.get(credential_ref)
    assert stored_secret is not None
    assert '"api_key":"old-secret"' in stored_secret
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
        chat = create_bailian_connection(service)

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
            model="16k_zh",
            context_length=8192,
            options={"language_scope": "zh_with_limited_english"},
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
            model="qwen-plus",
            context_length=32768,
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

        connection = create_bailian_connection(service, api_key="key")
        notes = service.create_profile(
            name="Notes",
            purpose="notes",
            connection_id=connection.id,
            model="qwen-plus",
            context_length=32768,
        )
        with pytest.raises(InvalidConfiguration, match="tested notes profile"):
            service.update_defaults(notes_enabled=True, notes_profile_id=notes.id)

        service.record_profile_test(notes.id, ok=True, message="ok")
        service.authorize_chat_data(notes.id)
        enabled = service.update_defaults(notes_enabled=True, notes_profile_id=notes.id)
        assert enabled.notes_enabled is True

        service.update_connection(
            connection.id,
            credentials={"api_key": "rotated-key"},
        )
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
        with pytest.raises(InvalidConfiguration, match="endpoint"):
            service.create_connection(
                name="Bad",
                protocol="aliyun_bailian",
                base_url=base_url,
                parameters={"workspace_id": "ws-1234"},
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
                protocol="aliyun_bailian",
                base_url=(
                    "https://ws-1234.cn-beijing.maas.aliyuncs.com/"
                    "compatible-mode/v1"
                ),
                parameters={
                    "workspace_id": "ws-1234",
                    "api_key": "must-not-be-stored",
                },
            )
    finally:
        session.bind.dispose()
        session.close()


def test_loopback_chat_provider_addresses_are_rejected(tmp_path: Path) -> None:
    service, _, session = make_service(tmp_path)
    try:
        with pytest.raises(InvalidConfiguration, match="endpoint"):
            service.create_connection(
                name="Local",
                protocol="aliyun_bailian",
                base_url="http://127.0.0.2:11434/v1",
                parameters={"workspace_id": "ws-1234"},
            )
    finally:
        session.bind.dispose()
        session.close()


class FailingSecretStore(MemorySecretStore):
    def set(self, reference: str, value: str) -> None:
        if '"api_key":"fail"' in value:
            raise RuntimeError("credential manager unavailable")
        super().set(reference, value)


def test_secret_store_failure_rolls_back_database_mutation(tmp_path: Path) -> None:
    engine = initialize_database(tmp_path / "vtnote.db")
    session = Session(engine)
    secrets = FailingSecretStore()
    service = ConfigurationService(session, secrets)
    try:
        existing = create_bailian_connection(
            service,
            name="Original",
            api_key="old",
        )

        with pytest.raises(RuntimeError, match="credential manager"):
            service.update_connection(
                existing.id,
                name="Changed",
                credentials={"api_key": "fail"},
            )

        loaded = service.get_connection(existing.id)
        assert loaded.name == "Original"
        assert loaded.revision == 1
        assert loaded.has_secret is True

        with pytest.raises(RuntimeError, match="credential manager"):
            create_bailian_connection(
                service,
                name="Never committed",
                workspace_id="ws-never",
                api_key="fail",
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
        first = create_bailian_connection(
            service,
            name="First",
            workspace_id="ws-first",
        )
        second = create_bailian_connection(
            service,
            name="Second",
            workspace_id="ws-second",
        )
        cloud = service.create_connection(
            name="Cloud", protocol="tencent_recording_asr",
            base_url="https://asr.tencentcloudapi.com", parameters={}
        )
        profile = service.create_profile(
            name="Notes",
            purpose="notes",
            connection_id=first.id,
            model="qwen-plus",
            context_length=32768,
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
        connection = create_bailian_connection(
            service,
            api_key="unique-secret-value",
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
        connection = create_bailian_connection(service, api_key="known-key")
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
        connection = create_bailian_connection(service)
        with pytest.raises(InvalidConfiguration, match="profile option"):
            service.create_profile(
                name="Notes", purpose="notes", connection_id=connection.id,
                model="qwen-plus",
                context_length=32768,
                options={"temperature": {"secret": "leak"}},
            )
        with pytest.raises(InvalidConfiguration, match="local Whisper"):
            service.update_defaults(local_whisper_options={"api_key": "leak"})
        with pytest.raises(InvalidConfiguration, match="GPU-only"):
            service.update_defaults(local_whisper_options={"device": "cpu"})
    finally:
        session.bind.dispose()
        session.close()
