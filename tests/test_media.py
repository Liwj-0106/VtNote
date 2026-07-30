from __future__ import annotations

import hashlib
import json
import math
import struct
import sys
import wave
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from vtnote.config import Settings
from vtnote.database import initialize_database
from vtnote.media import (
    CommandError,
    CommandResult,
    CommandRunner,
    FfmpegBinaries,
    FfmpegMediaProcessor,
    MediaError,
    MediaLimits,
)
from vtnote.models import (
    ItemRecord,
    RuntimeAssetRecord,
    RuntimeCleanupEventRecord,
    TaskRecord,
)
from vtnote.paths import StoragePaths
from vtnote.runtime_assets import RuntimeAssetService


def ffprobe_payload(
    *,
    duration: str = "2.5",
    size: str = "128",
    codec: str = "aac",
    sample_rate: str = "44100",
    channels: int = 2,
) -> bytes:
    return json.dumps(
        {
            "format": {"duration": duration, "size": size, "format_name": "mov,mp4"},
            "streams": [
                {
                    "codec_type": "audio",
                    "codec_name": codec,
                    "sample_rate": sample_rate,
                    "channels": channels,
                }
            ],
        }
    ).encode()


class RecordingRunner:
    def __init__(self, probe_payload: bytes | None = None) -> None:
        self.probe_payload = probe_payload or ffprobe_payload()
        self.calls: list[tuple[tuple[str, ...], float, int]] = []

    def run(
        self, argv: tuple[str, ...], *, timeout: float, max_output_bytes: int
    ) -> CommandResult:
        self.calls.append((argv, timeout, max_output_bytes))
        return CommandResult(stdout=self.probe_payload, stderr=b"")


def test_command_runner_is_shell_free_timeout_bounded_and_opaque() -> None:
    runner = CommandRunner()
    ok = runner.run(
        (sys.executable, "-c", "print('ok')"), timeout=5, max_output_bytes=1_024
    )
    assert ok.stdout.strip() == b"ok"

    with pytest.raises(CommandError) as timeout_error:
        runner.run(
            (sys.executable, "-c", "import time; time.sleep(2)"),
            timeout=0.05,
            max_output_bytes=1_024,
        )
    assert timeout_error.value.code == "command_timeout"
    assert "time.sleep" not in str(timeout_error.value)

    with pytest.raises(CommandError) as output_error:
        runner.run(
            (sys.executable, "-c", "print('x' * 10000)"),
            timeout=5,
            max_output_bytes=128,
        )
    assert output_error.value.code == "command_output_limit"

    with pytest.raises(CommandError) as failed_error:
        runner.run(
            (sys.executable, "-c", "raise SystemExit(7)"),
            timeout=5,
            max_output_bytes=1_024,
        )
    assert failed_error.value.code == "command_failed"
    assert "SystemExit" not in str(failed_error.value)


def test_probe_local_validates_a_local_audio_file_without_modifying_it(tmp_path: Path) -> None:
    source = tmp_path / "private recording.mp4"
    source.write_bytes(b"original-media")
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    runner = RecordingRunner()
    processor = FfmpegMediaProcessor(
        runner=runner,
        binaries=FfmpegBinaries(ffmpeg="ffmpeg", ffprobe="ffprobe"),
    )

    info = processor.probe_local(source)

    assert info.duration_ms == 2_500
    assert info.size_bytes == len(b"original-media")
    assert info.audio_codec == "aac"
    assert info.sample_rate == 44_100
    assert info.channels == 2
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    argv, timeout, max_output = runner.calls[0]
    assert argv == (
        "ffprobe",
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-show_entries",
        "format=duration,size,format_name:stream=codec_type,codec_name,sample_rate,channels",
        "-of",
        "json",
        str(source),
    )
    assert timeout > 0
    assert max_output <= 1024 * 1024


