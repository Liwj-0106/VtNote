from __future__ import annotations

import hashlib
import json
import base64
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from vtnote.ai_stages import (
    NotesStageHandler,
    SnapshotBailianCredentialResolver,
    TranslationStageHandler,
    build_ai_stage_handlers,
)
from vtnote.artifacts import ensure_transcript_json
from vtnote.chat import (
    AiLimits,
    ChatError,
    ChatRequest,
    ChatResponse,
    ChatUsage,
    bailian_chat_endpoint,
)
from vtnote.config import Settings
from vtnote.configuration import ConfigurationService
from vtnote.database import initialize_database
from vtnote.models import (
    ItemRecord,
    ProcessorProfileRecord,
    ProviderConnectionRecord,
    StageRunRecord,
    TaskRecord,
)
from vtnote.paths import StoragePaths
from vtnote.provider_credentials import serialize_credential_bundle
from vtnote.schemas import (
    Provenance,
    ProvenanceMethod,
    Transcript,
    TranscriptSegment,
    Translation,
)
from vtnote.tasks import InvalidTaskOperation, TaskService
from vtnote.tokenhub_chat import TOKENHUB_BASE_URL, TOKENHUB_CHAT_ENDPOINT
from vtnote.url_security import SourceUrlPolicy
from vtnote.worker import StageContext, Worker
from vtnote.worker_store import WorkerStore
from vtnote.sensitive_text import ProtectedTextEnvelope


NOW = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
WORKSPACE_ID = "ws-123456"
MODEL = "qwen-plus"


class CountingSecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.get_calls: list[str] = []

    def get(self, reference: str) -> str | None:
        self.get_calls.append(reference)
        return self.values.get(reference)

    def set(self, reference: str, value: str) -> None:
        self.values[reference] = value

    def delete(self, reference: str) -> None:
        self.values.pop(reference, None)


class PublicResolver:
    def resolve(self, _host: str) -> list[str]:
        return ["93.184.216.34"]


class RecordingProtector:
    def __init__(self, plaintext: str) -> None:
        self.plaintext = plaintext
        self.calls: list[str] = []

    def protect(self, _purpose: str, _plaintext: str) -> ProtectedTextEnvelope:
        raise AssertionError("the stage must never protect an existing task prompt")

    def unprotect(
        self,
        purpose: str,
        _envelope: ProtectedTextEnvelope,
    ) -> str:
        self.calls.append(purpose)
        return self.plaintext


@dataclass
class ScriptedClient:
    purpose: str
    failure: Exception | None = None
    calls: list[ChatRequest] = field(default_factory=list)

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        if self.failure is not None:
            raise self.failure
        payload = json.loads(request.messages[1].content)
        if self.purpose == "translation":
            content = json.dumps(
                {
                    "translations": [
                        {
                            "cue_id": cue["cue_id"],
                            "text": f"译文-{cue['cue_id']}",
                        }
                        for cue in payload["cues"]
                    ]
                },
                ensure_ascii=False,
            )
        else:
            if payload["operation"] == "map":
                citation = {
                    key: payload["cues"][0][key]
                    for key in ("cue_id", "start_ms", "end_ms")
                }
            else:
                citation = payload["nodes"][0]["summary_citations"][0]
            content = json.dumps(
                {
                    "title": "学习笔记",
                    "summary": "这是综合总结。",
                    "summary_citations": [citation],
                    "key_points": [
                        {"text": "关键内容", "citations": [citation]}
                    ],
                },
                ensure_ascii=False,
            )
        return ChatResponse(
            content=content,
            requested_model=MODEL,
            actual_model=MODEL,
            finish_reason="stop",
            request_id="request-safe",
            usage=ChatUsage(
                prompt_tokens=10,
                completion_tokens=10,
                total_tokens=20,
            ),
        )


