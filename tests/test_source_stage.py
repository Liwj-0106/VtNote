from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

import vtnote.source_stage as source_stage_module
from vtnote.artifacts import (
    transcript_from_source_subtitle,
    validate_source_subtitle,
)
from vtnote.database import initialize_database
from vtnote.media import MediaInfo
from vtnote.models import ItemRecord, RuntimeAssetRecord, StageRunRecord, TaskRecord
from vtnote.paths import StoragePaths
from vtnote.runtime_assets import RuntimeAssetService
from vtnote.schemas import ProvenanceMethod
from vtnote.source_stage import SourceStageError, SourceStageHandler
from vtnote.sources import (
    AudioOutcome,
    PlatformSourceError,
    SourceProbeResult,
    SubtitleCandidateError,
    SubtitleOutcome,
    SubtitleTrack,
    make_subtitle_track,
)
from vtnote.worker import StageCancelled, StageContext, Worker
from vtnote.worker_store import StageResult, WorkerStore


NOW = datetime(2026, 7, 29, 14, 0, tzinfo=timezone.utc)
ITEM_ID = "22222222-2222-4222-8222-222222222222"
VALID_SRT = (
    b"1\n00:00:00,000 --> 00:00:01,250\nhello\n\n"
    b"2\n00:00:02,000 --> 00:00:03,500\nworld\n"
)
VALID_VTT = b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello\n"


def media_info(path: Path) -> MediaInfo:
    return MediaInfo(
        duration_ms=3_500,
        size_bytes=path.stat().st_size,
        format_name="webm",
        audio_codec="opus",
        sample_rate=48_000,
        channels=2,
    )


class FakeLocalSources:
    def __init__(self) -> None:
        self.media_calls: list[Path] = []
        self.subtitle_calls: list[Path] = []

    def validate_media(self, path: Path) -> MediaInfo:
        self.media_calls.append(path)
        return media_info(path)

    def validate_subtitle(self, path: Path) -> None:
        self.subtitle_calls.append(path)
        validate_source_subtitle(
            path.suffix.removeprefix(".").casefold(),
            path.read_bytes(),
        )


@dataclass
class FakePlatformSource:
    probe_result: SourceProbeResult
    subtitle_results: dict[str, bytes | Exception]
    engine: Engine
    paths: StoragePaths
    audio_calls: int = 0
    subtitle_calls: list[str] = field(default_factory=list)
    after_subtitle: Callable[[], None] | None = None

    def probe(self, _: str) -> SourceProbeResult:
        return self.probe_result

    def fetch_subtitle(
        self,
        _: SourceProbeResult,
        track: SubtitleTrack,
    ) -> SubtitleOutcome:
        self.subtitle_calls.append(track.id)
        result = self.subtitle_results[track.id]
        if isinstance(result, Exception):
            raise result
        if self.after_subtitle is not None:
            self.after_subtitle()
        return SubtitleOutcome(track, result)

    def fetch_audio(
        self,
        _: SourceProbeResult,
        item_id: str,
    ) -> AudioOutcome:
        self.audio_calls += 1
        destination = self.paths.downloaded_audio(item_id, "webm")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"owned platform audio")
        with Session(self.engine) as session:
            view = RuntimeAssetService(session, self.paths).register_staged(
                item_id=item_id,
                role="downloaded_audio",
                relative_path=self.paths.runtime_relative(destination),
            )
        return AudioOutcome(
            asset_id=view.id,
            format="webm",
            duration_ms=3_500,
            size_bytes=destination.stat().st_size,
        )


def track(
    ordinal: int,
    *,
    language: str = "zh-Hans",
    kind: str = "manual",
    format: str = "vtt",
) -> SubtitleTrack:
    return make_subtitle_track(
        source_kind="youtube",
        language=language,
        format=format,
        kind=kind,  # type: ignore[arg-type]
        stable_ordinal=ordinal,
    )


def youtube_probe(*tracks: SubtitleTrack) -> SourceProbeResult:
    return SourceProbeResult(
        source_kind="youtube",
        canonical_url="https://www.youtube.com/watch?v=abcDEF12345",
        title="Safe platform title",
        duration_ms=3_500,
        subtitle_tracks=tuple(tracks),
    )


