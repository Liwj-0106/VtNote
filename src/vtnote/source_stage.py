"""Durable subtitle-first source-stage orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session

from vtnote.artifacts import (
    ArtifactExistsError,
    ensure_transcript_json,
    transcript_from_source_subtitle,
    write_source_original,
)
from vtnote.media import MediaInfo
from vtnote.models import ItemRecord, RuntimeAssetRecord
from vtnote.paths import StoragePaths, UnsafePathError
from vtnote.runtime_assets import (
    RuntimeAssetError,
    RuntimeAssetService,
    RuntimeAssetView,
)
from vtnote.schemas import ProvenanceMethod
from vtnote.sources import (
    AudioOutcome,
    PlatformSourceError,
    SourceAdapter,
    SourceCapabilityError,
    SubtitleOutcome,
    SubtitleTrackSelector,
    acquire_subtitle_or_audio,
)
from vtnote.worker import StageCancelled, StageContext, StageExecutionError
from vtnote.worker_store import StageResult


class LocalSourceValidator(Protocol):
    def validate_media(self, path: Path) -> MediaInfo: ...

    def validate_subtitle(self, path: Path) -> None: ...


class SourceStageError(StageExecutionError):
    """A source-stage failure containing no source/provider free text."""

    _CODES = frozenset(
        {
            "adapter_drift",
            "adapter_unavailable",
            "audio_handoff_invalid",
            "auth_required",
            "invalid_content",
            "invalid_source_stage",
            "media_invalid",
            "platform_source_failed",
            "platform_source_invalid",
            "region_restricted",
            "removed",
            "runtime_asset_invalid",
            "source_not_found",
            "source_stage_failed",
            "subtitle_invalid",
            "subtitle_publication_failed",
            "temporary",
            "unsupported",
            "unsupported_source",
            "uploaded_source_invalid",
            "youtube_runtime_unavailable",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("invalid source stage error code")
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class _ItemSource:
    kind: str
    locator: str
    display_name: str | None
    output_type: str | None
    audio_export_enabled: bool


class _RecoverySourceAdapter:
    def __init__(
        self,
        *,
        delegate: SourceAdapter,
        context: StageContext,
        paths: StoragePaths,
        local_sources: LocalSourceValidator,
    ) -> None:
        self.delegate = delegate
        self.context = context
        self.paths = paths
        self.local_sources = local_sources

    def probe(self, canonical_source: str):
        return self.delegate.probe(canonical_source)

    def fetch_subtitle(self, probe, track):
        return self.delegate.fetch_subtitle(probe, track)

    def fetch_audio(self, probe, item_id: str) -> AudioOutcome:
        with Session(self.context.store.engine) as session:
            assets = RuntimeAssetService(session, self.paths)
            try:
                recovered = assets.recover_downloaded_audio(item_id=item_id)
                if recovered is not None:
                    path = assets.resolve(recovered.id)
                    info = self.local_sources.validate_media(path)
                    return AudioOutcome(
                        asset_id=recovered.id,
                        format=path.suffix.removeprefix(".").casefold(),
                        duration_ms=info.duration_ms,
                        size_bytes=info.size_bytes,
                    )
            except (OSError, ValueError, RuntimeAssetError, UnsafePathError):
                raise SourceStageError("audio_handoff_invalid") from None
        return self.delegate.fetch_audio(probe, item_id)


class SourceStageHandler:
    """Resolve one source and publish an immutable transcript or audio handoff."""

    def __init__(
        self,
        *,
        paths: StoragePaths,
        platform_source: SourceAdapter | None,
        local_sources: LocalSourceValidator,
        preferred_languages: tuple[str, ...],
    ) -> None:
        self.paths = paths
        self.platform_source = platform_source
        self.local_sources = local_sources
        self.selector = SubtitleTrackSelector(preferred_languages)

    @staticmethod
    def _load_item(context: StageContext) -> _ItemSource:
        if context.claim.stage != "source":
            raise SourceStageError("invalid_source_stage")
        with Session(context.store.engine) as session:
            item = session.get(ItemRecord, context.claim.item_id)
            if item is None:
                raise SourceStageError("source_not_found")
            output_type = item.task.options.get("output_type")
            if output_type not in {None, "audio", "transcript", "notes"}:
                raise SourceStageError("invalid_source_stage")
            audio_export_enabled = item.task.options.get(
                "audio_export_enabled", output_type == "audio"
            )
            if not isinstance(audio_export_enabled, bool):
                raise SourceStageError("invalid_source_stage")
            return _ItemSource(
                kind=item.source_kind,
                locator=item.source_locator,
                display_name=item.source_display_name,
                output_type=output_type,
                audio_export_enabled=audio_export_enabled,
            )

    def _uploaded(
        self,
        context: StageContext,
        locator: str,
        *,
        require_active: bool,
    ) -> tuple[RuntimeAssetView, Path]:
        with Session(context.store.engine) as session:
            assets = RuntimeAssetService(session, self.paths)
            try:
                row = session.get(RuntimeAssetRecord, locator)
                if (
                    row is None
                    or row.item_id != context.claim.item_id
                    or row.role != "uploaded_source"
                    or row.state not in {"active", "trash"}
                ):
                    raise SourceStageError("uploaded_source_invalid")
                if require_active and row.state == "trash":
                    view = assets.restore(row.id)
                else:
                    view = assets.get(row.id)
                return view, assets.resolve(view.id)
            except SourceStageError:
                raise
            except (OSError, ValueError, RuntimeAssetError, UnsafePathError):
                raise SourceStageError("uploaded_source_invalid") from None

    def _trash_asset(
        self,
        context: StageContext,
        asset_id: str,
    ) -> None:
        with Session(context.store.engine) as session:
            assets = RuntimeAssetService(session, self.paths)
            try:
                row = session.get(RuntimeAssetRecord, asset_id)
                if row is None:
                    raise SourceStageError("runtime_asset_invalid")
                if row.state == "active":
                    assets.trash(asset_id, now=context.clock())
                elif row.state != "trash":
                    raise SourceStageError("runtime_asset_invalid")
            except SourceStageError:
                raise
            except (OSError, ValueError, RuntimeAssetError, UnsafePathError):
                raise SourceStageError("runtime_asset_invalid") from None

    def _trash_stale_audio(self, context: StageContext) -> None:
        with Session(context.store.engine) as session:
            assets = RuntimeAssetService(session, self.paths)
            try:
                active = assets.active_for_role(
                    item_id=context.claim.item_id,
                    role="downloaded_audio",
                )
                if active is not None:
                    assets.trash(active.id, now=context.clock())
            except (OSError, ValueError, RuntimeAssetError, UnsafePathError):
                raise SourceStageError("runtime_asset_invalid") from None

    def _publish_subtitle(
        self,
        context: StageContext,
        *,
        extension: str,
        content: bytes,
        language: str,
        method: ProvenanceMethod,
        provider: str,
        duration_ms: int | None,
        title: str,
        evidence: dict[str, str],
        trash_asset_id: str | None = None,
        trash_stale_audio: bool = False,
    ) -> StageResult:
        try:
            transcript = transcript_from_source_subtitle(
                extension,
                content,
                language=language,
                method=method,
                provider=provider,
                duration_ms=duration_ms,
            )
            context.checkpoint()
            ensure_transcript_json(
                self.paths,
                context.claim.item_id,
                transcript,
            )
            context.checkpoint()
            write_source_original(
                self.paths,
                context.claim.item_id,
                extension,
                content,
            )
            context.checkpoint()
        except (ArtifactExistsError, OSError, ValueError, UnsafePathError):
            raise SourceStageError("subtitle_publication_failed") from None
        if trash_asset_id is not None:
            self._trash_asset(context, trash_asset_id)
        if trash_stale_audio:
            self._trash_stale_audio(context)
        return StageResult(
            item_title=title,
            execution_evidence=evidence,
            skip_stages=("transcribe",),
        )

    def _subtitle_file(
        self,
        context: StageContext,
        *,
        source: _ItemSource,
        path: Path,
        method: ProvenanceMethod,
        trash_asset_id: str | None,
    ) -> StageResult:
        try:
            self.local_sources.validate_subtitle(path)
            extension = path.suffix.removeprefix(".").casefold()
            content = path.read_bytes()
        except (OSError, ValueError):
            raise SourceStageError("subtitle_invalid") from None
        context.checkpoint()
        return self._publish_subtitle(
            context,
            extension=extension,
            content=content,
            language="und",
            method=method,
            provider="vtnote",
            duration_ms=None,
            title=source.display_name or path.name,
            evidence={"source_method": method.value},
            trash_asset_id=trash_asset_id,
        )

    def _media_file(
        self,
        context: StageContext,
        *,
        source: _ItemSource,
        path: Path,
    ) -> StageResult:
        try:
            self.local_sources.validate_media(path)
        except (OSError, ValueError):
            raise SourceStageError("media_invalid") from None
        context.checkpoint()
        return StageResult(item_title=source.display_name or path.name)

    def _platform(self, context: StageContext, source: _ItemSource) -> StageResult:
        if self.platform_source is None:
            raise SourceStageError("adapter_unavailable")
        try:
            probe = self.platform_source.probe(source.locator)
            context.checkpoint()
            recovering = _RecoverySourceAdapter(
                delegate=self.platform_source,
                context=context,
                paths=self.paths,
                local_sources=self.local_sources,
            )
            if source.output_type == "audio":
                outcome = recovering.fetch_audio(probe, context.claim.item_id)
                context.checkpoint()
                self._validate_audio_handoff(context, outcome)
                return StageResult(
                    item_title=probe.title,
                    execution_evidence={
                        "provider": probe.source_kind,
                        "source_method": "platform_audio",
                    },
                )
            if source.audio_export_enabled:
                retained_audio = recovering.fetch_audio(probe, context.claim.item_id)
                context.checkpoint()
                self._validate_audio_handoff(context, retained_audio)
            outcome = acquire_subtitle_or_audio(
                probe,
                recovering,
                self.selector,
                item_id=context.claim.item_id,
            )
            context.checkpoint()
        except SourceStageError:
            raise
        except StageCancelled:
            raise
        except (PlatformSourceError, SourceCapabilityError) as error:
            raise SourceStageError(error.code) from None
        except (OSError, ValueError, RuntimeAssetError, UnsafePathError):
            raise SourceStageError("platform_source_invalid") from None
        except Exception:
            raise SourceStageError("platform_source_failed") from None

        if isinstance(outcome, SubtitleOutcome):
            return self._publish_subtitle(
                context,
                extension=outcome.track.format,
                content=outcome.content,
                language=outcome.track.language,
                method=ProvenanceMethod.PLATFORM_SUBTITLE,
                provider=probe.source_kind,
                duration_ms=probe.duration_ms,
                title=probe.title,
                evidence={
                    "source_method": "platform_subtitle",
                    "selected_track_id": outcome.track.id,
                    "asr_route": "platform_subtitle",
                    "provider": probe.source_kind,
                },
                trash_stale_audio=not source.audio_export_enabled,
            )
        self._validate_audio_handoff(context, outcome)
        return StageResult(
            item_title=probe.title,
            execution_evidence={
                "provider": probe.source_kind,
                "fallback_reason": "platform_subtitle_unavailable",
            },
        )

    def _validate_audio_handoff(
        self,
        context: StageContext,
        outcome: AudioOutcome,
    ) -> None:
        with Session(context.store.engine) as session:
            row = session.get(RuntimeAssetRecord, outcome.asset_id)
            if (
                row is None
                or row.item_id != context.claim.item_id
                or row.role != "downloaded_audio"
                or row.state != "active"
            ):
                raise SourceStageError("audio_handoff_invalid")
            try:
                path = RuntimeAssetService(session, self.paths).resolve(row.id)
                info = self.local_sources.validate_media(path)
            except (OSError, ValueError, RuntimeAssetError, UnsafePathError):
                raise SourceStageError("audio_handoff_invalid") from None
            if (
                info.duration_ms != outcome.duration_ms
                or info.size_bytes != outcome.size_bytes
            ):
                raise SourceStageError("audio_handoff_invalid")

    def _run(self, context: StageContext) -> StageResult:
        context.checkpoint()
        source = self._load_item(context)
        if source.kind == "local_subtitle":
            return self._subtitle_file(
                context,
                source=source,
                path=Path(source.locator),
                method=ProvenanceMethod.LOCAL_SUBTITLE,
                trash_asset_id=None,
            )
        if source.kind == "local_media":
            return self._media_file(
                context,
                source=source,
                path=Path(source.locator),
            )
        if source.kind in {"uploaded_subtitle", "uploaded_media"}:
            asset, path = self._uploaded(
                context,
                source.locator,
                require_active=source.kind == "uploaded_media",
            )
            if source.kind == "uploaded_subtitle":
                return self._subtitle_file(
                    context,
                    source=source,
                    path=path,
                    method=ProvenanceMethod.UPLOADED_SUBTITLE,
                    trash_asset_id=asset.id,
                )
            return self._media_file(context, source=source, path=path)
        if source.kind == "url":
            return self._platform(context, source)
        raise SourceStageError("unsupported_source")

    def run(self, context: StageContext) -> StageResult:
        try:
            return self._run(context)
        except (SourceStageError, StageCancelled):
            raise
        except Exception:
            raise SourceStageError("source_stage_failed") from None