@pytest.mark.parametrize(
    ("name", "payload", "code"),
    [
        ("playlist.m3u8", ffprobe_payload(), "unsupported_media_extension"),
        ("empty.mp4", ffprobe_payload(duration="0"), "invalid_media_duration"),
        (
            "silent.mp4",
            json.dumps(
                {
                    "format": {"duration": "1", "size": "10", "format_name": "mp4"},
                    "streams": [{"codec_type": "video", "codec_name": "h264"}],
                }
            ).encode(),
            "audio_stream_missing",
        ),
    ],
)
def test_probe_local_rejects_unsupported_or_unusable_media(
    tmp_path: Path, name: str, payload: bytes, code: str
) -> None:
    source = tmp_path / name
    source.write_bytes(b"media")
    processor = FfmpegMediaProcessor(
        runner=RecordingRunner(payload),
        binaries=FfmpegBinaries(ffmpeg="ffmpeg", ffprobe="ffprobe"),
    )

    with pytest.raises(MediaError) as caught:
        processor.probe_local(source)

    assert caught.value.code == code
    assert str(source) not in str(caught.value)


def test_probe_local_rejects_urls_directories_devices_and_size_limits(tmp_path: Path) -> None:
    processor = FfmpegMediaProcessor(
        runner=RecordingRunner(),
        binaries=FfmpegBinaries(ffmpeg="ffmpeg", ffprobe="ffprobe"),
        limits=MediaLimits(max_source_bytes=4, max_duration_ms=60_000),
    )
    directory = tmp_path / "directory.mp4"
    directory.mkdir()
    oversized = tmp_path / "large.mp4"
    oversized.write_bytes(b"12345")

    for candidate, code in (
        (Path("https://example.com/video.mp4"), "invalid_local_media_path"),
        (directory, "invalid_local_media_path"),
        (Path(r"\\.\PhysicalDrive0"), "invalid_local_media_path"),
        (oversized, "media_size_limit"),
    ):
        with pytest.raises(MediaError) as caught:
            processor.probe_local(candidate)
        assert caught.value.code == code


@pytest.mark.parametrize(
    "rendered",
    [
        r"\\server\share\video.mp4",
        r"\\.\PhysicalDrive0",
        r"\\?\C:\private\video.mp4",
        r"\\?\UNC\server\share\video.mp4",
    ],
)
def test_probe_rejects_windows_network_device_and_extended_paths_before_access(
    monkeypatch: pytest.MonkeyPatch, rendered: str,
) -> None:
    processor = FfmpegMediaProcessor(
        runner=RecordingRunner(),
        binaries=FfmpegBinaries(ffmpeg="ffmpeg", ffprobe="ffprobe"),
    )
    candidate = Path(rendered)
    original_is_file = Path.is_file

    def guarded_is_file(path: Path) -> bool:
        if str(path).startswith("\\\\"):
            raise AssertionError("Windows network/device namespace was touched")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", guarded_is_file)

    with pytest.raises(MediaError) as caught:
        processor.probe_local(candidate)

    assert caught.value.code == "invalid_local_media_path"


def make_asset_service(tmp_path: Path) -> tuple[RuntimeAssetService, StoragePaths, Session, str]:
    paths = StoragePaths.from_settings(
        Settings(data_root=tmp_path / "data", runtime_cache_root=tmp_path / "cache")
    )
    engine = initialize_database(paths.database)
    session = Session(engine)
    task = TaskRecord(options={}, pipeline_snapshot_json={})
    item = ItemRecord(position=0, source_kind="local_media", source_locator="local")
    task.items.append(item)
    session.add(task)
    session.commit()
    return RuntimeAssetService(session, paths), paths, session, item.id