def seed(
    tmp_path: Path,
    *,
    source_kind: str,
    source_locator: str,
    display_name: str | None = None,
) -> tuple[Engine, StoragePaths, WorkerStore, StageContext]:
    paths = StoragePaths(
        data_root=tmp_path / "data",
        runtime_cache_root=tmp_path / "runtime",
    )
    engine = initialize_database(paths.database)
    with Session(engine) as session:
        item = ItemRecord(
            id=ITEM_ID,
            task=TaskRecord(
                status="queued",
                options={},
                pipeline_snapshot_json={},
                created_at=NOW,
            ),
            position=0,
            source_kind=source_kind,
            source_locator=source_locator,
            source_display_name=display_name,
            status="queued",
            artifact_relpath=f"items/{ITEM_ID}",
        )
        item.stage_runs = [
            StageRunRecord(stage="source", attempt=1, status="queued"),
            StageRunRecord(stage="transcribe", attempt=1, status="queued"),
        ]
        session.add(item)
        session.commit()
    store = WorkerStore(engine, process_id=1)
    claim = store.claim_next("source-worker", NOW, timedelta(seconds=60))
    assert claim is not None and claim.stage == "source"
    return (
        engine,
        paths,
        store,
        StageContext(store=store, claim=claim, clock=lambda: NOW),
    )


def handler(
    *,
    paths: StoragePaths,
    platform: FakePlatformSource | None = None,
    local: FakeLocalSources | None = None,
) -> SourceStageHandler:
    return SourceStageHandler(
        paths=paths,
        platform_source=platform,
        local_sources=local or FakeLocalSources(),
        preferred_languages=("zh-Hans", "en"),
    )


def stage_status(engine: Engine, stage: str) -> str:
    with Session(engine) as session:
        return session.scalar(
            select(StageRunRecord.status).where(
                StageRunRecord.item_id == ITEM_ID,
                StageRunRecord.stage == stage,
            )
        )  # type: ignore[return-value]


def test_local_subtitle_publishes_immutable_artifacts_and_skips_transcribe(
    tmp_path: Path,
) -> None:
    source = (tmp_path / "user-owned.srt").absolute()
    source.write_bytes(VALID_SRT)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    engine, paths, store, context = seed(
        tmp_path,
        source_kind="local_subtitle",
        source_locator=str(source),
    )
    local = FakeLocalSources()
    try:
        result = handler(paths=paths, local=local).run(context)
        assert result.skip_stages == ("transcribe",)
        assert result.execution_evidence == {"source_method": "local_subtitle"}
        assert store.complete(context.claim, result, now=NOW)

        assert paths.source_original(ITEM_ID, "srt").read_bytes() == VALID_SRT
        transcript = paths.transcript(ITEM_ID).read_text(encoding="utf-8")
        assert '"language":"und"' in transcript
        assert '"method":"local_subtitle"' in transcript
        assert '"text":"hello"' in transcript
        assert stage_status(engine, "transcribe") == "skipped"
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
        assert source.is_file()
    finally:
        engine.dispose()


def test_bilibili_json_seconds_normalize_to_canonical_milliseconds() -> None:
    transcript = transcript_from_source_subtitle(
        "json",
        (
            b'{"body":[{"from":0.25,"to":1.75,'
            b'"content":"caption"}]}'
        ),
        language="zh-Hans",
        method=ProvenanceMethod.PLATFORM_SUBTITLE,
        provider="bilibili",
    )

    assert transcript.duration_ms == 1750
    assert [
        (segment.id, segment.start_ms, segment.end_ms, segment.text)
        for segment in transcript.segments
    ] == [("seg_000001", 250, 1750, "caption")]