@dataclass
class ClientFactory:
    failures: dict[str, Exception] = field(default_factory=dict)
    clients: list[ScriptedClient] = field(default_factory=list)
    selected_profiles: list[dict[str, object]] = field(default_factory=list)

    def __call__(self, profile: Mapping[str, object], _credentials: object) -> ScriptedClient:
        purpose = profile.get("purpose")
        assert purpose in {"translation", "notes"}
        self.selected_profiles.append(dict(profile))
        client = ScriptedClient(str(purpose), self.failures.get(str(purpose)))
        self.clients.append(client)
        return client

    @property
    def network_calls(self) -> int:
        return sum(len(client.calls) for client in self.clients)


@dataclass(frozen=True)
class AiCase:
    paths: StoragePaths
    store: WorkerStore
    secrets: CountingSecretStore
    item_id: str
    task_id: str
    profiles: dict[str, dict[str, Any]]


def _paths(tmp_path: Path) -> StoragePaths:
    return StoragePaths.from_settings(
        Settings(
            data_root=tmp_path / "data",
            runtime_cache_root=tmp_path / "cache",
        )
    )


def _transcript() -> Transcript:
    return Transcript(
        language="en",
        duration_ms=2_000,
        provenance=Provenance(
            method=ProvenanceMethod.PLATFORM_SUBTITLE,
            provider="fixture",
            model=None,
        ),
        segments=(
            TranscriptSegment(
                id="seg_000001",
                start_ms=0,
                end_ms=1_000,
                text="First source cue.",
            ),
            TranscriptSegment(
                id="seg_000002",
                start_ms=1_000,
                end_ms=2_000,
                text="Second source cue.",
            ),
        ),
    )


def _fingerprint(
    *,
    connection_revision: int,
    profile_revision: int,
    endpoint: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "protocol": "aliyun_bailian",
        "endpoint": endpoint,
        "connection_revision": connection_revision,
        "profile_revision": profile_revision,
        "model": MODEL,
        "response_format": "json_object",
        "enable_thinking": False,
        "options": {"temperature": 0.2, "max_tokens": 4096},
    }


def _fingerprint_digest(value: Mapping[str, object]) -> str:
    body = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _make_case(
    tmp_path: Path,
    *,
    translation: bool = True,
    notes: bool = True,
) -> AiCase:
    paths = _paths(tmp_path)
    engine = initialize_database(paths.database)
    secrets = CountingSecretStore()
    credential_ref = "connection:test-bailian"
    secrets.set(
        credential_ref,
        serialize_credential_bundle(
            "aliyun_bailian",
            {"api_key": "sk-test-only"},
        ),
    )
    endpoint = bailian_chat_endpoint(WORKSPACE_ID)
    profiles: dict[str, dict[str, Any]] = {}
    with Session(engine) as session:
        connection = ProviderConnectionRecord(
            name="Bailian",
            protocol="aliyun_bailian",
            base_url=endpoint,
            parameters={"workspace_id": WORKSPACE_ID},
            credential_ref=credential_ref,
            revision=1,
        )
        session.add(connection)
        session.flush()
        for purpose in ("translation", "notes"):
            fingerprint = _fingerprint(
                connection_revision=1,
                profile_revision=1,
                endpoint=endpoint,
            )
            row = ProcessorProfileRecord(
                name=f"Bailian {purpose}",
                purpose=purpose,
                connection=connection,
                model=MODEL,
                context_length=32_768,
                options={
                    "temperature": 0.2,
                    "max_tokens": 4096,
                    "enable_thinking": False,
                },
                revision=1,
                test_ok=True,
                tested_revision=1,
                tested_connection_revision=1,
                capability_fingerprint_json=fingerprint,
                chat_data_authorized_fingerprint=_fingerprint_digest(fingerprint),
            )
            session.add(row)
            session.flush()
            profiles[purpose] = {
                "id": row.id,
                "connection_id": connection.id,
                "name": row.name,
                "purpose": purpose,
                "protocol": "aliyun_bailian",
                "base_url": endpoint,
                "parameters": {"workspace_id": WORKSPACE_ID},
                "connection_revision": 1,
                "model": MODEL,
                "context_length": 32_768,
                "options": {
                    "temperature": 0.2,
                    "max_tokens": 4096,
                    "enable_thinking": False,
                },
                "profile_revision": 1,
                "has_secret": True,
                "capability_fingerprint": fingerprint,
                "chat_data_consent_fingerprint": _fingerprint_digest(fingerprint),
            }
        task = TaskRecord(
            status="queued",
            pipeline_snapshot_json={
                "schema_version": 1,
                "translation": {
                    "enabled": translation,
                    "profile": profiles["translation"] if translation else None,
                    "target_language": "zh-Hans",
                },
                "notes": {
                    "enabled": notes,
                    "profile": profiles["notes"] if notes else None,
                    "template": "summary",
                    "output_language": "zh-Hans",
                    "custom_prompt_envelope": None,
                },
            },
        )
        item = ItemRecord(
            task=task,
            position=0,
            source_kind="local_subtitle",
            source_locator=str(tmp_path / "source.srt"),
            status="queued",
        )
        item.stage_runs = [
            StageRunRecord(
                stage="source",
                attempt=1,
                status="completed",
                finished_at=NOW,
            ),
            StageRunRecord(
                stage="transcribe",
                attempt=1,
                status="completed",
                finished_at=NOW,
            ),
            *(
                [StageRunRecord(stage="translate", attempt=1, status="queued")]
                if translation
                else []
            ),
            *(
                [StageRunRecord(stage="notes", attempt=1, status="queued")]
                if notes
                else []
            ),
        ]
        session.add(item)
        session.commit()
        item_id = item.id
        task_id = task.id
    ensure_transcript_json(paths, item_id, _transcript())
    return AiCase(
        paths=paths,
        store=WorkerStore(engine),
        secrets=secrets,
        item_id=item_id,
        task_id=task_id,
        profiles=profiles,
    )


