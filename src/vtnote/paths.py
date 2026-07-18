"""Owned-path construction that rejects absolute paths and traversal."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from vtnote.config import Settings


class UnsafePathError(ValueError):
    """Raised when an untrusted path would leave an application-owned root."""


_LANGUAGE_RE = re.compile(
    r"^[A-Za-z]{2,3}(?:-[A-Za-z]{4})?(?:-(?:[A-Za-z]{2}|\d{3}))?"
    r"(?:-(?:[A-Za-z0-9]{5,8}|\d[A-Za-z0-9]{3}))*$"
)
_SOURCE_EXTENSIONS = frozenset({"srt", "vtt", "ass", "json"})
_AUDIO_EXTENSIONS = frozenset({"wav", "mp3", "m4a", "flac", "ogg", "opus", "webm"})


def _uuid_component(value: str | UUID) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, AttributeError) as error:
        raise UnsafePathError(f"invalid UUID path component: {value!r}") from error


def _extension(value: str, allowed: frozenset[str]) -> str:
    normalized = value.casefold()
    if normalized not in allowed or value != Path(value).name:
        raise UnsafePathError(f"unsupported file extension: {value!r}")
    return normalized


def _language_component(value: str) -> str:
    if not _LANGUAGE_RE.fullmatch(value):
        raise UnsafePathError(f"invalid language tag: {value!r}")
    parts = value.split("-")
    normalized = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            normalized.append(part.title())
        elif len(part) == 2 and part.isalpha():
            normalized.append(part.upper())
        else:
            normalized.append(part.lower())
    return "-".join(normalized)


def _is_reparse_point(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _assert_owned_path(root: Path, candidate: Path) -> Path:
    root = root.absolute()
    candidate = candidate.absolute()
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise UnsafePathError("path is outside its application-owned root") from error

    current = root
    if _is_reparse_point(current):
        raise UnsafePathError(f"owned root is a symlink or reparse point: {current}")
    for part in relative.parts:
        current /= part
        if _is_reparse_point(current):
            raise UnsafePathError(f"owned path contains a symlink or reparse point: {current}")

    resolved_root = root.resolve(strict=False)
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError as error:
        raise UnsafePathError("resolved path escapes its application-owned root") from error
    return candidate


def _resolve_under(root: Path, parts: tuple[str | Path, ...]) -> Path:
    if not parts:
        return root
    converted = [Path(part) for part in parts]
    if any(part.is_absolute() or ".." in part.parts for part in converted):
        raise UnsafePathError("owned path components must be relative and cannot contain '..'")
    candidate = root.joinpath(*converted).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise UnsafePathError("owned path escapes its configured root") from error
    return candidate


@dataclass(frozen=True, slots=True)
class StoragePaths:
    data_root: Path
    runtime_cache_root: Path

    @classmethod
    def from_settings(cls, settings: Settings) -> "StoragePaths":
        return cls(settings.data_root, settings.runtime_cache_root)

    @property
    def database(self) -> Path:
        return self.durable("vtnote.db")

    def durable(self, *parts: str | Path) -> Path:
        return _resolve_under(self.data_root, parts)

    def runtime(self, *parts: str | Path) -> Path:
        return _resolve_under(self.runtime_cache_root, parts)

    def source_original(self, item_id: str | UUID, extension: str) -> Path:
        return self.durable(
            "items",
            _uuid_component(item_id),
            "source",
            f"original.{_extension(extension, _SOURCE_EXTENSIONS)}",
        )

    def transcript(self, item_id: str | UUID) -> Path:
        return self.durable("items", _uuid_component(item_id), "transcript.json")

    def translation(self, item_id: str | UUID, language: str) -> Path:
        return self.durable(
            "items",
            _uuid_component(item_id),
            "translations",
            f"{_language_component(language)}.json",
        )

    def note(self, item_id: str | UUID, note_id: str | UUID) -> Path:
        return self.durable(
            "items", _uuid_component(item_id), "notes", f"{_uuid_component(note_id)}.md"
        )

    def runtime_audio(self, item_id: str | UUID, extension: str = "wav") -> Path:
        return self.runtime(
            "items",
            _uuid_component(item_id),
            "audio",
            f"source.{_extension(extension, _AUDIO_EXTENSIONS)}",
        )

    def assert_durable_destination(self, candidate: Path) -> Path:
        return _assert_owned_path(self.data_root, candidate)

    def assert_runtime_destination(self, candidate: Path) -> Path:
        return _assert_owned_path(self.runtime_cache_root, candidate)

    def ensure_roots(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.runtime_cache_root.mkdir(parents=True, exist_ok=True)
