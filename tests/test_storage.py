from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest
from pydantic import ValidationError

from vtnote.artifacts import (
    ArtifactExistsError,
    ensure_transcript_json,
    write_source_original,
    write_note_markdown,
    write_transcript_json,
    write_translation_json,
)
from vtnote.config import Settings, platform_storage_roots
from vtnote.paths import StoragePaths, UnsafePathError
from vtnote.schemas import (
    Provenance,
    ProvenanceMethod,
    Transcript,
    TranscriptSegment,
    Translation,
    TranslationEntry,
    transcript_sha256,
)


ITEM_ID = "11111111-1111-4111-8111-111111111111"
NOTE_ID = "22222222-2222-4222-8222-222222222222"
UPLOAD_ID = "33333333-3333-4333-8333-333333333333"
ASSET_ID = "44444444-4444-4444-8444-444444444444"


def make_directory_link(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as error:
        if os.name != "nt" or getattr(error, "winerror", None) != 1314:
            raise
        result = subprocess.run(
            [
                os.environ.get("COMSPEC", "cmd.exe"),
                "/d",
                "/c",
                "mklink",
                "/J",
                str(link),
                str(target),
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_root=tmp_path / "data",
        runtime_cache_root=tmp_path / "cache",
    )


def make_transcript(text: str) -> Transcript:
    return Transcript(
        language="en",
        duration_ms=1_000,
        provenance=Provenance(
            method=ProvenanceMethod.PLATFORM_SUBTITLE,
            provider="manual_upload",
            model=None,
        ),
        segments=[TranscriptSegment(id="seg_000001", start_ms=0, end_ms=1_000, text=text)],
    )


def make_two_segment_transcript() -> Transcript:
    return Transcript(
        language="en",
        duration_ms=2_000,
        provenance=Provenance(
            method=ProvenanceMethod.PLATFORM_SUBTITLE,
            provider="manual_upload",
            model=None,
        ),
        segments=[
            TranscriptSegment(id="seg_000001", start_ms=0, end_ms=1_000, text="one"),
            TranscriptSegment(id="seg_000002", start_ms=1_000, end_ms=2_000, text="two"),
        ],
    )


def test_settings_reject_relative_or_overlapping_storage_roots(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="absolute"):
        Settings(data_root=Path("relative"), runtime_cache_root=tmp_path / "cache")

    with pytest.raises(ValidationError, match="must not overlap"):
        Settings(data_root=tmp_path, runtime_cache_root=tmp_path / "cache")

    if os.name == "nt":
        with pytest.raises(ValidationError, match="same Windows drive"):
            Settings(
                data_root=Path(r"D:\VtNote-data"),
                runtime_cache_root=Path(r"C:\VtNote-runtime"),
            )


def test_platform_storage_roots_use_native_user_directories() -> None:
    mac_data, mac_cache = platform_storage_roots(
        platform_name="darwin",
        environment={},
        home="/Users/demo",
    )
    assert mac_data == PurePosixPath(
        "/Users/demo/Library/Application Support/VtNote"
    )
    assert mac_cache == PurePosixPath("/Users/demo/Library/Caches/VtNote")

    windows_data, windows_cache = platform_storage_roots(
        platform_name="win32",
        environment={"LOCALAPPDATA": r"C:\Users\demo\AppData\Local"},
        home=r"C:\Users\demo",
    )
    assert windows_data == PureWindowsPath(
        r"C:\Users\demo\AppData\Local\VtNote\Data"
    )
    assert windows_cache == PureWindowsPath(
        r"C:\Users\demo\AppData\Local\VtNote\Cache"
    )

    linux_data, linux_cache = platform_storage_roots(
        platform_name="linux",
        environment={
            "XDG_DATA_HOME": "/srv/demo/data",
            "XDG_CACHE_HOME": "/srv/demo/cache",
        },
        home="/home/demo",
    )
    assert linux_data == PurePosixPath("/srv/demo/data/VtNote")
    assert linux_cache == PurePosixPath("/srv/demo/cache/VtNote")


def test_settings_defaults_are_absolute_and_support_environment_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VTNOTE_DATA_ROOT", raising=False)
    monkeypatch.delenv("VTNOTE_RUNTIME_CACHE_ROOT", raising=False)
    defaults = Settings()

    assert defaults.data_root.is_absolute()
    assert defaults.runtime_cache_root.is_absolute()
    assert defaults.data_root != defaults.runtime_cache_root
    if sys.platform == "darwin":
        assert defaults.data_root == (
            Path.home() / "Library" / "Application Support" / "VtNote"
        )
        assert defaults.runtime_cache_root == (
            Path.home() / "Library" / "Caches" / "VtNote"
        )

    data_root = tmp_path / "explicit-data"
    cache_root = tmp_path / "explicit-cache"
    monkeypatch.setenv("VTNOTE_DATA_ROOT", str(data_root))
    monkeypatch.setenv("VTNOTE_RUNTIME_CACHE_ROOT", str(cache_root))
    overridden = Settings()

    assert overridden.data_root == data_root
    assert overridden.runtime_cache_root == cache_root


def test_settings_cannot_bind_outside_loopback(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(
            data_root=tmp_path / "data",
            runtime_cache_root=tmp_path / "cache",
            bind_host="0.0.0.0",
        )


def test_storage_paths_reject_escape_and_absolute_components(tmp_path: Path) -> None:
    paths = StoragePaths.from_settings(make_settings(tmp_path))

    with pytest.raises(UnsafePathError):
        paths.durable("..", "outside.txt")
    with pytest.raises(UnsafePathError):
        paths.runtime(tmp_path / "absolute.txt")


def test_storage_paths_create_only_owned_roots(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    paths = StoragePaths.from_settings(settings)

    paths.ensure_roots()

    assert settings.data_root.is_dir()
    assert settings.runtime_cache_root.is_dir()
    assert paths.database == settings.data_root / "vtnote.db"


def test_storage_paths_produce_the_fixed_item_layout(tmp_path: Path) -> None:
    paths = StoragePaths.from_settings(make_settings(tmp_path))

    assert paths.source_original(ITEM_ID, "srt") == (
        paths.data_root / "items" / ITEM_ID / "source" / "original.srt"
    )
    assert paths.transcript(ITEM_ID) == paths.data_root / "items" / ITEM_ID / "transcript.json"
    assert paths.translation(ITEM_ID, "zh-Hans") == (
        paths.data_root / "items" / ITEM_ID / "translations" / "zh-Hans.json"
    )
    assert paths.note(ITEM_ID, NOTE_ID) == (
        paths.data_root / "items" / ITEM_ID / "notes" / f"{NOTE_ID}.md"
    )
    assert paths.runtime_audio(ITEM_ID, "wav") == (
        paths.runtime_cache_root / "items" / ITEM_ID / "audio" / "source.wav"
    )


def test_storage_paths_produce_typed_runtime_media_layout(tmp_path: Path) -> None:
    paths = StoragePaths.from_settings(make_settings(tmp_path))

    assert paths.incoming_upload(UPLOAD_ID, "mp4") == (
        paths.runtime_cache_root / "incoming" / UPLOAD_ID / "upload.mp4"
    )
    assert paths.uploaded_source(ITEM_ID, "srt") == (
        paths.runtime_cache_root / "items" / ITEM_ID / "source" / "upload.srt"
    )
    assert paths.downloaded_audio(ITEM_ID, "webm") == (
        paths.runtime_cache_root / "items" / ITEM_ID / "audio" / "downloaded.webm"
    )
    assert paths.cloud_ogg(ITEM_ID) == (
        paths.runtime_cache_root / "items" / ITEM_ID / "audio" / "cloud.ogg"
    )
    assert paths.local_prepared_audio(ITEM_ID) == (
        paths.runtime_cache_root / "items" / ITEM_ID / "audio" / "local.wav"
    )
    assert paths.trash_asset(ASSET_ID, "mp4") == (
        paths.runtime_cache_root / "trash" / ASSET_ID / "asset.mp4"
    )


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("incoming_upload", ("not-a-uuid", "mp4")),
        ("incoming_upload", (UPLOAD_ID, "../mp4")),
        ("uploaded_source", (ITEM_ID, "exe")),
        ("downloaded_audio", (ITEM_ID, "mp4")),
        ("trash_asset", (ASSET_ID, "exe")),
    ],
)
def test_typed_runtime_media_paths_reject_invalid_components(
    tmp_path: Path, method: str, args: tuple[str, ...]
) -> None:
    paths = StoragePaths.from_settings(make_settings(tmp_path))

    with pytest.raises(UnsafePathError):
        getattr(paths, method)(*args)


def test_runtime_relative_path_round_trip_rejects_traversal(tmp_path: Path) -> None:
    paths = StoragePaths.from_settings(make_settings(tmp_path))
    candidate = paths.downloaded_audio(ITEM_ID, "webm")

    relative = paths.runtime_relative(candidate)

    assert relative == f"items/{ITEM_ID}/audio/downloaded.webm"
    assert paths.runtime_from_relative(relative) == candidate
    for unsafe in ("../outside.mp4", "/absolute.mp4", r"items\\escape.mp4"):
        with pytest.raises(UnsafePathError):
            paths.runtime_from_relative(unsafe)


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("source_original", ("not-a-uuid", "srt")),
        ("source_original", (ITEM_ID, "../srt")),
        ("source_original", (ITEM_ID, "exe")),
        ("translation", (ITEM_ID, "../../escape")),
        ("translation", (ITEM_ID, "not_a_language")),
        ("note", (ITEM_ID, "not-a-uuid")),
        ("runtime_audio", (ITEM_ID, "../wav")),
        ("runtime_audio", (ITEM_ID, "exe")),
    ],
)
def test_typed_storage_paths_reject_invalid_components(
    tmp_path: Path, method: str, args: tuple[str, ...]
) -> None:
    paths = StoragePaths.from_settings(make_settings(tmp_path))

    with pytest.raises(UnsafePathError):
        getattr(paths, method)(*args)


