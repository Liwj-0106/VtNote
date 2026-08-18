from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import vtnote.api as api_module
from vtnote.api import ConnectivityResult, create_app
from vtnote.artifacts import (
    write_note_markdown,
    write_transcript_json,
    write_translation_json,
)
from vtnote.config import Settings
from vtnote.database import initialize_database
from vtnote.models import ItemRecord, ProcessorProfileRecord, StageRunRecord
from vtnote.media import MediaInfo, PreparedAudio
from vtnote.paths import StoragePaths
from vtnote.platform_sources import PlatformSourceRegistry
from vtnote.provider_credentials import (
    BailianCredentialBundle,
    TencentCredentialBundle,
    TokenHubCredentialBundle,
)
from vtnote.schemas import (
    Provenance,
    ProvenanceMethod,
    Transcript,
    TranscriptSegment,
    Translation,
    TranslationEntry,
    transcript_sha256,
)
from vtnote.secrets import MemorySecretStore
from vtnote.sources import SourceProbeResult, make_subtitle_track
from vtnote.runtime_assets import RuntimeAssetService


BASE_URL = "http://127.0.0.1:8765"
MODEL_REVISION = "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"


def bailian_connection_payload(
    *,
    name: str = "Chat",
    workspace_id: str = "ws-1234",
    api_key: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": name,
        "protocol": "aliyun_bailian",
        "base_url": (
            f"https://{workspace_id}.cn-beijing.maas.aliyuncs.com/"
            "compatible-mode/v1"
        ),
        "parameters": {"workspace_id": workspace_id},
    }
    if api_key is not None:
        payload["credentials"] = {"api_key": api_key}
    return payload


class PublicResolver:
    def resolve(self, host: str) -> list[str]:
        return ["142.250.72.14"]


class FakeConnectionTester:
    def __init__(self) -> None:
        self.called = False

    def test_connection(self, connection, secret, *, follow_redirects: bool):
        self.called = True
        assert secret == "super-secret"
        assert follow_redirects is False
        return ConnectivityResult(ok=True, message="accepted super-secret")


class TencentConnectionTester:
    def __init__(self) -> None:
        self.credentials = None

    def test_connection(
        self,
        connection,
        credentials,
        *,
        follow_redirects: bool,
    ):
        self.credentials = credentials
        assert connection.protocol == "tencent_recording_asr"
        assert follow_redirects is False
        return ConnectivityResult(ok=True, message="validated")


class FakeProfileTester:
    def __init__(self) -> None:
        self.calls = []

    def test_profile(
        self,
        profile,
        credentials,
        test_input,
        *,
        follow_redirects: bool,
    ):
        self.calls.append((profile, credentials, test_input))
        assert follow_redirects is False
        assert credentials.secret_id.get_secret_value() == "AKID-example"
        assert credentials.secret_key.get_secret_value() == "secret-key"
        return ConnectivityResult(ok=True, message="provider response omitted")


class SpeechSampleFiles:
    def validate_media(self, path: Path) -> MediaInfo:
        return MediaInfo(
            duration_ms=5_000,
            size_bytes=path.stat().st_size,
            format_name="wav",
            audio_codec="pcm_s16le",
            sample_rate=16_000,
            channels=1,
        )

    def validate_subtitle(self, path: Path) -> None:
        raise AssertionError("speech sample must be media")


class FakeBailianProfileTester:
    def __init__(self) -> None:
        self.calls = []

    def test_profile(
        self,
        profile,
        credentials,
        test_input,
        *,
        follow_redirects: bool,
    ):
        self.calls.append((profile, credentials, test_input))
        assert profile.protocol == "aliyun_bailian"
        assert isinstance(credentials, BailianCredentialBundle)
        assert credentials.api_key.get_secret_value() == "sk-private"
        assert follow_redirects is False
        return ConnectivityResult(ok=True, message="safe")


class FakeTokenHubProfileTester:
    def __init__(self) -> None:
        self.calls = []

    def test_profile(
        self,
        profile,
        credentials,
        test_input,
        *,
        follow_redirects: bool,
    ):
        self.calls.append((profile, credentials, test_input))
        assert profile.protocol == "tencent_tokenhub"
        assert profile.model == "glm-5.1"
        assert profile.context_length == 200_000
        assert isinstance(credentials, TokenHubCredentialBundle)
        assert credentials.api_key.get_secret_value() == "tokenhub-private"
        assert follow_redirects is False
        return ConnectivityResult(ok=True, message="safe")


class FakeSourceProbe:
    def probe(self, url: str):
        assert url == "https://youtu.be/abc"
        return SourceProbeResult(
            source_kind="youtube",
            canonical_url="https://www.youtube.com/watch?v=abc",
            title="Example",
            duration_ms=12_345,
            subtitle_tracks=(
                make_subtitle_track(
                    source_kind="youtube",
                    language="zh-Hans",
                    format="vtt",
                    kind="manual",
                    stable_ordinal=0,
                ),
                make_subtitle_track(
                    source_kind="youtube",
                    language="en",
                    format="vtt",
                    kind="automatic",
                    stable_ordinal=1,
                ),
            ),
            redirect_trace=(
                "https://www.youtube.com/watch?v=abc&token=private-trace",
            ),
        )


class ExplodingSourceProbe:
    def probe(self, url: str):
        raise RuntimeError("Authorization: super-secret")


class UnsafeReportedRedirectProbe:
    def probe(self, url: str):
        return SourceProbeResult(
            source_kind="youtube",
            canonical_url="https://www.youtube.com/watch?v=abc",
            title="Unsafe",
            duration_ms=None,
            subtitle_tracks=(),
            redirect_trace=("https://127.0.0.1/internal",),
        )


