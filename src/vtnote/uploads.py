"""Streaming browser upload ingress and app-owned failure recovery."""

from __future__ import annotations

import json
import os
import unicodedata
from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from python_multipart.exceptions import FormParserError, MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect

from vtnote.artifacts import validate_source_subtitle
from vtnote.media import FfmpegMediaProcessor, MEDIA_EXTENSIONS, MediaError, MediaInfo
from vtnote.models import RuntimeCleanupEventRecord
from vtnote.paths import StoragePaths, UnsafePathError
from vtnote.runtime_assets import RuntimeAssetError, RuntimeAssetService, RuntimeAssetView
from vtnote.tasks import TaskService, TaskView


SUBTITLE_EXTENSIONS = frozenset({"srt", "vtt", "ass", "json"})


@dataclass(frozen=True, slots=True)
class UploadLimits:
    max_media_bytes: int = 8 * 1024 * 1024 * 1024
    max_subtitle_bytes: int = 16 * 1024 * 1024
    max_metadata_bytes: int = 32 * 1024
    max_request_overhead_bytes: int = 128 * 1024
    max_header_count: int = 4
    max_header_size: int = 4 * 1024
    max_display_name: int = 180

    @property
    def max_request_bytes(self) -> int:
        return max(self.max_media_bytes, self.max_subtitle_bytes) + self.max_request_overhead_bytes


@dataclass(frozen=True, slots=True)
class UploadTaskContext:
    task_id: str
    item_id: str


@dataclass(slots=True)
class UploadState:
    upload_id: str
    metadata: dict[str, Any] | None = None
    context: UploadTaskContext | None = None
    upload_kind: str | None = None
    extension: str | None = None
    display_name: str | None = None
    incoming_path: Path | None = None
    file_size: int = 0


class UploadError(ValueError):
    """An upload failure carrying only a safe code and owned state."""

    def __init__(self, code: str, *, status_code: int, state: UploadState) -> None:
        self.code = code
        self.status_code = status_code
        self.state = state
        super().__init__(f"upload failed: {code}")


AcceptMetadata = Callable[[dict[str, Any], str], UploadTaskContext]


def _decode_header_value(value: bytes) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("header is not UTF-8") from None