def test_uploaded_subtitle_is_trashed_only_after_durable_publication(
    tmp_path: Path,
) -> None:
    engine, paths, store, context = seed(
        tmp_path,
        source_kind="uploaded_subtitle",
        source_locator="00000000-0000-4000-8000-000000000000",
        display_name="captions.srt",
    )
    uploaded = paths.uploaded_source(ITEM_ID, "srt")
    uploaded.parent.mkdir(parents=True, exist_ok=True)
    uploaded.write_bytes(VALID_SRT)
    with Session(engine) as session:
        asset = RuntimeAssetService(session, paths).register_staged(
            item_id=ITEM_ID,
            role="uploaded_source",
            relative_path=paths.runtime_relative(uploaded),
        )
        item = session.get(ItemRecord, ITEM_ID)
        assert item is not None
        item.source_locator = asset.id
        session.commit()
        asset_id = asset.id
    try:
        result = handler(paths=paths).run(context)
        assert store.complete(context.claim, result, now=NOW)
        assert paths.transcript(ITEM_ID).is_file()
        assert paths.source_original(ITEM_ID, "srt").read_bytes() == VALID_SRT
        with Session(engine) as session:
            stored = session.get(RuntimeAssetRecord, asset_id)
            assert stored is not None
            assert stored.state == "trash"
            assert stored.purge_after == NOW + timedelta(hours=24)
            assert RuntimeAssetService(session, paths).resolve(asset_id).is_file()
    finally:
        engine.dispose()


@pytest.mark.parametrize("source_kind", ["local_media", "uploaded_media"])
def test_media_handoff_keeps_transcribe_queued_and_publishes_no_subtitle(
    tmp_path: Path,
    source_kind: str,
) -> None:
    media = (tmp_path / "user-owned.webm").absolute()
    media.write_bytes(b"media")
    engine, paths, store, context = seed(
        tmp_path,
        source_kind=source_kind,
        source_locator=str(media),
        display_name="video.webm",
    )
    asset_id: str | None = None
    if source_kind == "uploaded_media":
        uploaded = paths.uploaded_source(ITEM_ID, "webm")
        uploaded.parent.mkdir(parents=True, exist_ok=True)
        uploaded.write_bytes(b"media")
        with Session(engine) as session:
            asset = RuntimeAssetService(session, paths).register_staged(
                item_id=ITEM_ID,
                role="uploaded_source",
                relative_path=paths.runtime_relative(uploaded),
            )
            item = session.get(ItemRecord, ITEM_ID)
            assert item is not None
            item.source_locator = asset.id
            session.commit()
            asset_id = asset.id
    local = FakeLocalSources()
    try:
        result = handler(paths=paths, local=local).run(context)
        assert result.skip_stages == ()
        assert store.complete(context.claim, result, now=NOW)
        assert stage_status(engine, "transcribe") == "queued"
        assert not paths.transcript(ITEM_ID).exists()
        assert not paths.durable("items", ITEM_ID, "source").exists()
        assert len(local.media_calls) == 1
        if source_kind == "local_media":
            assert media.is_file()
        else:
            with Session(engine) as session:
                stored = session.get(RuntimeAssetRecord, asset_id)
                assert stored is not None and stored.state == "active"
    finally:
        engine.dispose()


def test_platform_candidate_fallback_publishes_without_audio(
    tmp_path: Path,
) -> None:
    first, second = track(0), track(1, language="en")
    engine, paths, store, context = seed(
        tmp_path,
        source_kind="url",
        source_locator="https://youtu.be/abcDEF12345?token=secret",
    )
    platform = FakePlatformSource(
        probe_result=youtube_probe(first, second),
        subtitle_results={
            first.id: SubtitleCandidateError("subtitle_unavailable"),
            second.id: VALID_VTT,
        },
        engine=engine,
        paths=paths,
    )
    try:
        result = handler(paths=paths, platform=platform).run(context)
        assert result.execution_evidence == {
            "source_method": "platform_subtitle",
            "selected_track_id": second.id,
            "asr_route": "platform_subtitle",
            "provider": "youtube",
        }
        assert platform.subtitle_calls == [first.id, second.id]
        assert platform.audio_calls == 0
        assert store.complete(context.claim, result, now=NOW)
        assert paths.source_original(ITEM_ID, "vtt").read_bytes() == VALID_VTT
        assert stage_status(engine, "transcribe") == "skipped"
        with Session(engine) as session:
            row = session.get(StageRunRecord, context.claim.stage_run_id)
            assert row is not None
            assert row.execution_evidence_json == result.execution_evidence
            assert "secret" not in repr(row.execution_evidence_json)
    finally:
        engine.dispose()


