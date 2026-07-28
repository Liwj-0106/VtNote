from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from vtnote.config import Settings
from vtnote import pipeline as pipeline_contract
from vtnote.configuration import ConfigurationService, InvalidConfiguration
from vtnote.database import initialize_database
from vtnote.models import ItemRecord
from vtnote.paths import StoragePaths
from vtnote.pipeline import (
    RETRY_ACTIVE_CONFLICTS,
    STAGE_DEPENDENCIES,
    aggregate_item_status,
    aggregate_task_status,
)
from vtnote.secrets import MemorySecretStore
from vtnote.tasks import TaskService
from vtnote.url_security import SourceUrlPolicy


class PublicResolver:
    def resolve(self, host: str) -> list[str]:
        return ["142.250.72.14"]


def test_shared_pipeline_keeps_translation_and_notes_as_parallel_branches() -> None:
    assert STAGE_DEPENDENCIES == {
        "source": frozenset(),
        "transcribe": frozenset({"source"}),
        "translate": frozenset({"transcribe"}),
        "notes": frozenset({"transcribe"}),
    }
    assert "notes" not in RETRY_ACTIVE_CONFLICTS["translate"]
    assert "translate" not in RETRY_ACTIVE_CONFLICTS["notes"]


def test_shared_pipeline_aggregates_core_and_optional_stage_outcomes() -> None:
    core_success = {"source": "completed", "transcribe": "completed"}

    assert aggregate_item_status(core_success) == "completed"
    assert aggregate_item_status(
        {**core_success, "translate": "failed", "notes": "completed"}
    ) == "completed_with_warnings"
    assert aggregate_item_status(
        {**core_success, "translate": "completed", "notes": "running"}
    ) == "running"
    assert aggregate_item_status(
        {"source": "completed", "transcribe": "failed"}
    ) == "failed"
    assert aggregate_task_status(["completed", "completed_with_warnings"]) == (
        "completed_with_warnings"
    )


@pytest.mark.parametrize(
    "progress",
    [
        {"current": True, "total": 10, "unit": "items", "message_code": "working"},
        {"current": -1, "total": 10, "unit": "items", "message_code": "working"},
        {"current": 11, "total": 10, "unit": "items", "message_code": "working"},
        {"current": 0, "total": 0, "unit": "items", "message_code": "working"},
        {
            "current": 1 << 53,
            "total": 1 << 53,
            "unit": "bytes",
            "message_code": "working",
        },
        {"current": 1, "total": 10, "unit": ["items"], "message_code": "working"},
        {"current": 1, "total": 10, "unit": "seconds", "message_code": "working"},
        {"current": 1, "total": 10, "unit": "items", "message_code": "Not free text"},
        {
            "current": 1,
            "total": 10,
            "unit": "items",
            "message_code": "abcdef0123456789abcdef0123456789",
        },
        {
            "current": 1,
            "total": 10,
            "unit": "items",
            "message_code": "working",
            "provider_message": "raw",
        },
    ],
)
def test_stage_progress_contract_rejects_invalid_shapes(
    progress: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        pipeline_contract.validate_stage_progress(progress)


@pytest.mark.parametrize(
    "evidence",
    [
        {"provider_message": "raw provider response"},
        {"provider": {"name": "tencent"}},
        {"provider": "tencent\nAuthorization"},
        {"provider": "AKIDEXAMPLE1234567890"},
        {"provider": "b4cb2d8d-1a37-44c7-8fa1-bb4b23967c39"},
        {"model": "b4cb2d8d-1a37-44c7-8fa1-bb4b23967c39"},
        {"fallback_reason": "human readable free text"},
        {"fallback_reason": "abcdef0123456789abcdef0123456789"},
        {"selected_track_id": "subtitle-1"},
    ],
)
def test_execution_evidence_contract_rejects_unknown_or_free_text(
    evidence: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        pipeline_contract.validate_execution_evidence(evidence)


def test_execution_evidence_accepts_registered_fallback_reason() -> None:
    assert pipeline_contract.validate_execution_evidence(
        {"fallback_reason": "cloud_rate_limited"}
    ) == {"fallback_reason": "cloud_rate_limited"}


@pytest.mark.parametrize(
    "status_code",
    [
        123,
        True,
        {"code": "waiting"},
        "18446744073709551615",
        "b4cb2d8d-1a37-44c7-8fa1-bb4b23967c39",
        "AKIDEXAMPLE1234567890",
    ],
)
def test_provider_status_contract_rejects_unknown_or_non_string_values(
    status_code: object,
) -> None:
    with pytest.raises(ValueError):
        pipeline_contract.validate_provider_status_code(status_code)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (
            {
                "source": "failed",
                "transcribe": "queued",
                "translate": "queued",
                "notes": "queued",
            },
            "failed",
        ),
        (
            {
                "source": "completed",
                "transcribe": "canceled",
                "translate": "queued",
                "notes": "queued",
            },
            "canceled",
        ),
    ],
)
def test_core_terminal_outcome_dominates_queued_dependents(
    statuses: dict[str, str], expected: str
) -> None:
    assert aggregate_item_status(statuses) == expected