def _handlers(case: AiCase, factory: ClientFactory) -> dict[str, object]:
    resolver = SnapshotBailianCredentialResolver(
        engine=case.store.engine,
        secrets=case.secrets,
    )
    return {
        "translate": TranslationStageHandler(
            paths=case.paths,
            credential_resolver=resolver,
            client_factory=factory,
            limits=AiLimits(),
        ),
        "notes": NotesStageHandler(
            paths=case.paths,
            credential_resolver=resolver,
            client_factory=factory,
            limits=AiLimits(),
        ),
    }


def _run_one(case: AiCase, handlers: Mapping[str, object], *, worker_id: str) -> None:
    checks = 0

    def stop_requested() -> bool:
        nonlocal checks
        checks += 1
        return checks > 1

    Worker(
        store=case.store,
        worker_id=worker_id,
        handlers=handlers,  # type: ignore[arg-type]
        lease_duration=timedelta(minutes=5),
        clock=lambda: NOW,
        stop_requested=stop_requested,
        sleeper=lambda _delay: None,
    ).run()


def _latest(case: AiCase, stage: str) -> StageRunRecord:
    with Session(case.store.engine) as session:
        return session.scalars(
            select(StageRunRecord)
            .where(
                StageRunRecord.item_id == case.item_id,
                StageRunRecord.stage == stage,
            )
            .order_by(StageRunRecord.attempt.desc())
        ).first()


def test_profile_snapshot_carries_exact_test_and_text_consent_fingerprints(
    tmp_path: Path,
) -> None:
    case = _make_case(tmp_path, notes=False)
    with Session(case.store.engine) as session:
        configuration = ConfigurationService(
            session,
            case.secrets,
            paths=case.paths,
        )
        snapshot = configuration.snapshot_profile(
            case.profiles["translation"]["id"]
        )

    assert snapshot["capability_fingerprint"] == case.profiles["translation"][
        "capability_fingerprint"
    ]
    assert snapshot["chat_data_consent_fingerprint"] == case.profiles[
        "translation"
    ]["chat_data_consent_fingerprint"]