def test_fatal_platform_error_never_advances_or_downloads_audio(
    tmp_path: Path,
) -> None:
    first, second = track(0), track(1)
    engine, paths, _, context = seed(
        tmp_path,
        source_kind="url",
        source_locator="https://youtu.be/abcDEF12345",
    )
    platform = FakePlatformSource(
        probe_result=youtube_probe(first, second),
        subtitle_results={
            first.id: PlatformSourceError("adapter_drift"),
            second.id: VALID_VTT,
        },
        engine=engine,
        paths=paths,
    )
    try:
        with pytest.raises(SourceStageError) as caught:
            handler(paths=paths, platform=platform).run(context)
        assert caught.value.code == "adapter_drift"
        assert platform.subtitle_calls == [first.id]
        assert platform.audio_calls == 0
        assert not paths.transcript(ITEM_ID).exists()
    finally:
        engine.dispose()


def test_all_candidate_failures_create_exactly_one_audio_handoff(
    tmp_path: Path,
) -> None:
    first, second = track(0), track(1)
    engine, paths, store, context = seed(
        tmp_path,
        source_kind="url",
        source_locator="https://youtu.be/abcDEF12345",
    )
    platform = FakePlatformSource(
        probe_result=youtube_probe(first, second),
        subtitle_results={
            first.id: SubtitleCandidateError("subtitle_unavailable"),
            second.id: SubtitleCandidateError("subtitle_invalid"),
        },
        engine=engine,
        paths=paths,
    )
    try:
        result = handler(paths=paths, platform=platform).run(context)
        assert platform.audio_calls == 1
        assert result.skip_stages == ()
        assert result.execution_evidence == {
            "provider": "youtube",
            "fallback_reason": "platform_subtitle_unavailable",
        }
        assert store.complete(context.claim, result, now=NOW)
        assert stage_status(engine, "transcribe") == "queued"
        with Session(engine) as session:
            assets = session.scalars(
                select(RuntimeAssetRecord).where(
                    RuntimeAssetRecord.item_id == ITEM_ID,
                    RuntimeAssetRecord.role == "downloaded_audio",
                )
            ).all()
            assert len(assets) == 1
            assert assets[0].state == "active"
    finally:
        engine.dispose()


def test_existing_downloaded_audio_is_reused_without_second_download(
    tmp_path: Path,
) -> None:
    only = track(0)
    engine, paths, store, context = seed(
        tmp_path,
        source_kind="url",
        source_locator="https://youtu.be/abcDEF12345",
    )
    destination = paths.downloaded_audio(ITEM_ID, "webm")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"recovered audio")
    with Session(engine) as session:
        existing = RuntimeAssetService(session, paths).register_staged(
            item_id=ITEM_ID,
            role="downloaded_audio",
            relative_path=paths.runtime_relative(destination),
        )
    platform = FakePlatformSource(
        probe_result=youtube_probe(only),
        subtitle_results={
            only.id: SubtitleCandidateError("subtitle_unavailable"),
        },
        engine=engine,
        paths=paths,
    )
    try:
        result = handler(paths=paths, platform=platform).run(context)
        assert platform.audio_calls == 0
        assert store.complete(context.claim, result, now=NOW)
        with Session(engine) as session:
            current = RuntimeAssetService(session, paths).active_for_role(
                item_id=ITEM_ID,
                role="downloaded_audio",
            )
            assert current is not None and current.id == existing.id
    finally:
        engine.dispose()


def test_cancellation_after_fetch_prevents_artifact_publication(
    tmp_path: Path,
) -> None:
    only = track(0)
    engine, paths, _, context = seed(
        tmp_path,
        source_kind="url",
        source_locator="https://youtu.be/abcDEF12345",
    )

    def request_cancel() -> None:
        with Session(engine) as session:
            row = session.get(StageRunRecord, context.claim.stage_run_id)
            assert row is not None
            row.status = "cancel_requested"
            row.item.status = "cancel_requested"
            row.item.task.status = "cancel_requested"
            session.commit()

    platform = FakePlatformSource(
        probe_result=youtube_probe(only),
        subtitle_results={only.id: VALID_VTT},
        engine=engine,
        paths=paths,
        after_subtitle=request_cancel,
    )
    try:
        with pytest.raises(StageCancelled):
            handler(paths=paths, platform=platform).run(context)
        assert not paths.transcript(ITEM_ID).exists()
        assert not paths.source_original(ITEM_ID, "vtt").exists()
        assert stage_status(engine, "source") == "canceled"
    finally:
        engine.dispose()