class RecoveringDeleteSecretStore(MemorySecretStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail_delete = True

    def delete(self, reference: str) -> None:
        if self.fail_delete:
            raise RuntimeError("credential backend delete unavailable")
        super().delete(reference)


def make_client(
    tmp_path: Path,
    *,
    connection_tester=None,
    profile_tester=None,
    source_probe=None,
    secret_store=None,
    local_source_validator=None,
) -> tuple[TestClient, object, StoragePaths]:
    settings = Settings(data_root=tmp_path / "data", runtime_cache_root=tmp_path / "cache")
    paths = StoragePaths.from_settings(settings)
    engine = initialize_database(paths.database)
    app = create_app(
        settings=settings,
        engine=engine,
        secret_store=secret_store or MemorySecretStore(),
        resolver=PublicResolver(),
        connection_tester=connection_tester,
        profile_tester=profile_tester,
        source_probe=source_probe,
        local_source_validator=local_source_validator,
    )
    return TestClient(app, base_url=BASE_URL), engine, paths


def csrf(client: TestClient) -> dict[str, str]:
    response = client.get("/api/security/csrf")
    assert response.status_code == 200
    return {"Origin": BASE_URL, "X-CSRF-Token": response.json()["csrf_token"]}


def test_tencent_credentials_are_atomic_redacted_and_reject_sts_fields(
    tmp_path: Path,
) -> None:
    client, engine, _ = make_client(tmp_path)
    headers = csrf(client)
    try:
        created = client.post(
            "/api/connections",
            headers=headers,
            json={
                "name": "Tencent",
                "protocol": "tencent_recording_asr",
                "base_url": "https://asr.tencentcloudapi.com",
                "parameters": {},
                "credentials": {
                    "secret_id": "AKID-example",
                    "secret_key": "secret-key",
                },
            },
        )
        assert created.status_code == 201
        assert created.json()["configured_fields"] == {
            "secret_id": True,
            "secret_key": True,
        }
        assert "AKID-example" not in created.text
        assert "secret-key" not in created.text

        rejected = client.post(
            "/api/connections",
            headers=headers,
            json={
                "name": "Temporary credential",
                "protocol": "tencent_recording_asr",
                "base_url": "https://asr.tencentcloudapi.com",
                "parameters": {},
                "credentials": {
                    "secret_id": "AKID",
                    "secret_key": "key",
                    "token": "temporary",
                },
            },
        )
        assert rejected.status_code == 400
        assert rejected.json()["error"]["code"] == "invalid_configuration"
        assert "temporary" not in rejected.text
    finally:
        engine.dispose()


def test_tencent_connection_test_receives_an_atomic_typed_bundle(
    tmp_path: Path,
) -> None:
    tester = TencentConnectionTester()
    client, engine, _ = make_client(tmp_path, connection_tester=tester)
    headers = csrf(client)
    try:
        connection = client.post(
            "/api/connections",
            headers=headers,
            json={
                "name": "Tencent typed test",
                "protocol": "tencent_recording_asr",
                "base_url": "https://asr.tencentcloudapi.com",
                "parameters": {},
                "credentials": {
                    "secret_id": "AKID-example",
                    "secret_key": "secret-key",
                },
            },
        ).json()

        response = client.post(
            f"/api/connections/{connection['id']}/test",
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["test_ok"] is True
        assert isinstance(tester.credentials, TencentCredentialBundle)
        assert (
            tester.credentials.secret_id.get_secret_value()
            == "AKID-example"
        )
        assert (
            tester.credentials.secret_key.get_secret_value()
            == "secret-key"
        )
        assert "AKID-example" not in response.text
        assert "secret-key" not in response.text
    finally:
        engine.dispose()


def test_default_production_composition_wires_tencent_connection_policy_test(
    tmp_path: Path,
) -> None:
    client, engine, _ = make_client(tmp_path)
    headers = csrf(client)
    try:
        connection = client.post(
            "/api/connections",
            headers=headers,
            json={
                "name": "Tencent default adapter",
                "protocol": "tencent_recording_asr",
                "base_url": "https://asr.tencentcloudapi.com",
                "parameters": {},
                "credentials": {
                    "secret_id": "AKID-example",
                    "secret_key": "secret-key",
                },
            },
        ).json()

        response = client.post(
            f"/api/connections/{connection['id']}/test",
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["test_ok"] is True
        assert "AKID-example" not in response.text
        assert "secret-key" not in response.text
    finally:
        engine.dispose()


def test_tencent_asr_verification_uses_builtin_sample_and_delete_cascades(
    tmp_path: Path,
) -> None:
    tester = FakeProfileTester()
    client, engine, _ = make_client(tmp_path, profile_tester=tester)
    headers = csrf(client)
    try:
        connection = client.post(
            "/api/connections",
            headers=headers,
            json={
                "name": "Tencent ASR",
                "protocol": "tencent_recording_asr",
                "base_url": "https://asr.tencentcloudapi.com",
                "parameters": {
                    "asr_region": "ap-guangzhou",
                    "cos_configured": False,
                },
                "credentials": {
                    "secret_id": "AKID-example",
                    "secret_key": "secret-key",
                },
            },
        ).json()

        verified = client.post(
            f"/api/connections/{connection['id']}/verify-asr",
            headers=headers,
            json={
                "acknowledge_billable_request": True,
                "authorize_task_audio_upload": True,
            },
        )

        assert verified.status_code == 200
        assert verified.json()["connection"]["test_ok"] is True
        assert verified.json()["profile"]["test_ok"] is True
        assert verified.json()["profile"]["upload_authorized"] is True
        assert tester.calls[0][2].speech_sample_upload_id == (
            "builtin-tencent-asr-check"
        )
        assert client.get("/api/defaults").json()["cloud_asr_profile_id"] == (
            verified.json()["profile"]["id"]
        )

        deleted = client.delete(
            f"/api/connections/{connection['id']}?cascade_profiles=true",
            headers=headers,
        )
        assert deleted.status_code == 204
        assert client.get("/api/profiles").json() == []
        assert client.get("/api/defaults").json()["cloud_asr_profile_id"] is None
    finally:
        engine.dispose()


def test_tokenhub_verification_creates_and_authorizes_glm_notes_profile(
    tmp_path: Path,
) -> None:
    tester = FakeTokenHubProfileTester()
    client, engine, _ = make_client(tmp_path, profile_tester=tester)
    headers = csrf(client)
    try:
        connection = client.post(
            "/api/connections",
            headers=headers,
            json={
                "name": "Tencent TokenHub",
                "protocol": "tencent_tokenhub",
                "base_url": "https://tokenhub.tencentmaas.com/v1",
                "parameters": {},
                "credentials": {"api_key": "tokenhub-private"},
            },
        ).json()

        verified = client.post(
            f"/api/connections/{connection['id']}/verify-chat",
            headers=headers,
            json={
                "acknowledge_billable_request": True,
                "authorize_chat_data_upload": True,
            },
        )

        assert verified.status_code == 200
        assert verified.json()["connection"]["test_ok"] is True
        assert verified.json()["profile"]["test_ok"] is True
        assert verified.json()["profile"]["chat_data_authorized"] is True
        assert verified.json()["profile"]["model"] == "glm-5.1"
        assert "tokenhub-private" not in verified.text
        assert client.get("/api/defaults").json()["notes_profile_id"] == (
            verified.json()["profile"]["id"]
        )
        assert len(tester.calls) == 1
    finally:
        engine.dispose()


def test_arbitrary_openai_compatible_protocol_is_rejected_at_creation(
    tmp_path: Path,
) -> None:
    client, engine, _ = make_client(tmp_path)
    headers = csrf(client)
    try:
        response = client.post(
            "/api/connections",
            headers=headers,
            json={
                "name": "Future domestic chat",
                "protocol": "openai_compatible",
                "base_url": "https://api.example.com/v1",
                "parameters": {},
                "secret": "domestic-key",
            },
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_configuration"
    finally:
        engine.dispose()


def test_local_whisper_install_routes_require_ack_queue_and_cancel(
    tmp_path: Path,
) -> None:
    client, engine, _ = make_client(tmp_path)
    headers = csrf(client)
    try:
        initial = client.get("/api/assets/local-whisper")
        assert initial.status_code == 200
        assert initial.json()["state"] == "not_installed"
        assert "path" not in initial.text.casefold()

        no_ack = client.post(
            "/api/assets/local-whisper/install",
            headers=headers,
            json={
                "acknowledge_download": False,
                "expected_revision": MODEL_REVISION,
            },
        )
        assert no_ack.status_code == 400
        assert no_ack.json()["error"]["code"] == "download_ack_required"

        queued = client.post(
            "/api/assets/local-whisper/install",
            headers=headers,
            json={
                "acknowledge_download": True,
                "expected_revision": MODEL_REVISION,
            },
        )
        assert queued.status_code == 202
        assert queued.json()["state"] == "queued"
        assert queued.json()["revision"] == MODEL_REVISION
        assert "D:\\" not in queued.text

        canceled = client.post(
            "/api/assets/local-whisper/cancel",
            headers=headers,
        )
        assert canceled.status_code == 200
        assert canceled.json()["state"] == "canceled"
        assert canceled.json()["cancel_requested"] is True
        assert "D:\\" not in canceled.text
    finally:
        engine.dispose()


def test_billable_cloud_profile_test_requires_ack_and_uploaded_speech_sample(
    tmp_path: Path,
) -> None:
    tester = FakeProfileTester()
    client, engine, paths = make_client(
        tmp_path,
        profile_tester=tester,
        local_source_validator=SpeechSampleFiles(),
    )
    headers = csrf(client)
    try:
        connection = client.post(
            "/api/connections",
            headers=headers,
            json={
                "name": "Tencent",
                "protocol": "tencent_recording_asr",
                "base_url": "https://asr.tencentcloudapi.com",
                "parameters": {},
                "credentials": {
                    "secret_id": "AKID-example",
                    "secret_key": "secret-key",
                },
            },
        ).json()
        profile = client.post(
            "/api/profiles",
            headers=headers,
            json={
                "name": "Tencent ASR",
                "purpose": "cloud_asr",
                "connection_id": connection["id"],
                "model": "16k_zh",
                "options": {"language_scope": "zh_with_limited_english"},
            },
        ).json()

        no_ack = client.post(
            f"/api/profiles/{profile['id']}/test",
            headers=headers,
            json={
                "test_kind": "provider_profile",
                "speech_sample_upload_id": "sample-1",
            },
        )
        assert no_ack.status_code == 400
        assert no_ack.json()["error"]["code"] == "billable_test_ack_required"
        no_sample = client.post(
            f"/api/profiles/{profile['id']}/test",
            headers=headers,
            json={
                "test_kind": "provider_profile",
                "acknowledge_billable_request": True,
            },
        )
        assert no_sample.status_code == 400
        assert no_sample.json()["error"]["code"] == "speech_test_sample_required"

        sample = client.post(
            "/api/test-samples",
            headers=headers,
            files=[
                (
                    "metadata",
                    (
                        None,
                        json.dumps({"kind": "media"}),
                        "application/json",
                    ),
                ),
                ("file", ("sample.wav", b"speech-bytes", "audio/wav")),
            ],
        )
        assert sample.status_code == 201
        sample_id = sample.json()["id"]
        assert sample.json()["duration_ms"] == 5_000
        assert client.get("/api/tasks").json() == []

        tested = client.post(
            f"/api/profiles/{profile['id']}/test",
            headers=headers,
            json={
                "test_kind": "provider_profile",
                "acknowledge_billable_request": True,
                "speech_sample_upload_id": sample_id,
            },
        )
        assert tested.status_code == 200
        assert tested.json()["test_ok"] is True
        assert len(tester.calls) == 1
        test_input = tester.calls[0][2]
        assert test_input.speech_sample_upload_id == sample_id
        with Session(engine) as session:
            assert (
                RuntimeAssetService(session, paths).active_for_role(
                    item_id=sample_id,
                    role="uploaded_source",
                )
                is None
            )
    finally:
        engine.dispose()


def test_bailian_policy_capability_test_and_chat_data_consent_are_independent(
    tmp_path: Path,
) -> None:
    tester = FakeBailianProfileTester()
    client, engine, _ = make_client(tmp_path, profile_tester=tester)
    headers = csrf(client)
    try:
        connection_response = client.post(
            "/api/connections",
            headers=headers,
            json={
                "name": "Bailian",
                "protocol": "aliyun_bailian",
                "parameters": {"workspace_id": "ws-1234"},
                "credentials": {"api_key": "sk-private"},
            },
        )
        assert connection_response.status_code == 201
        connection = connection_response.json()
        profile_response = client.post(
            "/api/profiles",
            headers=headers,
            json={
                "name": "Notes",
                "purpose": "notes",
                "connection_id": connection["id"],
                "model": "qwen-plus",
                "context_length": 32768,
                "options": {
                    "temperature": 0.2,
                    "max_tokens": 4096,
                    "enable_thinking": False,
                },
            },
        )
        assert profile_response.status_code == 201
        profile = profile_response.json()

        static = client.post(
            f"/api/profiles/{profile['id']}/test",
            headers=headers,
            json={"test_kind": "connection_policy_validated"},
        )
        assert static.status_code == 200
        assert static.json()["tested"] is False
        assert static.json()["chat_data_authorized"] is False
        assert tester.calls == []

        no_ack = client.post(
            f"/api/profiles/{profile['id']}/test",
            headers=headers,
            json={"test_kind": "profile_capability_tested"},
        )
        assert no_ack.status_code == 400
        assert no_ack.json()["error"]["code"] == "billable_test_ack_required"

        tested = client.post(
            f"/api/profiles/{profile['id']}/test",
            headers=headers,
            json={
                "test_kind": "profile_capability_tested",
                "acknowledge_billable_request": True,
            },
        )
        assert tested.status_code == 200
        assert tested.json()["tested"] is True
        assert tested.json()["capability_fingerprint"]["protocol"] == "aliyun_bailian"
        assert tested.json()["chat_data_authorized"] is False
        assert len(tester.calls) == 1

        no_consent = client.post(
            f"/api/profiles/{profile['id']}/authorize-chat-data",
            headers=headers,
            json={"acknowledge_chat_data_upload": False},
        )
        assert no_consent.status_code == 400
        assert no_consent.json()["error"]["code"] == "chat_data_consent_required"

        authorized = client.post(
            f"/api/profiles/{profile['id']}/authorize-chat-data",
            headers=headers,
            json={"acknowledge_chat_data_upload": True},
        )
        assert authorized.status_code == 200
        assert authorized.json()["chat_data_authorized"] is True
        assert authorized.json()["chat_data_scope"] == {
            "subtitle_cues": True,
            "title_and_metadata": True,
            "target_or_output_language": True,
            "custom_prompt": True,
            "audio": False,
        }
        revoked = client.post(
            f"/api/profiles/{profile['id']}/revoke-chat-data",
            headers=headers,
        )
        assert revoked.status_code == 200
        assert revoked.json()["chat_data_authorized"] is False
    finally:
        engine.dispose()


def test_exact_host_origin_and_double_submit_csrf_are_enforced(tmp_path: Path) -> None:
    client, engine, _ = make_client(tmp_path)
    try:
        assert client.get("/api/tasks", headers={"Host": "localhost:8765"}).status_code == 403
        no_origin = client.post("/api/tasks", json={"sources": []})
        assert no_origin.status_code == 403
        token_headers = csrf(client)
        wrong_origin = client.post(
            "/api/tasks",
            json={"sources": []},
            headers={**token_headers, "Origin": "http://localhost:8765"},
        )
        assert wrong_origin.status_code == 403
        wrong_csrf = client.post(
            "/api/tasks",
            json={"sources": []},
            headers={**token_headers, "X-CSRF-Token": "wrong"},
        )
        assert wrong_csrf.status_code == 403
        assert "access-control-allow-origin" not in client.get("/api/tasks").headers
    finally:
        engine.dispose()


def test_non_ascii_csrf_header_returns_sanitized_error(tmp_path: Path) -> None:
    client, engine, _ = make_client(tmp_path)
    try:
        csrf(client)
        headers = httpx.Headers(
            [
                (b"Origin", BASE_URL.encode("ascii")),
                (b"X-CSRF-Token", b"\xff"),
            ]
        )
        response = client.post("/api/tasks", headers=headers, json={"sources": []})
        assert response.status_code == 403
        assert response.json() == {
            "error": {
                "code": "csrf_failed",
                "message": "CSRF validation failed",
                "details": None,
            }
        }
    finally:
        engine.dispose()


def test_interactive_api_docs_are_disabled_unless_dev_mode_is_explicit(tmp_path: Path) -> None:
    client, engine, _ = make_client(tmp_path)
    try:
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404
    finally:
        engine.dispose()

    settings = Settings(
        data_root=tmp_path / "dev-data",
        runtime_cache_root=tmp_path / "dev-cache",
        enable_dev_docs=True,
    )
    paths = StoragePaths.from_settings(settings)
    engine = initialize_database(paths.database)
    client = TestClient(
        create_app(
            settings=settings,
            engine=engine,
            secret_store=MemorySecretStore(),
            resolver=PublicResolver(),
        ),
        base_url=BASE_URL,
    )
    try:
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200
    finally:
        engine.dispose()


def test_errors_use_one_sanitized_shape(tmp_path: Path) -> None:
    client, engine, _ = make_client(tmp_path)
    try:
        response = client.post("/api/tasks", json={"sources": []}, headers=csrf(client))
        assert response.status_code == 400
        assert response.json() == {
            "error": {"code": "invalid_task", "message": "at least one source is required", "details": None}
        }
        not_found = client.get("/api/tasks/00000000-0000-0000-0000-000000000000")
        assert not_found.status_code == 404
        assert set(not_found.json()) == {"error"}
    finally:
        engine.dispose()


def test_connection_profile_and_defaults_routes_redact_secrets(tmp_path: Path) -> None:
    tester = FakeConnectionTester()
    client, engine, _ = make_client(tmp_path, connection_tester=tester)
    headers = csrf(client)
    try:
        created = client.post(
            "/api/connections",
            headers=headers,
            json=bailian_connection_payload(api_key="super-secret"),
        )
        assert created.status_code == 201
        connection = created.json()
        assert connection["has_secret"] is True
        assert connection["cleanup_pending"] is False
        assert "secret" not in connection
        assert "super-secret" not in created.text
        assert "credential_ref" not in created.text

        tested = client.post(f"/api/connections/{connection['id']}/test", headers=headers)
        assert tested.status_code == 200
        assert tester.called is False
        assert "super-secret" not in tested.text
        assert tested.json()["test_message"] == "Connection policy validated"

        assert client.get("/api/connections").status_code == 200
        assert client.get(f"/api/connections/{connection['id']}").status_code == 200
        patched = client.patch(
            f"/api/connections/{connection['id']}",
            headers=headers,
            json={"name": "Chat updated"},
        )
        assert patched.status_code == 200
        assert patched.json()["revision"] == 1

        profile = client.post(
            "/api/profiles",
            headers=headers,
            json={
                "name": "Notes",
                "purpose": "notes",
                "connection_id": connection["id"],
                "model": "qwen-plus",
                "context_length": 32768,
                "options": {"max_tokens": 4096},
            },
        )
        assert profile.status_code == 201
        profile_id = profile.json()["id"]
        assert client.get("/api/profiles").status_code == 200
        assert client.patch(
            f"/api/profiles/{profile_id}",
            headers=headers,
            json={"model": "qwen-max"},
        ).status_code == 200
        assert client.get("/api/defaults").json()["translation_enabled"] is False
        assert client.patch("/api/defaults", headers=headers, json={"asr_mode": "local"}).status_code == 200
        translation = client.post(
            "/api/profiles",
            headers=headers,
            json={
                "name": "Translation", "purpose": "translation",
                "connection_id": connection["id"],
                "model": "qwen-plus",
                "context_length": 32768,
                "options": {"max_tokens": 4096},
            },
        )
        assert translation.status_code == 201
        selected = client.patch(
            "/api/defaults", headers=headers,
            json={"translation_profile_id": translation.json()["id"]},
        )
        assert selected.json()["translation_profile_id"] == translation.json()["id"]
        cleared = client.patch(
            "/api/defaults", headers=headers, json={"translation_profile_id": None}
        )
        assert cleared.json()["translation_profile_id"] is None
        assert client.delete(
            f"/api/profiles/{translation.json()['id']}", headers=headers
        ).status_code == 204
        assert client.delete(f"/api/profiles/{profile_id}", headers=headers).status_code == 204
        assert client.delete(f"/api/connections/{connection['id']}", headers=headers).status_code == 204
        assert client.get(f"/api/profiles/{profile_id}").status_code == 404
        assert client.get(f"/api/connections/{connection['id']}").status_code == 404
        assert all(
            item["id"] != profile_id for item in client.get("/api/profiles").json()
        )
        assert all(
            item["id"] != connection["id"]
            for item in client.get("/api/connections").json()
        )
    finally:
        engine.dispose()


def test_credential_cleanup_status_and_retry_never_expose_reference_or_secret(
    tmp_path: Path,
) -> None:
    secrets = RecoveringDeleteSecretStore()
    client, engine, _ = make_client(tmp_path, secret_store=secrets)
    headers = csrf(client)
    try:
        connection = client.post(
            "/api/connections",
            headers=headers,
            json=bailian_connection_payload(
                name="Disposable",
                workspace_id="ws-disposable",
                api_key="cleanup-secret",
            ),
        ).json()
        assert client.delete(
            f"/api/connections/{connection['id']}", headers=headers
        ).status_code == 204

        pending = client.get("/api/credential-cleanup")
        assert pending.json() == {"cleanup_pending": True, "pending_count": 1}
        assert "cleanup-secret" not in pending.text
        assert "connection:" not in pending.text

        secrets.fail_delete = False
        retried = client.post("/api/credential-cleanup/retry", headers=headers)
        assert retried.json() == {"cleanup_pending": False, "pending_count": 0}
    finally:
        engine.dispose()


def test_patch_rejects_explicit_null_and_empty_patch_is_a_noop(tmp_path: Path) -> None:
    client, engine, _ = make_client(tmp_path)
    headers = csrf(client)
    try:
        connection = client.post(
            "/api/connections", headers=headers,
            json=bailian_connection_payload(),
        ).json()
        empty = client.patch(
            f"/api/connections/{connection['id']}", headers=headers, json={}
        )
        assert empty.status_code == 200
        assert empty.json()["revision"] == connection["revision"]
        assert client.patch(
            f"/api/connections/{connection['id']}", headers=headers,
            json={"base_url": None},
        ).status_code == 422

        profile = client.post(
            "/api/profiles", headers=headers,
            json={
                "name": "Notes", "purpose": "notes",
                "connection_id": connection["id"],
                "model": "qwen-plus",
                "context_length": 32768,
                "options": {"max_tokens": 4096},
            },
        ).json()
        assert client.patch(
            f"/api/profiles/{profile['id']}", headers=headers,
            json={"model": None},
        ).status_code == 422
        assert client.patch(
            f"/api/profiles/{profile['id']}", headers=headers, json={}
        ).json()["revision"] == profile["revision"]
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "field",
    [
        "asr_mode", "translation_enabled", "translation_target_language",
        "notes_enabled", "notes_template", "notes_output_language",
        "local_whisper_options",
    ],
)
def test_defaults_patch_rejects_null_for_non_nullable_fields(
    tmp_path: Path, field: str,
) -> None:
    client, engine, _ = make_client(tmp_path)
    try:
        response = client.patch(
            "/api/defaults", headers=csrf(client), json={field: None}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "field",
    [
        "cloud_asr_profile_id",
        "translation_profile_id",
        "notes_profile_id",
        "notes_custom_prompt",
    ],
)
def test_defaults_patch_allows_null_only_for_nullable_fields(
    tmp_path: Path, field: str,
) -> None:
    client, engine, _ = make_client(tmp_path)
    try:
        response = client.patch(
            "/api/defaults", headers=csrf(client), json={field: None}
        )
        assert response.status_code == 200
        if field == "notes_custom_prompt":
            assert response.json()["has_custom_prompt"] is False
            assert field not in response.json()
        else:
            assert response.json()[field] is None
    finally:
        engine.dispose()


def test_default_registry_reports_youtube_runtime_and_probe_injection_revalidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert not hasattr(api_module, "ProbeResult")
    assert not hasattr(api_module, "SourceProbe")
    monkeypatch.setattr(
        api_module,
        "build_default_platform_registry",
        lambda **_: PlatformSourceRegistry(
            bilibili=None,
            youtube=None,
            youtube_unavailable_code="youtube_runtime_unavailable",
        ),
    )
    client, engine, _ = make_client(tmp_path)
    try:
        response = client.post(
            "/api/sources/probe",
            headers=csrf(client),
            json={"url": "https://youtu.be/abc"},
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "youtube_runtime_unavailable"
    finally:
        engine.dispose()

    client, engine, _ = make_client(tmp_path / "injected", source_probe=FakeSourceProbe())
    try:
        response = client.post(
            "/api/sources/probe",
            headers=csrf(client),
            json={"url": "https://youtu.be/abc"},
        )
        assert response.status_code == 200
        manual = make_subtitle_track(
            source_kind="youtube",
            language="zh-Hans",
            format="vtt",
            kind="manual",
            stable_ordinal=0,
        )
        automatic = make_subtitle_track(
            source_kind="youtube",
            language="en",
            format="vtt",
            kind="automatic",
            stable_ordinal=1,
        )
        assert response.json() == {
            "source_kind": "youtube",
            "canonical_url": "https://www.youtube.com/watch?v=abc",
            "title": "Example",
            "duration_ms": 12_345,
            "subtitle_tracks": [
                {
                    "id": manual.id,
                    "language": "zh-hans",
                    "format": "vtt",
                    "kind": "manual",
                    "ui_label": "人工字幕",
                    "is_translated": False,
                    "is_live_chat": False,
                },
                {
                    "id": automatic.id,
                    "language": "en",
                    "format": "vtt",
                    "kind": "automatic",
                    "ui_label": "自动字幕",
                    "is_translated": False,
                    "is_live_chat": False,
                },
            ],
        }
        assert "private-trace" not in response.text
    finally:
        engine.dispose()


def test_probe_centrally_rejects_an_unsafe_reported_redirect_chain(tmp_path: Path) -> None:
    client, engine, _ = make_client(
        tmp_path, source_probe=UnsafeReportedRedirectProbe()
    )
    try:
        response = client.post(
            "/api/sources/probe", headers=csrf(client),
            json={"url": "https://youtu.be/abc"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "unsafe_source_url"
    finally:
        engine.dispose()


def test_task_list_get_cancel_retry_and_export_routes(tmp_path: Path) -> None:
    client, engine, paths = make_client(tmp_path)
    headers = csrf(client)
    try:
        created = client.post(
            "/api/tasks",
            headers=headers,
            json={"sources": [{"kind": "url", "locator": "https://youtu.be/abc"}]},
        )
        assert created.status_code == 201
        task = created.json()
        overridden = client.post(
            "/api/tasks",
            headers=headers,
            json={
                "sources": [{"kind": "url", "locator": "https://youtu.be/def"}],
                "asr_mode": "local",
                "translation_enabled": False,
                "translation_target_language": "ja",
                "notes_enabled": False,
                "notes_template": "key_points",
                "notes_output_language": "en",
            },
        )
        assert overridden.status_code == 201
        assert overridden.json()["pipeline_snapshot"]["schema_version"] == 1
        assert overridden.json()["pipeline_snapshot"]["asr"]["mode"] == "local"
        assert overridden.json()["pipeline_snapshot"]["translation"]["target_language"] == "ja"
        assert overridden.json()["pipeline_snapshot"]["notes"]["template"] == "key_points"
        assert client.get("/api/tasks").status_code == 200
        assert client.get(f"/api/tasks/{task['id']}").status_code == 200

        item_id = task["items"][0]["id"]
        with Session(engine) as session:
            item = session.get(ItemRecord, item_id)
            assert item is not None
            next(run for run in item.stage_runs if run.stage == "source").status = "completed"
            next(run for run in item.stage_runs if run.stage == "transcribe").status = "failed"
            session.commit()
        retried = client.post(
            f"/api/tasks/{task['id']}/retry", headers=headers,
            json={
                "item_id": item_id,
                "stage": "transcribe",
                "expected_attempt": 1,
            },
        )
        assert retried.status_code == 201
        assert client.post(
            f"/api/items/{item_id}/stages/transcribe/retry", headers=headers
        ).status_code == 404

        with Session(engine) as session:
            item = session.get(ItemRecord, item_id)
            assert item is not None
            latest = [run for run in item.stage_runs if run.stage == "transcribe"][-1]
            latest.status = "failed"
            session.commit()
        task_retry = client.post(
            f"/api/tasks/{task['id']}/retry",
            headers=headers,
            json={
                "item_id": item_id,
                "stage": "transcribe",
                "expected_attempt": 2,
            },
        )
        assert task_retry.status_code == 201

        transcript = Transcript(
            language="en",
            duration_ms=500,
            provenance=Provenance(method=ProvenanceMethod.PLATFORM_SUBTITLE, provider="youtube"),
            segments=[TranscriptSegment(id="seg_000001", start_ms=0, end_ms=500, text="Hello")],
        )
        write_transcript_json(paths, item_id, transcript)
        exported = client.get(f"/api/items/{item_id}/export?variant=original&format=txt")
        assert exported.status_code == 200
        assert exported.text == "Hello\n"

        canceled = client.post(f"/api/tasks/{task['id']}/cancel", headers=headers)
        assert canceled.status_code == 200
    finally:
        engine.dispose()


def test_health_readiness_pagination_and_result_read_models(tmp_path: Path) -> None:
    client, engine, paths = make_client(tmp_path)
    headers = csrf(client)
    try:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json() == {
            "status": "ok",
            "service": "vtnote",
            "version": "0.1.0",
        }
        readiness = client.get("/api/readiness")
        assert readiness.status_code == 200
        assert "secret" not in readiness.text.casefold()
        assert readiness.json()["limits"]["max_task_sources"] == 1

        created_tasks = [
            client.post(
                "/api/tasks",
                headers=headers,
                json={
                    "sources": [
                        {
                            "kind": "url",
                            "locator": f"https://youtu.be/read-{index}",
                        }
                    ]
                },
            ).json()
            for index in range(3)
        ]
        first_page = client.get("/api/tasks?limit=2")
        assert first_page.status_code == 200
        assert len(first_page.json()) == 2
        cursor = first_page.headers["x-next-cursor"]
        second_page = client.get(f"/api/tasks?limit=2&cursor={cursor}")
        assert len(second_page.json()) == 1
        assert {
            item["id"] for item in first_page.json() + second_page.json()
        } == {item["id"] for item in created_tasks}

        task = created_tasks[-1]
        item_id = task["items"][0]["id"]
        transcript = Transcript(
            language="en",
            duration_ms=2_000,
            provenance=Provenance(
                method=ProvenanceMethod.PLATFORM_SUBTITLE,
                provider="youtube",
            ),
            segments=(
                TranscriptSegment(
                    id="seg_000001",
                    start_ms=0,
                    end_ms=1_000,
                    text="First cue",
                ),
                TranscriptSegment(
                    id="seg_000002",
                    start_ms=1_000,
                    end_ms=2_000,
                    text="Second cue",
                ),
            ),
        )
        translation = Translation(
            language="zh-Hans",
            source_transcript_sha256=transcript_sha256(transcript),
            entries=(
                TranslationEntry(cue_id="seg_000001", text="第一句"),
                TranslationEntry(cue_id="seg_000002", text="第二句"),
            ),
        )
        write_transcript_json(paths, item_id, transcript)
        write_translation_json(paths, item_id, translation, transcript)
        note_id = "11111111-1111-4111-8111-111111111111"
        write_note_markdown(
            paths,
            item_id,
            note_id,
            "---\n"
            "generated_by_ai: true\n"
            f"task_id: {task['id']}\n"
            f"transcript_sha256: {translation.source_transcript_sha256}\n"
            "template: summary\n"
            "output_language: zh-Hans\n"
            "requested_model: qwen-plus\n"
            "response_model: qwen-plus\n"
            "---\n\n# 笔记\n\n内容 [seg_000001 @ 00:00.000–00:01.000]\n",
        )
        with Session(engine) as session:
            item = session.get(ItemRecord, item_id)
            assert item is not None
            item.title = "A title with / unsafe : filename"
            item.status = "completed_with_warnings"
            item.task.status = "completed_with_warnings"
            source = next(run for run in item.stage_runs if run.stage == "source")
            source.status = "completed"
            transcribe = next(
                run for run in item.stage_runs if run.stage == "transcribe"
            )
            transcribe.status = "completed"
            transcribe.execution_evidence_json = {
                "source_method": "platform_subtitle",
                "provider": "youtube",
            }
            transcribe.warning = "safe warning"
            session.commit()

        task_result = client.get(f"/api/tasks/{task['id']}").json()
        assert task_result["created_at"]
        assert task_result["items"][0]["stage_runs"][0]["created_at"]
        assert (
            task_result["items"][0]["stage_runs"][1]["execution_evidence"][
                "source_method"
            ]
            == "platform_subtitle"
        )
        transcript_response = client.get(f"/api/items/{item_id}/transcript")
        assert transcript_response.status_code == 200
        assert transcript_response.json()["segments"][0]["text"] == "First cue"
        translated_response = client.get(
            f"/api/items/{item_id}/translations/zh-Hans"
        )
        assert translated_response.status_code == 200
        assert translated_response.json()["entries"][0]["text"] == "第一句"
        notes_response = client.get(f"/api/items/{item_id}/notes")
        assert notes_response.status_code == 200
        assert notes_response.json()[0]["id"] == note_id
        assert "# 笔记" in notes_response.json()[0]["markdown"]

        summary_json = client.get(
            f"/api/items/{item_id}/execution-summary?format=json"
        )
        assert summary_json.status_code == 200
        assert summary_json.json()["task_status"] == "completed_with_warnings"
        assert "source_locator" not in summary_json.text
        summary_markdown = client.get(
            f"/api/items/{item_id}/execution-summary?format=markdown"
        )
        assert summary_markdown.status_code == 200
        assert "# 执行摘要" in summary_markdown.text
        assert "safe warning" in summary_markdown.text

        exported = client.get(
            f"/api/items/{item_id}/export?variant=original&format=srt"
        )
        assert exported.status_code == 200
        disposition = exported.headers["content-disposition"]
        assert disposition.startswith('attachment; filename="')
        assert "/" not in disposition
        assert ":" not in disposition
    finally:
        engine.dispose()


def test_item_outcomes_and_audio_download_reflect_available_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine, paths = make_client(tmp_path)
    headers = csrf(client)
    try:
        task = client.post(
            "/api/tasks",
            headers=headers,
            json={
                "sources": [{"kind": "url", "locator": "https://youtu.be/outcomes"}],
                "output_type": "audio",
            },
        ).json()
        item_id = task["items"][0]["id"]
        source = paths.downloaded_audio(item_id, "webm")
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"owned-audio")
        transcript = Transcript(
            language="en",
            duration_ms=500,
            provenance=Provenance(
                method=ProvenanceMethod.PLATFORM_SUBTITLE,
                provider="youtube",
            ),
            segments=(
                TranscriptSegment(
                    id="seg_000001", start_ms=0, end_ms=500, text="Hello"
                ),
            ),
        )
        write_transcript_json(paths, item_id, transcript)
        write_note_markdown(
            paths,
            item_id,
            "22222222-2222-4222-8222-222222222222",
            "# Note\n",
        )
        with Session(engine) as session:
            RuntimeAssetService(session, paths).register_staged(
                item_id=item_id,
                role="downloaded_audio",
                relative_path=paths.runtime_relative(source),
            )

        outcomes = client.get(f"/api/items/{item_id}/outcomes")
        assert outcomes.status_code == 200
        assert outcomes.json() == {"audio": True, "transcript": True, "notes": True}

        class FakeExportProcessor:
            def __init__(self, **_: object) -> None:
                pass

            def export_audio(
                self, export_item_id: str, export_source: Path, export_format: str
            ) -> PreparedAudio:
                assert export_source == source
                destination = paths.export_audio(export_item_id, export_format)
                destination.write_bytes(b"converted-audio")
                return PreparedAudio(
                    destination,
                    None,
                    True,
                    MediaInfo(500, 15, export_format, "aac", 44_100, 2),
                )

        monkeypatch.setattr(api_module, "FfmpegMediaProcessor", FakeExportProcessor)
        downloaded = client.get(f"/api/items/{item_id}/audio?format=m4a")
        assert downloaded.status_code == 200
        assert downloaded.content == b"converted-audio"
        assert "vtnote-" in downloaded.headers["content-disposition"]
        assert downloaded.headers["content-disposition"].endswith('-audio.m4a"')
    finally:
        engine.dispose()


def test_storage_trash_list_and_restore_are_typed_and_csrf_protected(
    tmp_path: Path,
) -> None:
    client, engine, paths = make_client(tmp_path)
    headers = csrf(client)
    try:
        task = client.post(
            "/api/tasks",
            headers=headers,
            json={
                "sources": [
                    {"kind": "url", "locator": "https://youtu.be/storage"}
                ]
            },
        ).json()
        item_id = task["items"][0]["id"]
        runtime_file = paths.downloaded_audio(item_id, "webm")
        runtime_file.parent.mkdir(parents=True, exist_ok=True)
        runtime_file.write_bytes(b"owned")
        with Session(engine) as session:
            item = session.get(ItemRecord, item_id)
            assert item is not None
            item.status = "completed"
            item.task.status = "completed"
            service = RuntimeAssetService(session, paths)
            asset = service.register_staged(
                item_id=item_id,
                role="downloaded_audio",
                relative_path=paths.runtime_relative(runtime_file),
            )
            trashed = service.trash(asset.id)
            asset_id = trashed.id
        storage = client.get("/api/storage")
        assert storage.status_code == 200
        assert storage.json()["trash"]["count"] == 1
        trash = client.get("/api/storage/trash")
        assert trash.status_code == 200
        assert trash.json() == [
            {
                "id": asset_id,
                "item_id": item_id,
                "role": "downloaded_audio",
                "state": "trash",
                "size_bytes": 5,
                "purge_after": trash.json()[0]["purge_after"],
            }
        ]
        assert "relative_path" not in trash.text
        assert client.post(
            f"/api/storage/trash/{asset_id}/restore"
        ).status_code == 403
        restored = client.post(
            f"/api/storage/trash/{asset_id}/restore",
            headers=headers,
        )
        assert restored.status_code == 200
        assert restored.json()["state"] == "active"
        assert runtime_file.read_bytes() == b"owned"
    finally:
        engine.dispose()


def test_task_create_accepts_and_returns_output_type(tmp_path: Path) -> None:
    client, engine, _ = make_client(tmp_path)
    try:
        response = client.post(
            "/api/tasks",
            headers=csrf(client),
            json={
                "sources": [
                    {"kind": "url", "locator": "https://youtu.be/output-type"}
                ],
                "output_type": "transcript",
            },
        )
        assert response.status_code == 201
        payload = response.json()
        assert payload["options"]["output_type"] == "transcript"
        assert payload["pipeline_snapshot"]["output_type"] == "transcript"
        assert [run["stage"] for run in payload["items"][0]["stage_runs"]] == [
            "source",
            "transcribe",
        ]
    finally:
        engine.dispose()


def test_task_create_accepts_audio_export_enabled(tmp_path: Path) -> None:
    client, engine, _ = make_client(tmp_path)
    try:
        response = client.post(
            "/api/tasks",
            headers=csrf(client),
            json={
                "sources": [
                    {"kind": "url", "locator": "https://youtu.be/full-output"}
                ],
                "audio_export_enabled": True,
                "translation_enabled": False,
                "notes_enabled": False,
            },
        )
        assert response.status_code == 201
        payload = response.json()
        assert payload["options"]["audio_export_enabled"] is True
        assert payload["pipeline_snapshot"]["audio_export_enabled"] is True
        assert [run["stage"] for run in payload["items"][0]["stage_runs"]] == [
            "source",
            "transcribe",
        ]
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "payload",
    [
        {"item_id": "item", "stage": "transcribe"},
        {"item_id": "item", "stage": "transcribe", "expected_attempt": 0},
        {"item_id": "item", "stage": "transcribe", "expected_attempt": True},
        {
            "item_id": "item",
            "stage": "transcribe",
            "expected_attempt": 1,
            "strategy": "local",
            "cloud_profile_id": "profile",
        },
        {
            "item_id": "item",
            "stage": "transcribe",
            "expected_attempt": 1,
            "strategy": "cloud_confirmed",
        },
        {
            "item_id": "item",
            "stage": "transcribe",
            "expected_attempt": 1,
            "strategy": "cloud_confirmed",
            "cloud_profile_id": "profile",
            "connection_revision": 1,
            "profile_revision": 1,
            "acknowledge_possible_charge": False,
        },
    ],
)
def test_retry_api_validates_attempt_strategy_and_charge_fields(
    tmp_path: Path, payload: dict[str, object],
) -> None:
    client, engine, _ = make_client(tmp_path)
    try:
        response = client.post(
            "/api/tasks/11111111-1111-4111-8111-111111111111/retry",
            headers=csrf(client),
            json=payload,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
    finally:
        engine.dispose()


def test_retry_api_accepts_explicit_local_and_cloud_confirmed_success(
    tmp_path: Path,
) -> None:
    client, engine, _ = make_client(tmp_path)
    headers = csrf(client)
    try:
        local_task = client.post(
            "/api/tasks",
            headers=headers,
            json={"sources": [{"kind": "url", "locator": "https://youtu.be/local"}]},
        ).json()
        local_item_id = local_task["items"][0]["id"]
        with Session(engine) as session:
            item = session.get(ItemRecord, local_item_id)
            assert item is not None
            next(run for run in item.stage_runs if run.stage == "source").status = (
                "completed"
            )
            transcribe = next(
                run for run in item.stage_runs if run.stage == "transcribe"
            )
            transcribe.status = "failed"
            transcribe.external_submission_state = "submission_unknown"
            session.commit()

        local_retry = client.post(
            f"/api/tasks/{local_task['id']}/retry",
            headers=headers,
            json={
                "item_id": local_item_id,
                "stage": "transcribe",
                "expected_attempt": 1,
                "strategy": "local",
            },
        )
        assert local_retry.status_code == 201
        assert all(
            "retry_override" not in stage
            for stage in local_retry.json()["stage_runs"]
        )
        with Session(engine) as session:
            local_attempt = session.scalar(
                select(StageRunRecord).where(
                    StageRunRecord.item_id == local_item_id,
                    StageRunRecord.stage == "transcribe",
                    StageRunRecord.attempt == 2,
                )
            )
            assert local_attempt is not None
            assert local_attempt.retry_override_json == {
                "schema_version": 1,
                "strategy": "local",
                "asr": {"mode": "local", "profile": None},
            }

        connection = client.post(
            "/api/connections",
            headers=headers,
            json={
                "name": "Retry cloud",
                "protocol": "tencent_recording_asr",
                "base_url": "https://asr.tencentcloudapi.com",
                "parameters": {},
                "credentials": {
                    "secret_id": "AKID",
                    "secret_key": "retry-secret",
                },
            },
        ).json()
        profile = client.post(
            "/api/profiles",
            headers=headers,
            json={
                "name": "Explicit cloud retry",
                "purpose": "cloud_asr",
                "connection_id": connection["id"],
                "model": "16k_zh",
            },
        ).json()
        with Session(engine) as session:
            row = session.get(ProcessorProfileRecord, profile["id"])
            assert row is not None
            row.test_ok = True
            row.tested_revision = row.revision
            row.tested_connection_revision = row.connection.revision
            row.upload_authorized_revision = row.revision
            row.upload_authorized_connection_revision = row.connection.revision
            session.commit()
            profile_revision = row.revision
            connection_revision = row.connection.revision

        cloud_task = client.post(
            "/api/tasks",
            headers=headers,
            json={
                "sources": [
                    {"kind": "url", "locator": "https://youtu.be/cloud"}
                ],
                "asr_mode": "local",
            },
        ).json()
        cloud_item_id = cloud_task["items"][0]["id"]
        with Session(engine) as session:
            item = session.get(ItemRecord, cloud_item_id)
            assert item is not None
            next(run for run in item.stage_runs if run.stage == "source").status = (
                "completed"
            )
            transcribe = next(
                run for run in item.stage_runs if run.stage == "transcribe"
            )
            transcribe.status = "failed"
            transcribe.external_submission_state = "submission_unknown"
            session.commit()

        cloud_retry = client.post(
            f"/api/tasks/{cloud_task['id']}/retry",
            headers=headers,
            json={
                "item_id": cloud_item_id,
                "stage": "transcribe",
                "expected_attempt": 1,
                "strategy": "cloud_confirmed",
                "cloud_profile_id": profile["id"],
                "connection_revision": connection_revision,
                "profile_revision": profile_revision,
                "acknowledge_possible_charge": True,
            },
        )
        assert cloud_retry.status_code == 201
        with Session(engine) as session:
            cloud_attempt = session.scalar(
                select(StageRunRecord).where(
                    StageRunRecord.item_id == cloud_item_id,
                    StageRunRecord.stage == "transcribe",
                    StageRunRecord.attempt == 2,
                )
            )
            assert cloud_attempt is not None
            assert cloud_attempt.retry_override_json["strategy"] == (
                "cloud_confirmed"
            )
            assert cloud_attempt.retry_override_json["asr"]["profile"]["id"] == (
                profile["id"]
            )
            assert (
                "acknowledge_possible_charge"
                not in cloud_attempt.retry_override_json
            )
    finally:
        engine.dispose()


def test_task_options_cannot_smuggle_credentials_into_durable_responses(tmp_path: Path) -> None:
    client, engine, _ = make_client(tmp_path)
    try:
        response = client.post(
            "/api/tasks",
            headers=csrf(client),
            json={
                "sources": [{"kind": "url", "locator": "https://youtu.be/abc"}],
                "options": {"secret": "leak-me"},
            },
        )
        assert response.status_code == 422
        assert "leak-me" not in response.text
    finally:
        engine.dispose()


def test_unexpected_adapter_errors_are_sanitized(
    tmp_path: Path, caplog,
) -> None:
    settings = Settings(
        data_root=tmp_path / "data", runtime_cache_root=tmp_path / "cache"
    )
    paths = StoragePaths.from_settings(settings)
    engine = initialize_database(paths.database)
    app = create_app(
        settings=settings,
        engine=engine,
        secret_store=MemorySecretStore(),
        resolver=PublicResolver(),
        source_probe=ExplodingSourceProbe(),
    )
    client = TestClient(app, base_url=BASE_URL, raise_server_exceptions=True)
    try:
        with caplog.at_level(logging.ERROR):
            response = client.post(
                "/api/sources/probe",
                headers=csrf(client),
                json={"url": "https://youtu.be/abc"},
            )
        assert response.status_code == 500
        assert response.json() == {
            "error": {"code": "internal_error", "message": "internal server error", "details": None}
        }
        assert "super-secret" not in response.text
        assert "super-secret" not in caplog.text
    finally:
        engine.dispose()