def test_transcript_write_is_immutable_and_preserves_first_content(tmp_path: Path) -> None:
    paths = StoragePaths.from_settings(make_settings(tmp_path))
    paths.ensure_roots()
    destination = paths.transcript(ITEM_ID)

    write_transcript_json(paths, ITEM_ID, make_transcript("first"))

    with pytest.raises(ArtifactExistsError):
        write_transcript_json(paths, ITEM_ID, make_transcript("replacement"))

    stored = json.loads(destination.read_text(encoding="utf-8"))
    assert stored["segments"][0]["text"] == "first"


def test_transcript_ensure_is_idempotent_for_identical_recovery(tmp_path: Path) -> None:
    paths = StoragePaths.from_settings(make_settings(tmp_path))
    transcript = make_transcript("same")

    first = ensure_transcript_json(paths, ITEM_ID, transcript)
    original = first.read_bytes()
    recovered = ensure_transcript_json(paths, ITEM_ID, transcript)

    assert recovered == first
    assert recovered.read_bytes() == original
    with pytest.raises(ArtifactExistsError):
        ensure_transcript_json(paths, ITEM_ID, make_transcript("different"))
    assert recovered.read_bytes() == original


def test_source_original_accepts_real_subtitles_and_never_overwrites(tmp_path: Path) -> None:
    paths = StoragePaths.from_settings(make_settings(tmp_path))
    source = b"1\n00:00:00,000 --> 00:00:01,000\nHello\n"

    destination = write_source_original(paths, ITEM_ID, "srt", source)
    assert destination.read_bytes() == source

    bili_json = (
        b'{"body":[{"from":0.0,"to":1.0,"content":"Hello"}]}'
    )
    json_destination = write_source_original(paths, NOTE_ID, "json", bili_json)
    assert json_destination.read_bytes() == bili_json
    assert write_source_original(paths, ITEM_ID, "srt", source) == destination

    with pytest.raises(ArtifactExistsError):
        write_source_original(
            paths,
            ITEM_ID,
            "srt",
            b"1\n00:00:00,000 --> 00:00:01,000\nReplacement\n",
        )
    assert destination.read_bytes() == source
    with pytest.raises(ArtifactExistsError):
        write_source_original(
            paths,
            ITEM_ID,
            "vtt",
            b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello\n",
        )
    assert not paths.source_original(ITEM_ID, "vtt").exists()


