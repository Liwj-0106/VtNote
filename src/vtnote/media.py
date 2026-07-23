"""Shell-free local FFmpeg primitives with typed runtime destinations."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import uuid4

from vtnote.paths import StoragePaths, UnsafePathError
from vtnote.runtime_assets import RuntimeAssetError, RuntimeAssetService


MEDIA_EXTENSIONS = frozenset(
    {"mp4", "mkv", "mov", "webm", "avi", "m4v", "mp3", "m4a", "wav", "flac", "ogg", "opus"}
)
CLOUD_OPUS_BITRATE = "32k"


class CommandError(RuntimeError):
    """A child-process failure represented only by a safe machine code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"command execution failed: {code}")


class MediaError(ValueError):
    """A media-validation failure represented only by a safe machine code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"media operation failed: {code}")


@dataclass(frozen=True, slots=True)
class CommandResult:
    stdout: bytes
    stderr: bytes


@dataclass(slots=True)
class _OutputState:
    limit: int
    total: int = 0
    overflow: bool = False
    stdout: list[bytes] = field(default_factory=list)
    stderr: list[bytes] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


class CommandRunner:
    """Run argv directly while bounding time and captured output in memory."""

    @staticmethod
    def _read_pipe(
        stream: BinaryIO,
        destination: list[bytes],
        state: _OutputState,
        process: subprocess.Popen[bytes],
    ) -> None:
        try:
            while chunk := stream.read(64 * 1024):
                with state.lock:
                    remaining = max(0, state.limit - state.total)
                    if remaining:
                        destination.append(chunk[:remaining])
                        state.total += min(len(chunk), remaining)
                    if len(chunk) > remaining:
                        state.overflow = True
                if state.overflow:
                    try:
                        process.kill()
                    except OSError:
                        pass
        finally:
            stream.close()

    def run(
        self, argv: tuple[str, ...], *, timeout: float, max_output_bytes: int
    ) -> CommandResult:
        if (
            not argv
            or any(not isinstance(argument, str) or not argument or "\x00" in argument for argument in argv)
            or timeout <= 0
            or max_output_bytes <= 0
        ):
            raise CommandError("invalid_command_contract")
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=creation_flags,
            )
        except (OSError, ValueError):
            raise CommandError("command_unavailable") from None
        if process.stdout is None or process.stderr is None:  # pragma: no cover - Popen contract
            process.kill()
            raise CommandError("command_pipe_unavailable")

        state = _OutputState(max_output_bytes)
        readers = (
            threading.Thread(
                target=self._read_pipe,
                args=(process.stdout, state.stdout, state, process),
                daemon=True,
            ),
            threading.Thread(
                target=self._read_pipe,
                args=(process.stderr, state.stderr, state, process),
                daemon=True,
            ),
        )
        for reader in readers:
            reader.start()
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
        for reader in readers:
            reader.join()

        if timed_out:
            raise CommandError("command_timeout")
        if state.overflow:
            raise CommandError("command_output_limit")
        if process.returncode != 0:
            raise CommandError("command_failed")
        return CommandResult(stdout=b"".join(state.stdout), stderr=b"".join(state.stderr))


class Runner(Protocol):
    def run(
        self, argv: tuple[str, ...], *, timeout: float, max_output_bytes: int
    ) -> CommandResult: ...


@dataclass(frozen=True, slots=True)
class FfmpegBinaries:
    ffmpeg: str
    ffprobe: str

    @classmethod
    def discover(cls) -> "FfmpegBinaries":
        conda_bin = Path(sys.prefix) / "Library" / "bin"
        ffmpeg = conda_bin / "ffmpeg.exe"
        ffprobe = conda_bin / "ffprobe.exe"
        return cls(
            ffmpeg=str(ffmpeg if ffmpeg.is_file() else shutil.which("ffmpeg") or "ffmpeg"),
            ffprobe=str(ffprobe if ffprobe.is_file() else shutil.which("ffprobe") or "ffprobe"),
        )


@dataclass(frozen=True, slots=True)
class MediaLimits:
    max_source_bytes: int = 8 * 1024 * 1024 * 1024
    max_duration_ms: int = 24 * 60 * 60 * 1000
    probe_timeout_seconds: float = 30
    conversion_timeout_seconds: float = 30 * 60
    max_command_output_bytes: int = 1024 * 1024


@dataclass(frozen=True, slots=True)
class MediaInfo:
    duration_ms: int
    size_bytes: int
    format_name: str
    audio_codec: str
    sample_rate: int
    channels: int


@dataclass(frozen=True, slots=True)
class PreparedAudio:
    path: Path
    asset_id: str | None
    converted: bool
    media_info: MediaInfo


class FfmpegMediaProcessor:
    def __init__(
        self,
        *,
        runner: Runner,
        binaries: FfmpegBinaries,
        limits: MediaLimits | None = None,
        paths: StoragePaths | None = None,
        assets: RuntimeAssetService | None = None,
    ) -> None:
        self.runner = runner
        self.binaries = binaries
        self.limits = limits or MediaLimits()
        self.paths = paths
        self.assets = assets

    @staticmethod
    def _safe_local_path(path: Path) -> Path:
        candidate = Path(path)
        rendered = str(candidate)
        lowered = rendered.casefold()
        if (
            not candidate.is_absolute()
            or lowered.startswith("\\\\")
            or not candidate.is_file()
        ):
            raise MediaError("invalid_local_media_path")
        if candidate.suffix.removeprefix(".").casefold() not in MEDIA_EXTENSIONS:
            raise MediaError("unsupported_media_extension")
        return candidate

    def probe_local(self, path: Path) -> MediaInfo:
        candidate = self._safe_local_path(path)
        try:
            size = candidate.stat().st_size
        except OSError:
            raise MediaError("invalid_local_media_path") from None
        if size <= 0:
            raise MediaError("empty_media")
        if size > self.limits.max_source_bytes:
            raise MediaError("media_size_limit")
        argv = (
            self.binaries.ffprobe,
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-show_entries",
            "format=duration,size,format_name:stream=codec_type,codec_name,sample_rate,channels",
            "-of",
            "json",
            str(candidate),
        )
        try:
            result = self.runner.run(
                argv,
                timeout=self.limits.probe_timeout_seconds,
                max_output_bytes=self.limits.max_command_output_bytes,
            )
            payload = json.loads(result.stdout.decode("utf-8"))
            format_payload = payload["format"]
            duration_seconds = float(format_payload["duration"])
            format_name = str(format_payload["format_name"])
            audio = next(
                stream for stream in payload["streams"] if stream.get("codec_type") == "audio"
            )
            codec = str(audio["codec_name"])
            sample_rate = int(audio["sample_rate"])
            channels = int(audio["channels"])
        except StopIteration:
            raise MediaError("audio_stream_missing") from None
        except CommandError as error:
            raise MediaError(error.code) from None
        except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            raise MediaError("invalid_ffprobe_output") from None
        if not math.isfinite(duration_seconds) or duration_seconds <= 0:
            raise MediaError("invalid_media_duration")
        duration_ms = round(duration_seconds * 1000)
        if duration_ms > self.limits.max_duration_ms:
            raise MediaError("media_duration_limit")
        if not codec or sample_rate <= 0 or channels <= 0:
            raise MediaError("invalid_audio_stream")
        return MediaInfo(
            duration_ms=duration_ms,
            size_bytes=size,
            format_name=format_name,
            audio_codec=codec,
            sample_rate=sample_rate,
            channels=channels,
        )

    def _runtime_dependencies(self) -> tuple[StoragePaths, RuntimeAssetService]:
        if self.paths is None or self.assets is None:
            raise MediaError("runtime_assets_unavailable")
        return self.paths, self.assets

    def _existing_or_recovered(
        self, item_id: str, role: str, destination: Path
    ) -> tuple[str, Path] | None:
        paths, assets = self._runtime_dependencies()
        try:
            existing = assets.active_for_role(item_id=item_id, role=role)
            if existing is not None:
                resolved = assets.resolve(existing.id)
                if resolved != destination:
                    raise MediaError("runtime_asset_path_mismatch")
                return existing.id, resolved
            recovered = assets.restore_trashed_for_role(item_id=item_id, role=role)
            if recovered is not None:
                resolved = assets.resolve(recovered.id)
                if resolved != destination:
                    raise MediaError("runtime_asset_path_mismatch")
                return recovered.id, resolved
            if destination.exists():
                view = assets.register_staged(
                    item_id=item_id,
                    role=role,
                    relative_path=paths.runtime_relative(destination),
                )
                return view.id, destination
        except (RuntimeAssetError, UnsafePathError):
            raise MediaError("runtime_asset_error") from None
        return None

    def _quarantine_failed_conversion(
        self, *, item_id: str, staging: Path
    ) -> None:
        """Register non-empty FFmpeg output before moving it into recoverable trash."""

        paths, assets = self._runtime_dependencies()
        try:
            if not staging.is_file() or staging.stat().st_size == 0:
                return
            failed = assets.register_staged(
                item_id=item_id,
                role="failed_media",
                relative_path=paths.runtime_relative(staging),
            )
            assets.trash(failed.id)
        except (OSError, RuntimeAssetError, UnsafePathError):
            # The original FFmpeg error remains the public failure. RuntimeAssetService
            # records any failed trash action using its safe lifecycle event.
            return

    @staticmethod
    def _opus_input_rate(path: Path) -> int:
        try:
            with path.open("rb") as source:
                header = source.read(64 * 1024)
            offset = header.index(b"OpusHead")
        except (OSError, ValueError):
            raise MediaError("invalid_cloud_ogg") from None
        if len(header) < offset + 16:
            raise MediaError("invalid_cloud_ogg")
        return int.from_bytes(header[offset + 12 : offset + 16], "little")

    def _run_conversion(
        self,
        *,
        item_id: str,
        source: Path,
        destination: Path,
        role: str,
        audio_args: tuple[str, ...],
    ) -> tuple[str, Path]:
        paths, assets = self._runtime_dependencies()
        existing = self._existing_or_recovered(item_id, role, destination)
        if existing is not None:
            return existing
        extension = destination.suffix.removeprefix(".")
        staging = paths.conversion_staging(item_id, str(uuid4()), extension)
        try:
            paths.assert_runtime_destination(staging)
            staging.parent.mkdir(parents=True, exist_ok=True)
            paths.assert_runtime_destination(staging)
            self.runner.run(
                (
                    self.binaries.ffmpeg,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-protocol_whitelist",
                    "file",
                    "-i",
                    str(source),
                    "-map",
                    "0:a:0",
                    "-vn",
                    *audio_args,
                    "-n",
                    str(staging),
                ),
                timeout=self.limits.conversion_timeout_seconds,
                max_output_bytes=self.limits.max_command_output_bytes,
            )
        except CommandError as error:
            self._quarantine_failed_conversion(item_id=item_id, staging=staging)
            raise MediaError(error.code) from None
        except (OSError, UnsafePathError):
            self._quarantine_failed_conversion(item_id=item_id, staging=staging)
            raise MediaError("conversion_destination_error") from None
        if not staging.is_file():
            raise MediaError("conversion_output_missing")
        try:
            paths.assert_runtime_destination(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            paths.assert_runtime_destination(destination)
            if destination.exists():
                raise MediaError("conversion_destination_conflict")
            if os.stat(staging).st_dev != os.stat(destination.parent).st_dev:
                raise MediaError("conversion_cross_device")
            os.replace(staging, destination)
            view = assets.register_staged(
                item_id=item_id,
                role=role,
                relative_path=paths.runtime_relative(destination),
            )
        except MediaError:
            self._quarantine_failed_conversion(item_id=item_id, staging=staging)
            raise
        except (OSError, RuntimeAssetError, UnsafePathError):
            self._quarantine_failed_conversion(item_id=item_id, staging=staging)
            raise MediaError("runtime_asset_error") from None
        return view.id, destination

    def convert_for_cloud(self, item_id: str, source: Path) -> PreparedAudio:
        self.probe_local(source)
        paths, _ = self._runtime_dependencies()
        destination = paths.cloud_ogg(item_id)
        asset_id, output = self._run_conversion(
            item_id=item_id,
            source=source,
            destination=destination,
            role="cloud_audio",
            audio_args=(
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "libopus",
                "-b:a",
                CLOUD_OPUS_BITRATE,
                "-application",
                "voip",
                "-f",
                "ogg",
            ),
        )
        info = self.probe_local(output)
        if (
            info.audio_codec != "opus"
            or info.channels != 1
            or self._opus_input_rate(output) != 16_000
        ):
            raise MediaError("invalid_cloud_ogg")
        return PreparedAudio(output, asset_id, True, info)

    def prepare_for_local(self, item_id: str, source: Path) -> PreparedAudio:
        source_info = self.probe_local(source)
        if (
            source_info.audio_codec == "pcm_s16le"
            and source_info.sample_rate == 16_000
            and source_info.channels == 1
        ):
            return PreparedAudio(Path(source), None, False, source_info)
        paths, _ = self._runtime_dependencies()
        destination = paths.local_prepared_audio(item_id)
        asset_id, output = self._run_conversion(
            item_id=item_id,
            source=source,
            destination=destination,
            role="local_audio",
            audio_args=(
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                "-f",
                "wav",
            ),
        )
        info = self.probe_local(output)
        if (
            info.audio_codec != "pcm_s16le"
            or info.sample_rate != 16_000
            or info.channels != 1
        ):
            raise MediaError("invalid_local_audio")
        return PreparedAudio(output, asset_id, True, info)