def make_services(tmp_path: Path):
    paths = StoragePaths.from_settings(
        Settings(data_root=tmp_path / "data", runtime_cache_root=tmp_path / "cache")
    )
    engine = initialize_database(paths.database)
    session = Session(engine)
    configuration = ConfigurationService(session, MemorySecretStore(), paths=paths)
    tasks = TaskService(session, configuration, paths, SourceUrlPolicy(PublicResolver()))
    return configuration, tasks, session, paths


def test_fixed_local_whisper_defaults_use_d_drive_roots(tmp_path: Path) -> None:
    configuration, _, session, paths = make_services(tmp_path)
    try:
        local = configuration.get_defaults().local_whisper_options
        assert local == {
            "model": "large-v3-turbo",
            "device": "auto",
            "compute_type": "int8_float16",
            "vad_filter": True,
            "model_root": str(paths.durable("models", "faster-whisper")),
            "cache_root": str(paths.runtime("models", "faster-whisper")),
        }
        assert Path(local["model_root"]).drive.casefold() == "d:"
        assert Path(local["cache_root"]).drive.casefold() == "d:"
        with pytest.raises(InvalidConfiguration, match="configured D-drive"):
            configuration.update_defaults(
                local_whisper_options={"model_root": r"C:\models"}
            )
    finally:
        session.close()
        session.bind.dispose()


def test_profile_context_length_is_positive_and_options_are_purpose_specific(tmp_path: Path) -> None:
    configuration, _, session, _ = make_services(tmp_path)
    try:
        cloud = configuration.create_connection(
            name="Cloud", protocol="volc_bigasr_flash",
            base_url="https://openspeech.bytedance.com", parameters={}, secret="key"
        )
        chat = configuration.create_connection(
            name="Chat", protocol="openai_compatible",
            base_url="https://api.example.com/v1", parameters={}, secret="key"
        )
        with pytest.raises(InvalidConfiguration, match="context_length"):
            configuration.create_profile(
                name="Bad context", purpose="notes", connection_id=chat.id,
                model="gpt", context_length=0
            )
        with pytest.raises(InvalidConfiguration, match="profile option"):
            configuration.create_profile(
                name="Bad cloud", purpose="cloud_asr", connection_id=cloud.id,
                model="bigmodel", context_length=8192, options={"temperature": 0.2}
            )
        with pytest.raises(InvalidConfiguration, match="profile option"):
            configuration.create_profile(
                name="Bad notes", purpose="notes", connection_id=chat.id,
                model="gpt", context_length=8192, options={"language": "zh-CN"}
            )
        valid = configuration.create_profile(
            name="Notes", purpose="notes", connection_id=chat.id,
            model="gpt", context_length=32768, options={"temperature": 0.2}
        )
        assert valid.context_length == 32768
        assert "context" not in valid.model_dump()
    finally:
        session.close()
        session.bind.dispose()


def test_volc_resource_is_fixed_and_not_caller_configurable(tmp_path: Path) -> None:
    configuration, tasks, session, _ = make_services(tmp_path)
    try:
        with pytest.raises(InvalidConfiguration, match="parameter"):
            configuration.create_connection(
                name="Bad Cloud", protocol="volc_bigasr_flash",
                base_url="https://openspeech.bytedance.com",
                parameters={"resource_id": "attacker.resource"}, secret="key"
            )
        connection = configuration.create_connection(
            name="Cloud", protocol="volc_bigasr_flash",
            base_url="https://openspeech.bytedance.com", parameters={}, secret="key"
        )
        profile = configuration.create_profile(
            name="Flash", purpose="cloud_asr", connection_id=connection.id,
            model="bigmodel", context_length=8192, options={"language": "zh-CN"}
        )
        configuration.record_profile_test(profile.id, ok=True, message="ok")
        configuration.authorize_cloud_upload(profile.id)
        configuration.update_defaults(asr_mode="cloud", cloud_asr_profile_id=profile.id)
        created = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
        )
        assert created.pipeline_snapshot["asr"]["profile"]["resource"] == (
            "volc.bigasr.auc_turbo"
        )
        local_only = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/local"}],
            options={"asr_mode": "local"},
        )
        assert local_only.pipeline_snapshot["asr"]["profile"] is None
    finally:
        session.close()
        session.bind.dispose()