@pytest.mark.parametrize(
    ("extension", "source"),
    [
        ("srt", b"not a subtitle"),
        ("vtt", b"WEBVTT\n"),
        ("json", b"{}"),
        ("json", b'{"segments":[42]}'),
        ("json", b'{"body":[{"from":2,"to":1,"content":"invalid"}]}'),
    ],
)
def test_source_original_rejects_non_subtitle_content(
    tmp_path: Path, extension: str, source: bytes
) -> None:
    paths = StoragePaths.from_settings(make_settings(tmp_path))

    with pytest.raises(ValueError):
        write_source_original(paths, ITEM_ID, extension, source)


def test_root_aware_write_rejects_a_substituted_symlink_or_junction_ancestor(
    tmp_path: Path,
) -> None:
    paths = StoragePaths.from_settings(make_settings(tmp_path))
    paths.ensure_roots()
    destination = paths.note(ITEM_ID, NOTE_ID)
    outside = tmp_path / "outside"
    outside.mkdir()
    destination.parent.parent.parent.mkdir(parents=True)
    make_directory_link(destination.parent.parent, outside)

    with pytest.raises(UnsafePathError):
        write_note_markdown(paths, ITEM_ID, NOTE_ID, "blocked")

    assert not (outside / "notes" / f"{NOTE_ID}.md").exists()