def _display_filename(raw: bytes, *, limit: int) -> tuple[str, str]:
    try:
        decoded = _decode_header_value(raw)
    except ValueError:
        raise ValueError("unsafe filename") from None
    normalized = unicodedata.normalize("NFKC", decoded).strip()
    if (
        not normalized
        or normalized in {".", ".."}
        or any(character in normalized for character in ("/", "\\", ":"))
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise ValueError("unsafe filename")
    extension = Path(normalized).suffix.removeprefix(".").casefold()
    if not extension:
        raise ValueError("filename has no extension")
    if len(normalized) > limit:
        suffix = f".{extension}"
        normalized = normalized[: max(1, limit - len(suffix))].rstrip(" .") + suffix
    return normalized, extension


class MultipartUploadStager:
    """Parse exactly metadata then file without Starlette form spooling."""

    def __init__(self, paths: StoragePaths, limits: UploadLimits) -> None:
        self.paths = paths
        self.limits = limits

    async def consume(
        self,
        chunks: AsyncIterable[bytes],
        *,
        content_type: str,
        content_length: int | None,
        accept_metadata: AcceptMetadata,
    ) -> UploadState:
        state = UploadState(upload_id=str(uuid4()))
        if content_length is not None and (
            content_length < 0 or content_length > self.limits.max_request_bytes
        ):
            raise UploadError("request_size_limit", status_code=413, state=state)
        media_type, parameters = parse_options_header(content_type)
        boundary = parameters.get(b"boundary")
        if media_type.lower() != b"multipart/form-data" or not boundary:
            raise UploadError("invalid_multipart_content_type", status_code=400, state=state)

        part_number = 0
        current_name: str | None = None
        current_kind: str | None = None
        current_header_field = bytearray()
        current_header_value = bytearray()
        headers: dict[bytes, bytes] = {}
        metadata_bytes = bytearray()
        file_handle = None
        ended = False

        def fail(code: str, status_code: int = 400) -> None:
            raise UploadError(code, status_code=status_code, state=state)

        def on_part_begin() -> None:
            nonlocal part_number, current_name, current_kind
            part_number += 1
            if part_number > 2:
                fail("unexpected_upload_part")
            current_name = None
            current_kind = None
            headers.clear()
            current_header_field.clear()
            current_header_value.clear()

        def on_header_field(data: bytes, start: int, end: int) -> None:
            current_header_field.extend(data[start:end])

        def on_header_value(data: bytes, start: int, end: int) -> None:
            current_header_value.extend(data[start:end])

        def on_header_end() -> None:
            key = bytes(current_header_field).strip().lower()
            if not key or key in headers:
                fail("invalid_upload_headers")
            headers[key] = bytes(current_header_value).strip()
            current_header_field.clear()
            current_header_value.clear()

        def on_headers_finished() -> None:
            nonlocal current_name, current_kind, file_handle
            disposition = headers.get(b"content-disposition")
            if disposition is None:
                fail("invalid_upload_headers")
            disposition_type, options = parse_options_header(disposition)
            if disposition_type.lower() != b"form-data" or b"name" not in options:
                fail("invalid_upload_headers")
            try:
                current_name = _decode_header_value(options[b"name"])
            except ValueError:
                fail("invalid_upload_headers")
            if part_number == 1:
                if current_name != "metadata" or b"filename" in options:
                    fail("metadata_must_be_first")
                content = headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
                if content != b"application/json":
                    fail("invalid_upload_metadata")
                current_kind = "metadata"
                return
            if current_name != "file" or b"filename" not in options or state.context is None:
                fail("unexpected_upload_part")
            try:
                display_name, extension = _display_filename(
                    options[b"filename"], limit=self.limits.max_display_name
                )
            except ValueError:
                fail("unsafe_filename")
            allowed = (
                MEDIA_EXTENSIONS if state.upload_kind == "media" else SUBTITLE_EXTENSIONS
            )
            if extension not in allowed:
                fail("unsupported_upload_extension")
            try:
                incoming = self.paths.incoming_upload(state.upload_id, extension)
                self.paths.assert_runtime_destination(incoming)
                incoming.parent.mkdir(parents=True, exist_ok=True)
                self.paths.assert_runtime_destination(incoming)
                file_handle = incoming.open("xb")
            except (OSError, UnsafePathError):
                fail("upload_staging_failed")
            state.extension = extension
            state.display_name = display_name
            state.incoming_path = incoming
            current_kind = "file"

        def on_part_data(data: bytes, start: int, end: int) -> None:
            chunk = data[start:end]
            if current_kind == "metadata":
                if len(metadata_bytes) + len(chunk) > self.limits.max_metadata_bytes:
                    fail("metadata_size_limit", 413)
                metadata_bytes.extend(chunk)
                return
            if current_kind != "file" or file_handle is None:
                fail("invalid_upload_state")
            file_limit = (
                self.limits.max_media_bytes
                if state.upload_kind == "media"
                else self.limits.max_subtitle_bytes
            )
            remaining = max(0, file_limit - state.file_size)
            if remaining:
                portion = chunk[:remaining]
                file_handle.write(portion)
                state.file_size += len(portion)
            if len(chunk) > remaining:
                fail("upload_size_limit", 413)

        def on_part_end() -> None:
            nonlocal file_handle
            if current_kind == "metadata":
                try:
                    decoded = metadata_bytes.decode("utf-8")
                    metadata = json.loads(decoded)
                    if not isinstance(metadata, dict) or metadata.get("kind") not in {
                        "media",
                        "subtitle",
                    }:
                        raise ValueError("invalid metadata")
                    context = accept_metadata(metadata, state.upload_id)
                except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                    fail("invalid_upload_metadata")
                state.metadata = metadata
                state.upload_kind = str(metadata["kind"])
                state.context = context
                return
            if current_kind == "file" and file_handle is not None:
                file_handle.flush()
                os.fsync(file_handle.fileno())
                file_handle.close()
                file_handle = None
                if state.file_size == 0:
                    fail("empty_upload")

        def on_end() -> None:
            nonlocal ended
            ended = True

        callbacks = {
            "on_part_begin": on_part_begin,
            "on_part_data": on_part_data,
            "on_part_end": on_part_end,
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_header_end": on_header_end,
            "on_headers_finished": on_headers_finished,
            "on_end": on_end,
        }
        try:
            parser = MultipartParser(
                boundary,
                callbacks,
                max_size=self.limits.max_request_bytes,
                max_header_count=self.limits.max_header_count,
                max_header_size=self.limits.max_header_size,
            )
        except (FormParserError, ValueError):
            raise UploadError("invalid_multipart_content_type", status_code=400, state=state) from None

        received = 0
        try:
            async for chunk in chunks:
                received += len(chunk)
                if received > self.limits.max_request_bytes:
                    fail("request_size_limit", 413)
                if content_length is not None and received > content_length:
                    fail("content_length_mismatch")
                if parser.write(chunk) != len(chunk):
                    fail("request_size_limit", 413)
            parser.finalize()
        except ClientDisconnect:
            raise UploadError("client_disconnected", status_code=400, state=state) from None
        except UploadError:
            raise
        except (MultipartParseError, FormParserError, ValueError, OSError):
            raise UploadError("malformed_multipart", status_code=400, state=state) from None
        finally:
            if file_handle is not None:
                file_handle.flush()
                file_handle.close()

        if content_length is not None and received != content_length:
            fail("content_length_mismatch")
        if not ended or part_number != 2 or state.context is None or state.incoming_path is None:
            fail("incomplete_multipart")
        return state


class LocalSourceFiles:
    """Validate trusted local originals in place without taking ownership."""

    def __init__(
        self,
        media: FfmpegMediaProcessor,
        *,
        max_subtitle_bytes: int = UploadLimits().max_subtitle_bytes,
    ) -> None:
        self.media = media
        self.max_subtitle_bytes = max_subtitle_bytes

    def validate_media(self, path: Path) -> MediaInfo:
        return self.media.probe_local(path)

    def validate_subtitle(self, path: Path) -> None:
        candidate = Path(path)
        rendered = str(candidate).casefold()
        extension = candidate.suffix.removeprefix(".").casefold()
        if (
            not candidate.is_absolute()
            or rendered.startswith("\\\\")
            or not candidate.is_file()
            or extension not in SUBTITLE_EXTENSIONS
        ):
            raise ValueError("invalid local subtitle")
        size = candidate.stat().st_size
        if size <= 0 or size > self.max_subtitle_bytes:
            raise ValueError("invalid local subtitle")
        validate_source_subtitle(extension, candidate.read_bytes())


class LocalSourceFilesProtocol(Protocol):
    def validate_media(self, path: Path) -> MediaInfo: ...

    def validate_subtitle(self, path: Path) -> None: ...


class UploadService:
    """Move staged bytes into item ownership, register them, or recover to trash."""

    def __init__(
        self,
        *,
        session: Session,
        paths: StoragePaths,
        tasks: TaskService,
        assets: RuntimeAssetService,
        local_sources: LocalSourceFilesProtocol,
    ) -> None:
        self.session = session
        self.paths = paths
        self.tasks = tasks
        self.assets = assets
        self.local_sources = local_sources

    @staticmethod
    def staged_state(
        *,
        task_id: str,
        item_id: str,
        upload_id: str,
        upload_kind: str,
        extension: str,
        display_name: str,
        incoming_path: Path,
        file_size: int,
    ) -> UploadState:
        return UploadState(
            upload_id=upload_id,
            context=UploadTaskContext(task_id=task_id, item_id=item_id),
            upload_kind=upload_kind,
            extension=extension,
            display_name=display_name,
            incoming_path=incoming_path,
            file_size=file_size,
        )

    def _destination(self, state: UploadState) -> Path:
        if state.context is None or state.extension is None:
            raise UploadError("invalid_upload_state", status_code=400, state=state)
        try:
            return self.paths.uploaded_source(state.context.item_id, state.extension)
        except UnsafePathError:
            raise UploadError("invalid_upload_state", status_code=400, state=state) from None

    def _own_file(self, state: UploadState) -> RuntimeAssetView:
        if state.context is None or state.incoming_path is None:
            raise UploadError("upload_file_missing", status_code=400, state=state)
        destination = self._destination(state)
        incoming = state.incoming_path
        try:
            self.paths.assert_runtime_destination(incoming)
            self.paths.assert_runtime_destination(destination)
            incoming_exists = incoming.is_file()
            destination_exists = destination.is_file()
            if incoming_exists and destination_exists:
                raise UploadError("upload_copy_conflict", status_code=400, state=state)
            if incoming_exists:
                destination.parent.mkdir(parents=True, exist_ok=True)
                self.paths.assert_runtime_destination(destination)
                if os.stat(incoming).st_dev != os.stat(destination.parent).st_dev:
                    raise UploadError("upload_cross_device", status_code=400, state=state)
                os.replace(incoming, destination)
            elif not destination_exists:
                raise UploadError("upload_file_missing", status_code=400, state=state)
            return self.assets.register_staged(
                item_id=state.context.item_id,
                role="uploaded_source",
                relative_path=self.paths.runtime_relative(destination),
            )
        except UploadError:
            raise
        except (OSError, UnsafePathError, RuntimeAssetError):
            raise UploadError("upload_ownership_failed", status_code=400, state=state) from None

    def complete(self, state: UploadState) -> TaskView:
        if (
            state.context is None
            or state.incoming_path is None
            or state.display_name is None
            or state.extension is None
        ):
            raise UploadError("invalid_upload_state", status_code=400, state=state)
        validation_path = state.incoming_path
        if not validation_path.is_file():
            validation_path = self._destination(state)
        try:
            if state.upload_kind == "media":
                self.local_sources.validate_media(validation_path)
            elif state.upload_kind == "subtitle":
                self.local_sources.validate_subtitle(validation_path)
            else:
                raise UploadError("invalid_upload_state", status_code=400, state=state)
        except UploadError:
            raise
        except MediaError as error:
            raise UploadError(error.code, status_code=400, state=state) from None
        except (OSError, ValueError):
            raise UploadError("invalid_subtitle", status_code=400, state=state) from None
        asset = self._own_file(state)
        return self.tasks.bind_uploaded_asset(
            item_id=state.context.item_id,
            asset_id=asset.id,
            display_name=state.display_name,
        )

    def _discard_empty(self, state: UploadState) -> None:
        if state.extension is None or state.incoming_path is None:
            return
        try:
            expected = self.paths.incoming_upload(state.upload_id, state.extension)
            if state.incoming_path != expected:
                raise UnsafePathError("incoming path mismatch")
            self.paths.assert_runtime_destination(expected)
            if expected.exists():
                if not expected.is_file() or expected.stat().st_size != 0:
                    raise OSError("not an empty typed incoming file")
                expected.unlink()
            event = RuntimeCleanupEventRecord(
                asset_id=str(UUID(state.upload_id)),
                action="discard",
                outcome="succeeded",
                code="zero_byte_incoming",
            )
        except (OSError, ValueError, UnsafePathError):
            event = RuntimeCleanupEventRecord(
                asset_id=state.upload_id,
                action="discard",
                outcome="failed",
                code="zero_byte_discard_failed",
            )
        self.session.add(event)
        self.session.commit()

    def fail(self, state: UploadState, *, code: str) -> TaskView | None:
        if state.context is None:
            return None
        try:
            owned: RuntimeAssetView | None = None
            candidate = state.incoming_path
            destination = self._destination(state) if state.extension is not None else None
            if candidate is not None and candidate.is_file() and candidate.stat().st_size == 0:
                self._discard_empty(state)
            elif (
                (candidate is not None and candidate.is_file())
                or (destination is not None and destination.is_file())
            ):
                owned = self._own_file(state)
            if owned is not None:
                if state.display_name is not None:
                    self.tasks.bind_uploaded_asset(
                        item_id=state.context.item_id,
                        asset_id=owned.id,
                        display_name=state.display_name,
                    )
                try:
                    self.assets.trash(owned.id)
                except RuntimeAssetError:
                    self.session.add(
                        RuntimeCleanupEventRecord(
                            asset_id=owned.id,
                            action="upload_cleanup",
                            outcome="failed",
                            code="upload_cleanup_failed",
                        )
                    )
                    self.session.commit()
        except (OSError, UploadError, RuntimeAssetError):
            pass
        return self.tasks.record_upload_failure(
            item_id=state.context.item_id,
            error_code=code,
            message=f"Upload failed: {code}",
        )
