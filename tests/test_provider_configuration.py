from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from vtnote.ai_stages import SnapshotBailianCredentialResolver
from vtnote.configuration import ConfigurationService, InvalidConfiguration
from vtnote.database import initialize_database
from vtnote.provider_credentials import ApiKeyCredentialBundle
from vtnote.secrets import MemorySecretStore


@pytest.mark.parametrize(
    ("protocol", "base_url", "model", "endpoint"),
    [
        (
            "openai_chat_completions",
            "https://api.openai.com/v1",
            "openai/gpt-4.1-mini",
            "https://api.openai.com/v1/chat/completions",
        ),
        (
            "anthropic_messages",
            "https://api.anthropic.com",
            "claude-sonnet-4-5",
            "https://api.anthropic.com/v1/messages",
        ),
        (
            "google_gemini",
            "https://generativelanguage.googleapis.com",
            "gemini-2.5-flash",
            (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-2.5-flash:generateContent"
            ),
        ),
        (
            "azure_openai",
            "https://summary.openai.azure.com/openai/v1",
            "summary-deployment",
            (
                "https://summary.openai.azure.com/openai/v1/"
                "chat/completions"
            ),
        ),
    ],
)
def test_modern_chat_protocols_create_test_authorize_and_snapshot(
    tmp_path: Path,
    protocol: str,
    base_url: str,
    model: str,
    endpoint: str,
) -> None:
    engine = initialize_database(tmp_path / f"{protocol}.db")
    session = Session(engine)
    secrets = MemorySecretStore()
    service = ConfigurationService(session, secrets)
    try:
        connection = service.create_connection(
            name=protocol,
            protocol=protocol,
            base_url=base_url,
            parameters={},
            credentials={"api_key": "secret-test-only"},
        )
        assert connection.has_secret is True
        assert connection.configured_fields == {"api_key": True}
        profile = service.create_profile(
            name=f"{protocol} notes",
            purpose="notes",
            connection_id=connection.id,
            model=model,
            context_length=32768,
            options={
                "temperature": 0.2,
                "max_tokens": 4096,
                "enable_thinking": False,
            },
        )
        profile = service.record_profile_test(profile.id, ok=True, message="ok")
        assert profile.capability_fingerprint is not None
        assert profile.capability_fingerprint["endpoint"] == endpoint
        profile = service.authorize_chat_data(profile.id)
        assert profile.chat_data_authorized is True
        snapshot = service.snapshot_profile(profile.id)
        assert snapshot["has_secret"] is True
        assert snapshot["chat_data_consent_fingerprint"]
        resolver = SnapshotBailianCredentialResolver(
            engine=engine,
            secrets=secrets,
        )
        validated = resolver.validate(snapshot, purpose="notes")
        assert isinstance(resolver.resolve(validated), ApiKeyCredentialBundle)
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.example.com/v1",
        "https://127.0.0.1/v1",
        "https://user:password@api.example.com/v1",
        "https://api.example.com:8443/v1",
    ],
)
def test_modern_chat_protocol_rejects_unsafe_base_urls(
    tmp_path: Path,
    base_url: str,
) -> None:
    engine = initialize_database(tmp_path / "unsafe.db")
    session = Session(engine)
    service = ConfigurationService(session, MemorySecretStore())
    try:
        with pytest.raises(InvalidConfiguration):
            service.create_connection(
                name="unsafe",
                protocol="openai_chat_completions",
                base_url=base_url,
                parameters={},
                credentials={"api_key": "secret-test-only"},
            )
    finally:
        session.close()
        engine.dispose()