def test_tokenhub_snapshot_validation_uses_connection_base_url(
    tmp_path: Path,
) -> None:
    case = _make_case(tmp_path, translation=False, notes=True)
    with Session(case.store.engine) as session:
        task = session.get(TaskRecord, case.task_id)
        assert task is not None
        snapshot = json.loads(json.dumps(task.pipeline_snapshot_json))
        profile_snapshot = snapshot["notes"]["profile"]
        connection = session.get(
            ProviderConnectionRecord,
            profile_snapshot["connection_id"],
        )
        profile = session.get(ProcessorProfileRecord, profile_snapshot["id"])
        assert connection is not None and profile is not None

        fingerprint = _fingerprint(
            connection_revision=1,
            profile_revision=1,
            endpoint=TOKENHUB_CHAT_ENDPOINT,
        )
        fingerprint["protocol"] = "tencent_tokenhub"
        fingerprint["model"] = "glm-5.1"
        digest = _fingerprint_digest(fingerprint)

        connection.protocol = "tencent_tokenhub"
        connection.base_url = TOKENHUB_BASE_URL
        connection.parameters = {}
        profile.model = "glm-5.1"
        profile.context_length = 200_000
        profile.capability_fingerprint_json = fingerprint
        profile.chat_data_authorized_fingerprint = digest
        profile_snapshot.update(
            {
                "protocol": "tencent_tokenhub",
                "base_url": TOKENHUB_BASE_URL,
                "parameters": {},
                "model": "glm-5.1",
                "context_length": 200_000,
                "capability_fingerprint": fingerprint,
                "chat_data_consent_fingerprint": digest,
            }
        )
        task.pipeline_snapshot_json = snapshot
        session.commit()

    validated = SnapshotBailianCredentialResolver(
        engine=case.store.engine,
        secrets=case.secrets,
    ).validate(profile_snapshot, purpose="notes")

    assert validated.protocol == "tencent_tokenhub"


@pytest.mark.parametrize("mutation", ["missing", "stale"])
def test_missing_or_stale_chat_consent_stops_before_secret_or_network(
    tmp_path: Path,
    mutation: str,
) -> None:
    case = _make_case(tmp_path, notes=False)
    with Session(case.store.engine) as session:
        task = session.get(TaskRecord, case.task_id)
        assert task is not None
        snapshot = json.loads(json.dumps(task.pipeline_snapshot_json))
        profile = snapshot["translation"]["profile"]
        if mutation == "missing":
            profile.pop("chat_data_consent_fingerprint")
        else:
            profile["chat_data_consent_fingerprint"] = "0" * 64
        task.pipeline_snapshot_json = snapshot
        session.commit()
    case.secrets.get_calls.clear()
    factory = ClientFactory()

    _run_one(case, _handlers(case, factory), worker_id=f"worker-{mutation}")

    stage = _latest(case, "translate")
    assert stage.status == "failed"
    assert stage.error_code == "chat_data_consent_stale"
    assert case.secrets.get_calls == []
    assert factory.network_calls == 0
    assert case.paths.transcript(case.item_id).is_file()


def test_translation_and_notes_complete_as_independent_parallel_branches(
    tmp_path: Path,
) -> None:
    case = _make_case(tmp_path)
    factory = ClientFactory()
    handlers = _handlers(case, factory)

    _run_one(case, handlers, worker_id="worker-translation")
    assert _latest(case, "translate").status == "completed"
    assert _latest(case, "notes").status == "queued"
    _run_one(case, handlers, worker_id="worker-notes")

    translated = Translation.model_validate_json(
        case.paths.translation(case.item_id, "zh-Hans").read_text("utf-8")
    )
    translated.validate_against(_transcript())
    note_path = case.paths.note(case.item_id, _latest(case, "notes").id)
    assert note_path.is_file()
    assert "AI 生成内容：请核对" not in note_path.read_text("utf-8")
    with Session(case.store.engine) as session:
        item = session.get(ItemRecord, case.item_id)
        assert item is not None
        assert item.status == "completed"
        assert [
            (row.stage, row.attempt)
            for row in item.stage_runs
            if row.stage in {"source", "transcribe"}
        ] == [("source", 1), ("transcribe", 1)]
    assert factory.network_calls == 2


