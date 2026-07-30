"""Durable optional AI stage handlers bound to immutable domestic profiles."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from pydantic import SecretStr, ValidationError
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from vtnote.chat import (
    AiLimits,
    AliyunBailianChatAdapter,
    ChatClient,
    ChatError,
    ChatProfileSnapshot,
    bailian_chat_endpoint,
    validate_chat_model,
)
from vtnote.configuration import chat_capability_fingerprint_digest
from vtnote.models import (
    ItemRecord,
    ProcessorProfileRecord,
    ProviderConnectionRecord,
    StageRunRecord,
)
from vtnote.notes import NoteError, NoteGenerator
from vtnote.paths import StoragePaths
from vtnote.provider_credentials import (
    BailianCredentialBundle,
    CredentialReentryRequired,
    parse_credential_bundle,
)
from vtnote.schemas import Transcript, Translation, transcript_sha256
from vtnote.secrets import SecretStore
from vtnote.sensitive_text import (
    SensitiveTextProtector,
    task_prompt_purpose,
    validate_protected_text_envelope,
)
from vtnote.translation import TranslationError, Translator
from vtnote.worker import StageContext, StageExecutionError
from vtnote.worker_store import StageResult


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_TRANSCRIPT_BYTES = 32 * 1024 * 1024
_MAX_DERIVED_ARTIFACT_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ValidatedBailianProfile:
    profile_id: str
    connection_id: str
    connection_revision: int
    profile_revision: int
    credential_ref: str


class BailianCredentialResolver(Protocol):
    def validate(
        self,
        profile: Mapping[str, object],
        *,
        purpose: Literal["translation", "notes"],
    ) -> ValidatedBailianProfile: ...

    def resolve(
        self,
        validated: ValidatedBailianProfile,
    ) -> BailianCredentialBundle: ...


class AiClientFactory(Protocol):
    def __call__(
        self,
        profile: Mapping[str, object],
        credentials: BailianCredentialBundle,
    ) -> ChatClient: ...


class CustomPromptResolver(Protocol):
    def __call__(self, item_id: str, *, attempt: int) -> str | None: ...


def _canonical_uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return value if str(UUID(value)) == value else None
    except (ValueError, AttributeError):
        return None


def _current_fingerprint(
    profile: ProcessorProfileRecord,
    connection: ProviderConnectionRecord,
) -> dict[str, object]:
    options = dict(profile.options)
    return {
        "schema_version": 1,
        "protocol": "aliyun_bailian",
        "endpoint": bailian_chat_endpoint(connection.parameters["workspace_id"]),
        "connection_revision": connection.revision,
        "profile_revision": profile.revision,
        "model": profile.model,
        "response_format": "json_object",
        "enable_thinking": options["enable_thinking"],
        "options": {
            "temperature": options["temperature"],
            "max_tokens": options["max_tokens"],
        },
    }


class SnapshotBailianCredentialResolver:
    """Validate test/consent revisions before touching the secret store."""

    def __init__(self, *, engine: Engine, secrets: SecretStore) -> None:
        self.engine = engine
        self.secrets = secrets

    def validate(
        self,
        profile: Mapping[str, object],
        *,
        purpose: Literal["translation", "notes"],
    ) -> ValidatedBailianProfile:
        profile_id = _canonical_uuid(profile.get("id"))
        connection_id = _canonical_uuid(profile.get("connection_id"))
        profile_revision = profile.get("profile_revision")
        connection_revision = profile.get("connection_revision")
        parameters = profile.get("parameters")
        capability = profile.get("capability_fingerprint")
        consent = profile.get("chat_data_consent_fingerprint")
        if (
            profile_id is None
            or connection_id is None
            or type(profile_revision) is not int
            or type(connection_revision) is not int
            or profile.get("purpose") != purpose
            or profile.get("protocol") != "aliyun_bailian"
            or not isinstance(parameters, Mapping)
            or set(parameters) != {"workspace_id"}
            or not isinstance(parameters.get("workspace_id"), str)
        ):
            raise StageExecutionError("ai_profile_snapshot_invalid")
        endpoint = bailian_chat_endpoint(str(parameters["workspace_id"]))
        if profile.get("base_url") != endpoint:
            raise StageExecutionError("ai_profile_snapshot_invalid")
        if (
            not isinstance(capability, Mapping)
            or not isinstance(consent, str)
            or _DIGEST.fullmatch(consent) is None
            or chat_capability_fingerprint_digest(capability) != consent
        ):
            raise StageExecutionError("chat_data_consent_stale")

        with Session(self.engine) as session:
            profile_row = session.get(ProcessorProfileRecord, profile_id)
            connection = session.get(ProviderConnectionRecord, connection_id)
            if (
                profile_row is None
                or connection is None
                or profile_row.connection_id != connection.id
                or profile_row.purpose != purpose
                or connection.protocol != "aliyun_bailian"
                or profile_row.revision != profile_revision
                or connection.revision != connection_revision
                or profile_row.test_ok is not True
                or profile_row.tested_revision != profile_row.revision
                or profile_row.tested_connection_revision != connection.revision
                or dict(connection.parameters) != dict(parameters)
                or connection.base_url != endpoint
                or profile_row.model != profile.get("model")
                or profile_row.context_length != profile.get("context_length")
                or dict(profile_row.options) != dict(profile.get("options") or {})
            ):
                raise StageExecutionError("chat_data_consent_stale")
            try:
                current = _current_fingerprint(profile_row, connection)
            except (KeyError, TypeError, ValueError):
                raise StageExecutionError("chat_data_consent_stale") from None
            digest = chat_capability_fingerprint_digest(current)
            if (
                dict(capability) != current
                or dict(profile_row.capability_fingerprint_json or {}) != current
                or profile_row.chat_data_authorized_fingerprint != digest
                or consent != digest
            ):
                raise StageExecutionError("chat_data_consent_stale")
            return ValidatedBailianProfile(
                profile_id=profile_row.id,
                connection_id=connection.id,
                connection_revision=connection.revision,
                profile_revision=profile_row.revision,
                credential_ref=connection.credential_ref,
            )

    def resolve(
        self,
        validated: ValidatedBailianProfile,
    ) -> BailianCredentialBundle:
        with Session(self.engine) as session:
            profile = session.get(ProcessorProfileRecord, validated.profile_id)
            connection = session.get(
                ProviderConnectionRecord,
                validated.connection_id,
            )
            if (
                profile is None
                or connection is None
                or profile.connection_id != connection.id
                or profile.revision != validated.profile_revision
                or connection.revision != validated.connection_revision
                or connection.credential_ref != validated.credential_ref
            ):
                raise StageExecutionError("chat_data_consent_stale")
        stored = self.secrets.get(validated.credential_ref)
        if stored is None:
            raise StageExecutionError("chat_credentials_unavailable")
        try:
            bundle = parse_credential_bundle("aliyun_bailian", stored)
        except CredentialReentryRequired:
            raise StageExecutionError("chat_credentials_reentry_required") from None
        if not isinstance(bundle, BailianCredentialBundle):
            raise StageExecutionError("chat_credentials_unavailable")
        return bundle


class SnapshotCustomPromptResolver:
    def __init__(
        self,
        *,
        engine: Engine,
        protector: SensitiveTextProtector,
    ) -> None:
        self.engine = engine
        self.protector = protector

    def __call__(self, item_id: str, *, attempt: int) -> str | None:
        with Session(self.engine) as session:
            item = session.get(ItemRecord, item_id)
            if item is None:
                raise StageExecutionError("notes_input_unavailable")
            run = session.query(StageRunRecord).filter_by(
                item_id=item_id,
                stage="notes",
                attempt=attempt,
            ).one_or_none()
            if run is None:
                raise StageExecutionError("notes_input_unavailable")
            notes = item.task.pipeline_snapshot_json.get("notes")
            raw = (
                notes.get("custom_prompt_envelope")
                if isinstance(notes, Mapping)
                else None
            )
            if raw is None:
                return None
            envelope = validate_protected_text_envelope(raw)
            return self.protector.unprotect(
                task_prompt_purpose(item.task_id),
                envelope,
            )


def build_bailian_chat_client(
    profile: Mapping[str, object],
    credentials: BailianCredentialBundle,
) -> ChatClient:
    parameters = profile.get("parameters")
    workspace_id = (
        parameters.get("workspace_id")
        if isinstance(parameters, Mapping)
        else None
    )
    if not isinstance(workspace_id, str):
        raise StageExecutionError("ai_profile_snapshot_invalid")
    return AliyunBailianChatAdapter(
        workspace_id=workspace_id,
        api_key=SecretStr(credentials.api_key.get_secret_value()),
    )


@dataclass(frozen=True, slots=True)
class _AiInput:
    task_id: str
    section: Mapping[str, object]
    profile: Mapping[str, object]


def _load_ai_input(
    context: StageContext,
    *,
    stage: Literal["translate", "notes"],
) -> _AiInput:
    if context.claim.stage != stage:
        raise StageExecutionError("invalid_ai_stage")
    section_name = "translation" if stage == "translate" else "notes"
    with Session(context.store.engine) as session:
        item = session.get(ItemRecord, context.claim.item_id)
        if item is None or not isinstance(item.task.pipeline_snapshot_json, dict):
            raise StageExecutionError("ai_input_unavailable")
        section = item.task.pipeline_snapshot_json.get(section_name)
        if (
            not isinstance(section, Mapping)
            or section.get("enabled") is not True
            or not isinstance(section.get("profile"), Mapping)
        ):
            raise StageExecutionError("ai_stage_disabled")
        return _AiInput(
            task_id=item.task_id,
            section=dict(section),
            profile=dict(section["profile"]),
        )


def _read_transcript(paths: StoragePaths, item_id: str) -> Transcript:
    path = paths.transcript(item_id)
    try:
        if not path.is_file() or path.stat().st_size > _MAX_TRANSCRIPT_BYTES:
            raise ValueError
        return Transcript.model_validate_json(path.read_bytes())
    except (OSError, ValueError, ValidationError):
        raise StageExecutionError("transcript_artifact_invalid") from None


def _evidence(profile: Mapping[str, object]) -> dict[str, str]:
    model = profile.get("model")
    if not isinstance(model, str):
        raise StageExecutionError("ai_profile_snapshot_invalid")
    return {"provider": "aliyun_bailian", "model": model}


def _raise_ai_error(error: Exception) -> None:
    if isinstance(error, ChatError):
        if error.submission_unknown:
            raise StageExecutionError(
                "chat_submission_unknown",
                external_submission_state="submission_unknown",
                warning="chat_submission_unknown_possible_charge",
            ) from None
        raise StageExecutionError(error.code) from None
    if isinstance(error, (TranslationError, NoteError)):
        raise StageExecutionError(error.code) from None
    raise error


class TranslationStageHandler:
    def __init__(
        self,
        *,
        paths: StoragePaths,
        credential_resolver: BailianCredentialResolver,
        client_factory: AiClientFactory = build_bailian_chat_client,
        limits: AiLimits | None = None,
    ) -> None:
        self.paths = paths
        self.credential_resolver = credential_resolver
        self.client_factory = client_factory
        self.limits = limits or AiLimits()

    def run(self, context: StageContext) -> StageResult:
        selected = _load_ai_input(context, stage="translate")
        target_language = selected.section.get("target_language")
        if not isinstance(target_language, str):
            raise StageExecutionError("translation_target_language_invalid")
        validated = self.credential_resolver.validate(
            selected.profile,
            purpose="translation",
        )
        transcript = _read_transcript(self.paths, context.claim.item_id)
        artifact = self.paths.translation(context.claim.item_id, target_language)
        if artifact.exists():
            try:
                if artifact.stat().st_size > _MAX_DERIVED_ARTIFACT_BYTES:
                    raise ValueError
                translation = Translation.model_validate_json(artifact.read_bytes())
                translation.validate_against(transcript)
                if translation.language != target_language:
                    raise ValueError
            except (OSError, ValueError, ValidationError):
                raise StageExecutionError("translation_artifact_invalid") from None
            context.checkpoint()
            return StageResult(execution_evidence=_evidence(selected.profile))
        credentials = self.credential_resolver.resolve(validated)
        client = self.client_factory(selected.profile, credentials)
        translator = Translator(
            cancel_check=lambda: _cancellation_checkpoint(context)
        )
        try:
            translator.translate_and_write(
                transcript,
                target_language,
                ChatProfileSnapshot.from_mapping(selected.profile),
                client,
                self.limits,
                paths=self.paths,
                item_id=context.claim.item_id,
            )
        except (ChatError, TranslationError) as error:
            _raise_ai_error(error)
        return StageResult(execution_evidence=_evidence(selected.profile))


def _cancellation_checkpoint(context: StageContext) -> bool:
    context.checkpoint()
    return False


def _note_metadata(path: Path) -> dict[str, str]:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_DERIVED_ARTIFACT_BYTES:
            raise ValueError
        lines = path.read_text("utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        raise ValueError("invalid note artifact") from None
    if len(lines) < 10 or lines[0] != "---":
        raise ValueError("invalid note artifact")
    try:
        closing = lines.index("---", 1)
    except ValueError:
        raise ValueError("invalid note artifact") from None
    metadata: dict[str, str] = {}
    for line in lines[1:closing]:
        key, separator, value = line.partition(": ")
        if not separator or not key or key in metadata:
            raise ValueError("invalid note artifact")
        metadata[key] = value
    if set(metadata) != {
        "generated_by_ai",
        "task_id",
        "transcript_sha256",
        "template",
        "output_language",
        "requested_model",
        "response_model",
    } or metadata["generated_by_ai"] != "true":
        raise ValueError("invalid note artifact")
    validate_chat_model(metadata["requested_model"])
    validate_chat_model(metadata["response_model"])
    return metadata


class NotesStageHandler:
    def __init__(
        self,
        *,
        paths: StoragePaths,
        credential_resolver: BailianCredentialResolver,
        client_factory: AiClientFactory = build_bailian_chat_client,
        custom_prompt_resolver: CustomPromptResolver | None = None,
        limits: AiLimits | None = None,
    ) -> None:
        self.paths = paths
        self.credential_resolver = credential_resolver
        self.client_factory = client_factory
        self.custom_prompt_resolver = custom_prompt_resolver
        self.limits = limits or AiLimits()

    def run(self, context: StageContext) -> StageResult:
        selected = _load_ai_input(context, stage="notes")
        template = selected.section.get("template")
        output_language = selected.section.get("output_language")
        if template not in {"summary", "key_points", "custom"}:
            raise StageExecutionError("note_template_invalid")
        if not isinstance(output_language, str):
            raise StageExecutionError("note_output_language_invalid")
        validated = self.credential_resolver.validate(
            selected.profile,
            purpose="notes",
        )
        transcript = _read_transcript(self.paths, context.claim.item_id)
        artifact = self.paths.note(
            context.claim.item_id,
            context.claim.stage_run_id,
        )
        if artifact.exists():
            try:
                metadata = _note_metadata(artifact)
                if (
                    metadata["task_id"] != selected.task_id
                    or metadata["transcript_sha256"] != transcript_sha256(transcript)
                    or metadata["template"] != template
                    or metadata["output_language"] != output_language
                    or metadata["requested_model"] != selected.profile.get("model")
                ):
                    raise ValueError
            except ValueError:
                raise StageExecutionError("note_artifact_invalid") from None
            context.checkpoint()
            return StageResult(execution_evidence=_evidence(selected.profile))
        credentials = self.credential_resolver.resolve(validated)
        client = self.client_factory(selected.profile, credentials)
        custom_prompt = None
        if template == "custom":
            if self.custom_prompt_resolver is None:
                raise StageExecutionError("note_custom_prompt_unavailable")
            custom_prompt = self.custom_prompt_resolver(
                context.claim.item_id,
                attempt=context.claim.attempt,
            )
        generator = NoteGenerator(
            task_id=selected.task_id,
            cancel_check=lambda: _cancellation_checkpoint(context),
        )
        try:
            generator.generate_and_write(
                transcript,
                ChatProfileSnapshot.from_mapping(selected.profile),
                client,
                template=template,
                output_language=output_language,
                custom_prompt=custom_prompt,
                limits=self.limits,
                paths=self.paths,
                item_id=context.claim.item_id,
                note_id=context.claim.stage_run_id,
            )
        except (ChatError, NoteError) as error:
            _raise_ai_error(error)
        return StageResult(execution_evidence=_evidence(selected.profile))


def build_ai_stage_handlers(
    *,
    engine: Engine,
    paths: StoragePaths,
    secrets: SecretStore,
    sensitive_text_protector: SensitiveTextProtector,
    client_factory: AiClientFactory = build_bailian_chat_client,
    limits: AiLimits | None = None,
) -> dict[str, TranslationStageHandler | NotesStageHandler]:
    """Build the production pair with one revision/consent gate."""

    resolver = SnapshotBailianCredentialResolver(
        engine=engine,
        secrets=secrets,
    )
    selected_limits = limits or AiLimits()
    return {
        "translate": TranslationStageHandler(
            paths=paths,
            credential_resolver=resolver,
            client_factory=client_factory,
            limits=selected_limits,
        ),
        "notes": NotesStageHandler(
            paths=paths,
            credential_resolver=resolver,
            client_factory=client_factory,
            custom_prompt_resolver=SnapshotCustomPromptResolver(
                engine=engine,
                protector=sensitive_text_protector,
            ),
            limits=selected_limits,
        ),
    }
