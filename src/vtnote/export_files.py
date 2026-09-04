"""Save user-selected outputs into a durable, user-controlled directory."""

from __future__ import annotations

import re
import shutil
import unicodedata
from pathlib import Path
from typing import Any, Literal
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy.orm import Session

from vtnote.exports import ExportFormat
from vtnote.media import (
    MEDIA_EXTENSIONS,
    CommandRunner,
    FfmpegBinaries,
    FfmpegMediaProcessor,
)
from vtnote.models import DefaultSettingsRecord, ItemRecord
from vtnote.paths import StoragePaths
from vtnote.runtime_assets import RuntimeAssetService
from vtnote.tasks import InvalidTaskOperation, TaskService


ExportItem = Literal["audio", "transcript", "notes"]
ExportVariant = Literal["original", "translation"]
BatchExportMode = Literal[
    "summary_markdown",
    "original_markdown",
    "zip_all",
    "zip_notes",
]
_INVALID_FILENAME = re.compile(r'[\\/:*?"<>|\r\n]+')


def default_export_directory() -> Path:
    return Path.cwd().resolve() / "exports"


def _safe_filename(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").strip()
    cleaned = _INVALID_FILENAME.sub("-", normalized).strip(" .-")
    return (cleaned[:80].rstrip(" .-") or "vtnote-result")


def _plain_note(markdown: str) -> str:
    content = re.sub(r"\A---\n.*?\n---\n?", "", markdown, flags=re.DOTALL)
    content = re.sub(r"^#{1,6}\s+", "", content, flags=re.MULTILINE)
    return re.sub(r"[*_`>]", "", content).strip()


class ExportDirectoryService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _row(self) -> DefaultSettingsRecord:
        row = self.session.get(DefaultSettingsRecord, 1)
        if row is None:
            row = DefaultSettingsRecord(id=1)
            self.session.add(row)
            self.session.commit()
        return row

    def get(self) -> dict[str, Any]:
        row = self._row()
        default = default_export_directory().absolute()
        selected = Path(row.export_directory).absolute() if row.export_directory else default
        return {
            "directory": str(selected),
            "default_directory": str(default),
            "is_default": row.export_directory is None,
        }

    def update(self, directory: str | None, *, use_default: bool = False) -> dict[str, Any]:
        row = self._row()
        if use_default:
            row.export_directory = None
        else:
            if not isinstance(directory, str) or not directory.strip():
                raise InvalidTaskOperation("export directory is required")
            selected = Path(directory.strip())
            if not selected.is_absolute():
                raise InvalidTaskOperation("export directory must be absolute")
            try:
                selected = selected.resolve(strict=True)
            except OSError:
                raise InvalidTaskOperation("export directory does not exist") from None
            if not selected.is_dir():
                raise InvalidTaskOperation("export destination must be a directory")
            row.export_directory = str(selected)
        self.session.commit()
        return self.get()

    def resolved(self) -> Path:
        settings = self.get()
        path = Path(str(settings["directory"]))
        if not path.is_absolute():
            raise InvalidTaskOperation("export directory must be absolute")
        if settings["is_default"]:
            path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise InvalidTaskOperation("export directory is unavailable")
        return path.resolve(strict=True)


class ExportFileService:
    def __init__(
        self,
        *,
        session: Session,
        paths: StoragePaths,
        tasks: TaskService,
    ) -> None:
        self.session = session
        self.paths = paths
        self.tasks = tasks
        self.assets = RuntimeAssetService(session, paths)

    def _audio_source(self, item_id: str) -> Path | None:
        for role in (
            "downloaded_audio",
            "uploaded_source",
            "cloud_audio_inline",
            "cloud_audio",
            "local_audio",
        ):
            view = self.assets.active_for_role(item_id=item_id, role=role)
            if view is None:
                continue
            resolved = self.assets.resolve(view.id)
            if resolved.suffix.removeprefix(".").casefold() in MEDIA_EXTENSIONS:
                return resolved
        return None

    def _prepared_audio(self, item_id: str, audio_format: str) -> Path:
        role = f"export_audio_{audio_format}"
        existing = self.assets.active_for_role(item_id=item_id, role=role)
        if existing is not None:
            return self.assets.resolve(existing.id)
        source = self._audio_source(item_id)
        if source is None:
            raise InvalidTaskOperation("audio artifact is not available")
        return FfmpegMediaProcessor(
            runner=CommandRunner(),
            binaries=FfmpegBinaries.discover(),
            paths=self.paths,
            assets=self.assets,
        ).export_audio(item_id, source, audio_format).path

    @staticmethod
    def _unique_path(directory: Path, stem: str, extension: str) -> Path:
        candidate = directory / f"{stem}.{extension}"
        number = 2
        while candidate.exists():
            candidate = directory / f"{stem} ({number}).{extension}"
            number += 1
        return candidate

    def save(
        self,
        item_id: str,
        *,
        items: list[ExportItem],
        audio_format: Literal["m4a", "mp3"],
        transcript_format: Literal["srt", "txt"],
        note_format: Literal["markdown", "txt"],
    ) -> dict[str, Any]:
        if not items or len(set(items)) != len(items):
            raise InvalidTaskOperation("select at least one unique export item")
        item = self.session.get(ItemRecord, item_id)
        if item is None:
            raise KeyError(item_id)
        directory = ExportDirectoryService(self.session).resolved()
        stem = _safe_filename(item.title or item.source_display_name)
        saved: list[dict[str, str]] = []
        for kind in items:
            if kind == "audio":
                source = self._prepared_audio(item_id, audio_format)
                destination = self._unique_path(directory, f"{stem}-audio", audio_format)
                shutil.copy2(source, destination)
            elif kind == "transcript":
                rendered = self.tasks.export_item(
                    item_id,
                    variant="original",
                    export_format=ExportFormat(transcript_format),
                )
                destination = self._unique_path(
                    directory, f"{stem}-transcript", transcript_format
                )
                destination.write_text(rendered, encoding="utf-8", newline="\n")
            elif kind == "notes":
                notes = self.tasks.list_item_notes(item_id)
                if not notes:
                    raise InvalidTaskOperation("note artifact is not available")
                markdown = str(notes[-1]["markdown"])
                extension = "md" if note_format == "markdown" else "txt"
                destination = self._unique_path(directory, f"{stem}-notes", extension)
                destination.write_text(
                    markdown if note_format == "markdown" else _plain_note(markdown),
                    encoding="utf-8",
                    newline="\n",
                )
            else:
                raise InvalidTaskOperation("invalid export item")
            saved.append({"kind": kind, "filename": destination.name})
        return {"directory": str(directory), "files": saved}

    def save_text_export(
        self,
        item_id: str,
        *,
        variant: ExportVariant,
        export_format: ExportFormat,
        language: str | None = None,
    ) -> dict[str, Any]:
        item = self.session.get(ItemRecord, item_id)
        if item is None:
            raise KeyError(item_id)
        rendered = self.tasks.export_item(
            item_id,
            variant=variant,
            export_format=export_format,
            language=language,
        )
        directory = ExportDirectoryService(self.session).resolved()
        stem = _safe_filename(item.title or item.source_display_name)
        suffix = "translation" if variant == "translation" else "transcript"
        extension = "md" if export_format is ExportFormat.MARKDOWN else export_format.value
        destination = self._unique_path(directory, f"{stem}-{suffix}", extension)
        destination.write_text(rendered, encoding="utf-8", newline="\n")
        return {
            "directory": str(directory),
            "files": [{"kind": "transcript", "filename": destination.name}],
        }

    def save_batch(
        self,
        task_ids: list[str],
        *,
        mode: BatchExportMode,
    ) -> dict[str, Any]:
        if not task_ids or len(set(task_ids)) != len(task_ids):
            raise InvalidTaskOperation("select at least one unique task")
        directory = ExportDirectoryService(self.session).resolved()
        records: list[tuple[str, str, str | None]] = []
        for task_id in task_ids:
            task = self.tasks.get_task(task_id)
            if not task.items:
                continue
            item = task.items[0]
            title = _safe_filename(item.title or item.source_display_name)
            notes = self.tasks.list_item_notes(item.id)
            markdown = str(notes[-1]["markdown"]) if notes else None
            records.append((item.id, title, markdown))

        if mode == "summary_markdown":
            saved: list[dict[str, str]] = []
            for _, title, markdown in records:
                if markdown is None:
                    continue
                destination = self._unique_path(directory, f"{title}-notes", "md")
                destination.write_text(markdown, encoding="utf-8", newline="\n")
                saved.append({"kind": "notes", "filename": destination.name})
            if not saved:
                raise InvalidTaskOperation("note artifacts are not available")
            return {"directory": str(directory), "files": saved}

        if mode == "original_markdown":
            saved = []
            for item_id, title, _ in records:
                try:
                    transcript = self.tasks.export_item(
                        item_id,
                        variant="original",
                        export_format=ExportFormat.MARKDOWN,
                    )
                except InvalidTaskOperation:
                    continue
                destination = self._unique_path(
                    directory, f"{title}-transcript", "md"
                )
                destination.write_text(
                    transcript,
                    encoding="utf-8",
                    newline="\n",
                )
                saved.append({"kind": "transcript", "filename": destination.name})
            if not saved:
                raise InvalidTaskOperation("transcript artifacts are not available")
            return {"directory": str(directory), "files": saved}

        if mode not in {"zip_all", "zip_notes"}:
            raise InvalidTaskOperation("invalid batch export mode")

        destination = self._unique_path(directory, "VtNote-summary-export", "zip")
        archive_names: set[str] = set()

        def archive_name(title: str, suffix: str, extension: str) -> str:
            candidate = f"{title}-{suffix}.{extension}"
            number = 2
            while candidate in archive_names:
                candidate = f"{title}-{suffix} ({number}).{extension}"
                number += 1
            archive_names.add(candidate)
            return candidate

        with ZipFile(destination, mode="w", compression=ZIP_DEFLATED) as archive:
            for item_id, title, markdown in records:
                if markdown is not None:
                    archive.writestr(archive_name(title, "notes", "md"), markdown)
                if mode == "zip_all":
                    try:
                        transcript = self.tasks.export_item(
                            item_id,
                            variant="original",
                            export_format=ExportFormat.MARKDOWN,
                        )
                    except InvalidTaskOperation:
                        continue
                    archive.writestr(
                        archive_name(title, "transcript", "md"), transcript
                    )
        if not archive_names:
            destination.unlink(missing_ok=True)
            raise InvalidTaskOperation("export artifacts are not available")
        return {
            "directory": str(directory),
            "files": [{"kind": "archive", "filename": destination.name}],
        }