def test_failed_translation_does_not_block_notes_or_hide_original(
    tmp_path: Path,
) -> None:
    case = _make_case(tmp_path)
    factory = ClientFactory(
        failures={"translation": ChatError("chat_rate_limited", retryable=True)}
    )
    handlers = _handlers(case, factory)

    _run_one(case, handlers, worker_id="worker-translation")
    _run_one(case, handlers, worker_id="worker-notes")

    assert _latest(case, "translate").status == "failed"
    assert _latest(case, "notes").status == "completed"
    assert case.paths.transcript(case.item_id).is_file()
    assert not case.paths.translation(case.item_id, "zh-Hans").exists()
    assert case.paths.note(case.item_id, _latest(case, "notes").id).is_file()
    with Session(case.store.engine) as session:
        item = session.get(ItemRecord, case.item_id)
        assert item is not None
        assert item.status == "completed_with_warnings"
        assert item.task.status == "completed_with_warnings"


def test_notes_retry_uses_the_explicit_profile_and_output_language(
    tmp_path: Path,
) -> None:
    case = _make_case(tmp_path, translation=False)
    with Session(case.store.engine) as session:
        configuration = ConfigurationService(
            session,
            case.secrets,
            paths=case.paths,
        )
        connection_id = case.profiles["notes"]["connection_id"]
        selected = configuration.create_profile(
            name="Retry notes",
            purpose="notes",
            connection_id=connection_id,
            model=MODEL,
            context_length=32_768,
            options={
                "temperature": 0.2,
                "max_tokens": 4096,
                "enable_thinking": False,
            },
        )
        configuration.record_profile_test(selected.id, ok=True, message="ok")
        configuration.authorize_chat_data(selected.id)
        item = session.get(ItemRecord, case.item_id)
        assert item is not None
        notes_run = next(run for run in item.stage_runs if run.stage == "notes")
        notes_run.status = "failed"
        item.status = "completed_with_warnings"
        item.task.status = "completed_with_warnings"
        session.commit()
        tasks = TaskService(
            session,
            configuration,
            case.paths,
            SourceUrlPolicy(PublicResolver()),
        )
        override = tasks.build_retry_override(
            notes_profile_id=selected.id,
            notes_profile_revision=selected.revision,
            notes_output_language="en",
        )
        tasks.retry_stage(
            case.item_id,
            "notes",
            expected_attempt=1,
            override=override,
        )

    factory = ClientFactory()
    _run_one(case, _handlers(case, factory), worker_id="worker-notes-retry")

    latest = _latest(case, "notes")
    assert latest.attempt == 2
    assert (latest.status, latest.error_code, latest.error_message) == (
        "completed",
        None,
        None,
    )
    assert factory.selected_profiles[-1]["id"] == selected.id
    payloads = [
        json.loads(request.messages[1].content)
        for client in factory.clients
        for request in client.calls
    ]
    assert payloads
    assert all(payload["output_language"] == "en" for payload in payloads)
    note = case.paths.note(case.item_id, latest.id).read_text("utf-8")
    assert "output_language: en" in note


def test_chat_submission_unknown_requires_charge_acknowledged_stage_only_retry(
    tmp_path: Path,
) -> None:
    case = _make_case(tmp_path, notes=False)
    failing_factory = ClientFactory(
        failures={
            "translation": ChatError(
                "chat_submission_unknown",
                submission_unknown=True,
            )
        }
    )
    _run_one(
        case,
        _handlers(case, failing_factory),
        worker_id="worker-unknown",
    )

    failed = _latest(case, "translate")
    assert failed.status == "failed"
    assert failed.external_submission_state == "submission_unknown"
    assert failed.warning == "chat_submission_unknown_possible_charge"
    assert failing_factory.network_calls == 1
    with Session(case.store.engine) as session:
        configuration = ConfigurationService(
            session,
            case.secrets,
            paths=case.paths,
        )
        tasks = TaskService(
            session,
            configuration,
            case.paths,
            SourceUrlPolicy(PublicResolver()),
        )
        with pytest.raises(
            InvalidTaskOperation,
            match="possible charge",
        ):
            tasks.retry_stage(
                case.item_id,
                "translate",
                expected_attempt=1,
                override=tasks.build_retry_override(strategy="same"),
            )
        override = tasks.build_retry_override(
            strategy="same",
            acknowledge_possible_charge=True,
        )
        tasks.retry_stage(
            case.item_id,
            "translate",
            expected_attempt=1,
            override=override,
            acknowledge_possible_charge=True,
        )
        stored_retry = session.scalars(
            select(StageRunRecord)
            .where(
                StageRunRecord.item_id == case.item_id,
                StageRunRecord.stage == "translate",
            )
            .order_by(StageRunRecord.attempt.desc())
        ).first()
        assert stored_retry is not None
        assert stored_retry.retry_override_json == {
            "schema_version": 1,
            "strategy": "same",
            "charge_acknowledged": True,
        }

    succeeding_factory = ClientFactory()
    _run_one(
        case,
        _handlers(case, succeeding_factory),
        worker_id="worker-retry",
    )
    assert _latest(case, "translate").attempt == 2
    assert _latest(case, "translate").status == "completed"
    with Session(case.store.engine) as session:
        item = session.get(ItemRecord, case.item_id)
        assert item is not None
        assert [
            (row.stage, row.attempt)
            for row in item.stage_runs
            if row.stage in {"source", "transcribe"}
        ] == [("source", 1), ("transcribe", 1)]