def test_root_aware_write_rejects_a_configured_reparse_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    data_link = tmp_path / "data-link"
    make_directory_link(data_link, outside)
    paths = StoragePaths.from_settings(
        Settings(data_root=data_link, runtime_cache_root=tmp_path / "cache")
    )

    with pytest.raises(UnsafePathError):
        write_note_markdown(paths, ITEM_ID, NOTE_ID, "blocked")

    assert not (outside / "items").exists()


def test_note_markdown_is_atomically_replaceable_at_the_typed_path(tmp_path: Path) -> None:
    paths = StoragePaths.from_settings(make_settings(tmp_path))

    write_note_markdown(paths, ITEM_ID, NOTE_ID, "first")
    write_note_markdown(paths, ITEM_ID, NOTE_ID, "second")

    assert paths.note(ITEM_ID, NOTE_ID).read_text(encoding="utf-8") == "second"


def test_translation_artifact_is_bound_to_the_source_hash_and_replaceable(tmp_path: Path) -> None:
    paths = StoragePaths.from_settings(make_settings(tmp_path))
    paths.ensure_roots()
    source = make_transcript("source")
    destination = paths.translation(ITEM_ID, "zh-CN")
    first = Translation(
        language="zh-CN",
        source_transcript_sha256=transcript_sha256(source),
        entries=[TranslationEntry(cue_id="seg_000001", text="第一版")],
    )
    replacement = first.model_copy(
        update={"entries": (TranslationEntry(cue_id="seg_000001", text="第二版"),)}
    )

    write_translation_json(paths, ITEM_ID, first, source)
    write_translation_json(paths, ITEM_ID, replacement, source)

    stored = json.loads(destination.read_text(encoding="utf-8"))
    assert stored["source_transcript_sha256"] == transcript_sha256(source)
    assert stored["entries"] == [{"cue_id": "seg_000001", "text": "第二版"}]


@pytest.mark.parametrize("invalid_case", ["wrong_hash", "missing", "extra", "reordered"])
def test_translation_write_validates_source_before_replacing_existing_artifact(
    tmp_path: Path, invalid_case: str
) -> None:
    paths = StoragePaths.from_settings(make_settings(tmp_path))
    paths.ensure_roots()
    source = make_two_segment_transcript()
    valid = Translation(
        language="zh-CN",
        source_transcript_sha256=transcript_sha256(source),
        entries=[
            TranslationEntry(cue_id="seg_000001", text="一"),
            TranslationEntry(cue_id="seg_000002", text="二"),
        ],
    )
    write_translation_json(paths, ITEM_ID, valid, source)
    destination = paths.translation(ITEM_ID, "zh-CN")
    original = destination.read_bytes()

    entries = list(valid.entries)
    source_hash = valid.source_transcript_sha256
    if invalid_case == "wrong_hash":
        source_hash = "0" * 64
    elif invalid_case == "missing":
        entries = entries[:1]
    elif invalid_case == "extra":
        entries.append(TranslationEntry(cue_id="seg_000003", text="三"))
    else:
        entries.reverse()
    invalid = Translation(
        language=valid.language,
        source_transcript_sha256=source_hash,
        entries=entries,
    )

    with pytest.raises(ValueError):
        write_translation_json(paths, ITEM_ID, invalid, source)

    assert destination.read_bytes() == original