class SimulatedCrash(BaseException):
    pass


def test_crash_before_first_atomic_publish_leaves_no_artifact_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = (tmp_path / "user-owned.srt").absolute()
    source.write_bytes(VALID_SRT)
    engine, paths, _, context = seed(
        tmp_path,
        source_kind="local_subtitle",
        source_locator=str(source),
    )
    real_publish = source_stage_module.ensure_transcript_json
    crashed = False

    def crash_then_publish(*args, **kwargs):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise SimulatedCrash()
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(
        source_stage_module,
        "ensure_transcript_json",
        crash_then_publish,
    )
    try:
        with pytest.raises(SimulatedCrash):
            handler(paths=paths).run(context)
        assert not paths.source_original(ITEM_ID, "srt").exists()
        assert not paths.transcript(ITEM_ID).exists()

        result = handler(paths=paths).run(context)

        assert result.skip_stages == ("transcribe",)
        assert paths.source_original(ITEM_ID, "srt").is_file()
        assert paths.transcript(ITEM_ID).is_file()
    finally:
        engine.dispose()


def test_crash_after_atomic_transcript_publish_recovers_identical_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = (tmp_path / "user-owned.srt").absolute()
    source.write_bytes(VALID_SRT)
    engine, paths, _, context = seed(
        tmp_path,
        source_kind="local_subtitle",
        source_locator=str(source),
    )
    real_publish = source_stage_module.ensure_transcript_json
    crashed = False

    def publish_then_crash(*args, **kwargs):
        nonlocal crashed
        result = real_publish(*args, **kwargs)
        if not crashed:
            crashed = True
            raise SimulatedCrash()
        return result

    monkeypatch.setattr(
        source_stage_module,
        "ensure_transcript_json",
        publish_then_crash,
    )
    try:
        with pytest.raises(SimulatedCrash):
            handler(paths=paths).run(context)
        before = paths.transcript(ITEM_ID).read_bytes()
        assert not paths.source_original(ITEM_ID, "srt").exists()

        result = handler(paths=paths).run(context)

        assert result.skip_stages == ("transcribe",)
        assert paths.transcript(ITEM_ID).read_bytes() == before
        assert paths.source_original(ITEM_ID, "srt").read_bytes() == VALID_SRT
    finally:
        engine.dispose()


def test_platform_subtitle_trashes_stale_owned_audio(
    tmp_path: Path,
) -> None:
    only = track(0)
    engine, paths, store, context = seed(
        tmp_path,
        source_kind="url",
        source_locator="https://youtu.be/abcDEF12345",
    )
    destination = paths.downloaded_audio(ITEM_ID, "webm")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"stale audio")
    with Session(engine) as session:
        stale = RuntimeAssetService(session, paths).register_staged(
            item_id=ITEM_ID,
            role="downloaded_audio",
            relative_path=paths.runtime_relative(destination),
        )
    platform = FakePlatformSource(
        probe_result=youtube_probe(only),
        subtitle_results={only.id: VALID_VTT},
        engine=engine,
        paths=paths,
    )
    try:
        result = handler(paths=paths, platform=platform).run(context)
        assert store.complete(context.claim, result, now=NOW)
        with Session(engine) as session:
            stored = session.get(RuntimeAssetRecord, stale.id)
            assert stored is not None
            assert stored.state == "trash"
            assert stored.purge_after == NOW + timedelta(hours=24)
    finally:
        engine.dispose()