def write_tiny_wav(path: Path, *, sample_rate: int = 16_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = bytearray()
    for index in range(sample_rate // 4):
        value = int(8_000 * math.sin(2 * math.pi * 440 * index / sample_rate))
        samples.extend(struct.pack("<h", value))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(bytes(samples))


def test_real_ffmpeg_cloud_conversion_is_mono_opus_with_16khz_opushead(
    tmp_path: Path,
) -> None:
    binaries = FfmpegBinaries.discover()
    if not (Path(binaries.ffmpeg).is_file() and Path(binaries.ffprobe).is_file()):
        pytest.skip("verified Conda FFmpeg binaries are unavailable")
    assets, paths, session, item_id = make_asset_service(tmp_path)
    source = tmp_path / "user-original.wav"
    write_tiny_wav(source)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    processor = FfmpegMediaProcessor(
        runner=CommandRunner(), binaries=binaries, paths=paths, assets=assets
    )
    try:
        prepared = processor.convert_for_cloud(item_id, source)
        output = paths.cloud_ogg(item_id)

        assert prepared.converted is True
        assert prepared.path == output
        assert prepared.asset_id is not None
        assert assets.resolve(prepared.asset_id) == output
        assert prepared.media_info.audio_codec == "opus"
        assert prepared.media_info.channels == 1
        # Opus always decodes at 48 kHz; the container header retains the requested input rate.
        assert prepared.media_info.sample_rate == 48_000
        ogg = output.read_bytes()
        opus_head = ogg.index(b"OpusHead")
        assert int.from_bytes(ogg[opus_head + 12 : opus_head + 16], "little") == 16_000
        assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash

        active = processor.convert_for_cloud(item_id, source)
        assert active.asset_id == prepared.asset_id

        trashed = assets.trash(prepared.asset_id)
        assert trashed.state == "trash"
        assert not output.exists()

        recovered = processor.convert_for_cloud(item_id, source)
        assert recovered.asset_id == prepared.asset_id
        assert recovered.path == output
        assert assets.active_for_role(item_id=item_id, role="cloud_audio") is not None

        registered = session.get(RuntimeAssetRecord, recovered.asset_id)
        assert registered is not None
        session.delete(registered)  # crash-shaped: canonical move survived, registration did not
        session.commit()
        crash_recovered = processor.convert_for_cloud(item_id, source)
        assert crash_recovered.asset_id != recovered.asset_id
        assert assets.resolve(crash_recovered.asset_id) == output
    finally:
        session.bind.dispose()
        session.close()


class FailFirstConversionRunner:
    def __init__(self, delegate: CommandRunner, ffmpeg: str) -> None:
        self.delegate = delegate
        self.ffmpeg = ffmpeg
        self.failed = False

    def run(
        self, argv: tuple[str, ...], *, timeout: float, max_output_bytes: int
    ) -> CommandResult:
        if argv[0] == self.ffmpeg and not self.failed:
            self.failed = True
            destination = Path(argv[-1])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"partial-not-valid-ogg")
            raise CommandError("command_timeout")
        return self.delegate.run(
            argv, timeout=timeout, max_output_bytes=max_output_bytes
        )


class InvalidFirstSuccessfulConversionRunner:
    """Produce one invalid-but-complete FFmpeg output, then delegate normally."""

    def __init__(self, delegate: CommandRunner, ffmpeg: str, mode: str) -> None:
        self.delegate = delegate
        self.ffmpeg = ffmpeg
        self.mode = mode
        self.invalidated = False

    def run(
        self, argv: tuple[str, ...], *, timeout: float, max_output_bytes: int
    ) -> CommandResult:
        if argv[0] == self.ffmpeg and not self.invalidated:
            self.invalidated = True
            modified = list(argv)
            if self.mode == "cloud":
                modified[modified.index("-f") + 1] = "matroska"
            else:
                modified[modified.index("-ar") + 1] = "8000"
            return self.delegate.run(
                tuple(modified), timeout=timeout, max_output_bytes=max_output_bytes
            )
        return self.delegate.run(
            argv, timeout=timeout, max_output_bytes=max_output_bytes
        )


class EmptyFailedConversionRunner:
    def __init__(self, delegate: CommandRunner, ffmpeg: str) -> None:
        self.delegate = delegate
        self.ffmpeg = ffmpeg

    def run(
        self, argv: tuple[str, ...], *, timeout: float, max_output_bytes: int
    ) -> CommandResult:
        if argv[0] == self.ffmpeg:
            destination = Path(argv[-1])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.touch()
            raise CommandError("command_failed")
        return self.delegate.run(
            argv, timeout=timeout, max_output_bytes=max_output_bytes
        )


def test_failed_ffmpeg_partial_is_quarantined_and_never_adopted_on_retry(
    tmp_path: Path,
) -> None:
    binaries = FfmpegBinaries.discover()
    if not (Path(binaries.ffmpeg).is_file() and Path(binaries.ffprobe).is_file()):
        pytest.skip("verified Conda FFmpeg binaries are unavailable")
    assets, paths, session, item_id = make_asset_service(tmp_path)
    source = tmp_path / "user-original.wav"
    write_tiny_wav(source)
    runner = FailFirstConversionRunner(CommandRunner(), binaries.ffmpeg)
    processor = FfmpegMediaProcessor(
        runner=runner, binaries=binaries, paths=paths, assets=assets
    )
    try:
        with pytest.raises(MediaError) as failed:
            processor.convert_for_cloud(item_id, source)
        assert failed.value.code == "command_timeout"
        assert not paths.cloud_ogg(item_id).exists()
        failed_assets = session.scalars(
            select(RuntimeAssetRecord).where(RuntimeAssetRecord.role == "failed_media")
        ).all()
        assert len(failed_assets) == 1
        assert failed_assets[0].state == "trash"

        prepared = processor.convert_for_cloud(item_id, source)
        assert prepared.media_info.audio_codec == "opus"
        assert prepared.asset_id != failed_assets[0].id
        assert assets.resolve(prepared.asset_id) == paths.cloud_ogg(item_id)
    finally:
        session.bind.dispose()
        session.close()


def test_invalid_cloud_container_is_quarantined_before_publish_and_retry_succeeds(
    tmp_path: Path,
) -> None:
    binaries = FfmpegBinaries.discover()
    if not (Path(binaries.ffmpeg).is_file() and Path(binaries.ffprobe).is_file()):
        pytest.skip("verified Conda FFmpeg binaries are unavailable")
    assets, paths, session, item_id = make_asset_service(tmp_path)
    source = tmp_path / "user-original.wav"
    write_tiny_wav(source)
    runner = InvalidFirstSuccessfulConversionRunner(
        CommandRunner(), binaries.ffmpeg, "cloud"
    )
    processor = FfmpegMediaProcessor(
        runner=runner, binaries=binaries, paths=paths, assets=assets
    )
    try:
        with pytest.raises(MediaError) as failed:
            processor.convert_for_cloud(item_id, source)
        assert failed.value.code == "invalid_cloud_ogg"
        assert not paths.cloud_ogg(item_id).exists()
        assert session.scalars(
            select(RuntimeAssetRecord).where(
                RuntimeAssetRecord.role == "cloud_audio"
            )
        ).all() == []
        quarantined = session.scalars(
            select(RuntimeAssetRecord).where(
                RuntimeAssetRecord.role == "failed_media"
            )
        ).all()
        assert len(quarantined) == 1
        assert quarantined[0].state == "trash"

        prepared = processor.convert_for_cloud(item_id, source)
        assert "ogg" in prepared.media_info.format_name.casefold().split(",")
        assert prepared.media_info.audio_codec == "opus"
        assert assets.resolve(prepared.asset_id) == paths.cloud_ogg(item_id)
    finally:
        session.bind.dispose()
        session.close()


def test_invalid_local_pcm_is_quarantined_before_publish_and_retry_succeeds(
    tmp_path: Path,
) -> None:
    binaries = FfmpegBinaries.discover()
    if not (Path(binaries.ffmpeg).is_file() and Path(binaries.ffprobe).is_file()):
        pytest.skip("verified Conda FFmpeg binaries are unavailable")
    assets, paths, session, item_id = make_asset_service(tmp_path)
    source = tmp_path / "user-original.wav"
    write_tiny_wav(source, sample_rate=44_100)
    runner = InvalidFirstSuccessfulConversionRunner(
        CommandRunner(), binaries.ffmpeg, "local"
    )
    processor = FfmpegMediaProcessor(
        runner=runner, binaries=binaries, paths=paths, assets=assets
    )
    try:
        with pytest.raises(MediaError) as failed:
            processor.prepare_for_local(item_id, source)
        assert failed.value.code == "invalid_local_audio"
        assert not paths.local_prepared_audio(item_id).exists()
        assert session.scalars(
            select(RuntimeAssetRecord).where(
                RuntimeAssetRecord.role == "local_audio"
            )
        ).all() == []
        quarantined = session.scalars(
            select(RuntimeAssetRecord).where(
                RuntimeAssetRecord.role == "failed_media"
            )
        ).all()
        assert len(quarantined) == 1
        assert quarantined[0].state == "trash"

        prepared = processor.prepare_for_local(item_id, source)
        assert prepared.media_info.audio_codec == "pcm_s16le"
        assert prepared.media_info.sample_rate == 16_000
        assert prepared.media_info.channels == 1
        assert assets.resolve(prepared.asset_id) == paths.local_prepared_audio(
            item_id
        )
    finally:
        session.bind.dispose()
        session.close()


def test_zero_byte_ffmpeg_staging_is_discarded_by_typed_path_and_audited(
    tmp_path: Path,
) -> None:
    binaries = FfmpegBinaries.discover()
    if not (Path(binaries.ffmpeg).is_file() and Path(binaries.ffprobe).is_file()):
        pytest.skip("verified Conda FFmpeg binaries are unavailable")
    assets, paths, session, item_id = make_asset_service(tmp_path)
    source = tmp_path / "user-original.wav"
    write_tiny_wav(source)
    processor = FfmpegMediaProcessor(
        runner=EmptyFailedConversionRunner(CommandRunner(), binaries.ffmpeg),
        binaries=binaries,
        paths=paths,
        assets=assets,
    )
    try:
        with pytest.raises(MediaError) as failed:
            processor.convert_for_cloud(item_id, source)
        assert failed.value.code == "command_failed"
        staging_directory = paths.runtime(
            "items", item_id, "audio", "staging"
        )
        assert not list(staging_directory.glob("*"))
        events = session.scalars(
            select(RuntimeCleanupEventRecord).where(
                RuntimeCleanupEventRecord.action == "discard"
            )
        ).all()
        assert len(events) == 1
        assert events[0].outcome == "succeeded"
        assert events[0].code == "zero_byte_media_staging"
    finally:
        session.bind.dispose()
        session.close()


def test_local_preparation_reuses_compatible_pcm_or_registers_typed_wav(
    tmp_path: Path,
) -> None:
    binaries = FfmpegBinaries.discover()
    if not (Path(binaries.ffmpeg).is_file() and Path(binaries.ffprobe).is_file()):
        pytest.skip("verified Conda FFmpeg binaries are unavailable")
    assets, paths, session, item_id = make_asset_service(tmp_path)
    compatible = tmp_path / "already-compatible.wav"
    write_tiny_wav(compatible)
    processor = FfmpegMediaProcessor(
        runner=CommandRunner(), binaries=binaries, paths=paths, assets=assets
    )
    try:
        reused = processor.prepare_for_local(item_id, compatible)
        assert reused.converted is False
        assert reused.path == compatible
        assert reused.asset_id is None

        stereo = tmp_path / "needs-conversion.wav"
        write_tiny_wav(stereo, sample_rate=44_100)
        converted = processor.prepare_for_local(item_id, stereo)
        assert converted.converted is True
        assert converted.path == paths.local_prepared_audio(item_id)
        assert converted.asset_id is not None
        assert converted.media_info.channels == 1
        assert converted.media_info.sample_rate == 16_000
        assert converted.media_info.audio_codec == "pcm_s16le"
    finally:
        session.bind.dispose()
        session.close()