def test_custom_prompt_is_decrypted_only_inside_the_concrete_notes_attempt(
    tmp_path: Path,
) -> None:
    case = _make_case(tmp_path, translation=False)
    prompt = "只提炼课程里可以立即实践的动作。"
    protector = RecordingProtector(prompt)
    with Session(case.store.engine) as session:
        task = session.get(TaskRecord, case.task_id)
        assert task is not None
        snapshot = json.loads(json.dumps(task.pipeline_snapshot_json))
        snapshot["notes"]["template"] = "custom"
        snapshot["notes"]["custom_prompt_envelope"] = {
            "schema_version": 1,
            "protection": "windows_dpapi_current_user",
            "ciphertext_b64": base64.b64encode(b"protected").decode("ascii"),
        }
        task.pipeline_snapshot_json = snapshot
        session.commit()
    factory = ClientFactory()
    handlers = build_ai_stage_handlers(
        engine=case.store.engine,
        paths=case.paths,
        secrets=case.secrets,
        sensitive_text_protector=protector,
        client_factory=factory,
        limits=AiLimits(),
    )

    _run_one(case, handlers, worker_id="worker-custom-notes")

    assert protector.calls == [f"task:{case.task_id}:notes_custom_prompt"]
    assert factory.network_calls == 1
    request = factory.clients[0].calls[0]
    assert prompt not in request.messages[0].content
    assert prompt in request.messages[1].content
    markdown = case.paths.note(
        case.item_id,
        _latest(case, "notes").id,
    ).read_text("utf-8")
    assert prompt not in markdown


@pytest.mark.parametrize("stage", ["translate", "notes"])
def test_crash_after_artifact_publish_recovers_without_a_duplicate_paid_call(
    tmp_path: Path,
    stage: str,
) -> None:
    case = _make_case(
        tmp_path,
        translation=stage == "translate",
        notes=stage == "notes",
    )
    factory = ClientFactory()
    handlers = _handlers(case, factory)
    claim = case.store.claim_next(
        "worker-crash",
        NOW,
        timedelta(seconds=1),
    )
    assert claim is not None and claim.stage == stage
    result = handlers[stage].run(  # type: ignore[union-attr]
        StageContext(case.store, claim, lambda: NOW)
    )
    assert result.status == "completed"
    assert factory.network_calls == 1

    assert case.store.recover_expired(NOW + timedelta(seconds=2)) == (
        claim.stage_run_id,
    )
    recovered = case.store.claim_next(
        "worker-recovery",
        NOW + timedelta(seconds=2),
        timedelta(minutes=5),
    )
    assert recovered is not None
    recovered_result = handlers[stage].run(  # type: ignore[union-attr]
        StageContext(
            case.store,
            recovered,
            lambda: NOW + timedelta(seconds=2),
        )
    )
    assert case.store.complete(
        recovered,
        recovered_result,
        now=NOW + timedelta(seconds=3),
    )
    assert factory.network_calls == 1