def test_unregistered_crash_complete_audio_is_adopted_without_redownload(
    tmp_path: Path,
) -> None:
    only = track(0)
    engine, paths, store, context = seed(
        tmp_path,
        source_kind="url",
        source_locator="https://youtu.be/abcDEF12345",
    )
    destination = paths.downloaded_audio(ITEM_ID, "webm")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"complete but not registered")
    platform = FakePlatformSource(
        probe_result=youtube_probe(only),
        subtitle_results={
            only.id: SubtitleCandidateError("subtitle_unavailable"),
        },
        engine=engine,
        paths=paths,
    )
    try:
        result = handler(paths=paths, platform=platform).run(context)
        assert platform.audio_calls == 0
        assert store.complete(context.claim, result, now=NOW)
        with Session(engine) as session:
            rows = session.scalars(
                select(RuntimeAssetRecord).where(
                    RuntimeAssetRecord.item_id == ITEM_ID,
                    RuntimeAssetRecord.role == "downloaded_audio",
                )
            ).all()
            assert len(rows) == 1
            assert rows[0].state == "active"
    finally:
        engine.dispose()


def test_worker_persists_closed_source_error_without_locator(
    tmp_path: Path,
) -> None:
    engine, _, store, context = seed(
        tmp_path,
        source_kind="url",
        source_locator="https://youtu.be/abcDEF12345?token=secret",
    )
    stopped = False

    class FailingHandler:
        def run(self, _: StageContext) -> StageResult:
            nonlocal stopped
            stopped = True
            raise SourceStageError("adapter_drift")

    worker = Worker(
        store=store,
        worker_id="other-worker",
        handlers={"source": FailingHandler()},
        lease_duration=timedelta(seconds=60),
        clock=lambda: NOW + timedelta(seconds=61),
        sleeper=lambda _: None,
        stop_requested=lambda: stopped,
    )
    try:
        assert store.recover_expired(NOW + timedelta(seconds=60)) == (
            context.claim.stage_run_id,
        )
        worker.run()
        with Session(engine) as session:
            row = session.get(StageRunRecord, context.claim.stage_run_id)
            assert row is not None
            assert row.status == "failed"
            assert row.error_code == "adapter_drift"
            assert row.error_message == "adapter_drift"
            assert "secret" not in row.error_message
    finally:
        engine.dispose()


def test_untyped_platform_failure_is_collapsed_to_safe_code(
    tmp_path: Path,
) -> None:
    engine, paths, _, context = seed(
        tmp_path,
        source_kind="url",
        source_locator="https://youtu.be/abcDEF12345?token=secret",
    )

    class ExplodingPlatform:
        def probe(self, _: str):
            raise RuntimeError(
                "failed https://youtu.be/abcDEF12345?token=super-secret"
            )

    try:
        with pytest.raises(SourceStageError) as caught:
            SourceStageHandler(
                paths=paths,
                platform_source=ExplodingPlatform(),  # type: ignore[arg-type]
                local_sources=FakeLocalSources(),
                preferred_languages=("zh-Hans",),
            ).run(context)
        assert caught.value.code == "platform_source_failed"
        assert "secret" not in str(caught.value)
        with pytest.raises(ValueError):
            SourceStageError("super_secret")
    finally:
        engine.dispose()


def test_uploaded_media_is_restored_from_trash_before_handoff(
    tmp_path: Path,
) -> None:
    engine, paths, store, context = seed(
        tmp_path,
        source_kind="uploaded_media",
        source_locator="00000000-0000-4000-8000-000000000000",
        display_name="video.webm",
    )
    uploaded = paths.uploaded_source(ITEM_ID, "webm")
    uploaded.parent.mkdir(parents=True, exist_ok=True)
    uploaded.write_bytes(b"media")
    with Session(engine) as session:
        assets = RuntimeAssetService(session, paths)
        asset = assets.register_staged(
            item_id=ITEM_ID,
            role="uploaded_source",
            relative_path=paths.runtime_relative(uploaded),
        )
        item = session.get(ItemRecord, ITEM_ID)
        assert item is not None
        item.source_locator = asset.id
        session.commit()
        assets.trash(asset.id, now=NOW)
        asset_id = asset.id
    try:
        result = handler(paths=paths).run(context)
        assert store.complete(context.claim, result, now=NOW)
        with Session(engine) as session:
            stored = session.get(RuntimeAssetRecord, asset_id)
            assert stored is not None
            assert stored.state == "active"
            assert stored.purge_after is None
    finally:
        engine.dispose()