def test_task_overrides_snapshot_all_pipeline_choices_and_fixed_item_path(tmp_path: Path) -> None:
    configuration, tasks, session, _, = make_services(tmp_path)
    try:
        chat = configuration.create_connection(
            name="Chat", protocol="openai_compatible",
            base_url="https://api.example.com/v1", parameters={}, secret="key"
        )
        translation = configuration.create_profile(
            name="Translation", purpose="translation", connection_id=chat.id,
            model="translate", context_length=16384, options={"temperature": 0.1}
        )
        notes = configuration.create_profile(
            name="Notes", purpose="notes", connection_id=chat.id,
            model="notes", context_length=32768, options={"max_tokens": 2048}
        )
        configuration.record_profile_test(translation.id, ok=True, message="ok")
        configuration.record_profile_test(notes.id, ok=True, message="ok")

        task = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}],
            options={
                "asr_mode": "local",
                "translation_enabled": True,
                "translation_profile_id": translation.id,
                "translation_target_language": "ja",
                "notes_enabled": True,
                "notes_profile_id": notes.id,
                "notes_template": "custom",
                "notes_output_language": "zh-Hans",
                "notes_custom_prompt": "Use headings",
            },
        )
        snapshot = task.pipeline_snapshot
        assert snapshot["schema_version"] == 1
        assert snapshot["asr"]["mode"] == "local"
        assert snapshot["translation"]["target_language"] == "ja"
        assert snapshot["notes"] == {
            "enabled": True,
            "profile": snapshot["notes"]["profile"],
            "template": "custom",
            "output_language": "zh-Hans",
            "has_custom_prompt": True,
        }
        stored_item = session.get(ItemRecord, task.items[0].id)
        assert stored_item is not None
        assert stored_item.artifact_relpath == f"items/{stored_item.id}"
    finally:
        session.close()
        session.bind.dispose()


def test_stale_cloud_falls_back_in_auto_but_forced_cloud_fails(tmp_path: Path) -> None:
    configuration, tasks, session, _ = make_services(tmp_path)
    try:
        connection = configuration.create_connection(
            name="Cloud", protocol="volc_bigasr_flash",
            base_url="https://openspeech.bytedance.com", parameters={}, secret="key"
        )
        profile = configuration.create_profile(
            name="Flash", purpose="cloud_asr", connection_id=connection.id,
            model="bigmodel", context_length=8192
        )
        configuration.record_profile_test(profile.id, ok=True, message="ok")
        configuration.authorize_cloud_upload(profile.id)
        configuration.update_defaults(asr_mode="auto", cloud_asr_profile_id=profile.id)
        configuration.update_connection(
            connection.id, base_url="https://openspeech-revised.bytedance.com"
        )

        automatic = tasks.create_task(
            sources=[{"kind": "url", "locator": "https://youtu.be/abc"}]
        )
        assert automatic.pipeline_snapshot["asr"]["profile"] is None

        with pytest.raises(InvalidConfiguration, match="cloud ASR"):
            tasks.create_task(
                sources=[{"kind": "url", "locator": "https://youtu.be/def"}],
                options={"asr_mode": "cloud", "cloud_asr_profile_id": profile.id},
            )
    finally:
        session.close()
        session.bind.dispose()


def test_names_are_unique_case_insensitively(tmp_path: Path) -> None:
    configuration, _, session, _ = make_services(tmp_path)
    try:
        configuration.create_connection(
            name="Chat", protocol="openai_compatible",
            base_url="https://api.example.com/v1", parameters={}
        )
        with pytest.raises(InvalidConfiguration, match="name"):
            configuration.create_connection(
                name="chat", protocol="openai_compatible",
                base_url="https://other.example.com/v1", parameters={}
            )
    finally:
        session.close()
        session.bind.dispose()
