"""Canonical, provider-independent transcript and translation schemas."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProvenanceMethod(str, Enum):
    """Stable transcript acquisition methods in the v1 artifact contract."""

    PLATFORM_SUBTITLE = "platform_subtitle"
    CLOUD_ASR = "cloud_asr"
    LOCAL_ASR = "local_asr"


class CanonicalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Provenance(CanonicalModel):
    method: ProvenanceMethod
    provider: str = Field(min_length=1)
    model: str | None = Field(default=None, min_length=1)


class TranscriptSegment(CanonicalModel):
    id: str = Field(pattern=r"^seg_\d{6}$")
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        return self


class Transcript(CanonicalModel):
    schema_version: Literal[1] = 1
    language: str = Field(min_length=1)
    duration_ms: int = Field(ge=0)
    provenance: Provenance
    segments: tuple[TranscriptSegment, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_timeline(self) -> Self:
        segment_ids = [segment.id for segment in self.segments]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError("segment IDs must be unique")
        timeline = [(segment.start_ms, segment.end_ms) for segment in self.segments]
        if timeline != sorted(timeline):
            raise ValueError("cues must be chronological")
        if self.duration_ms < max(segment.end_ms for segment in self.segments):
            raise ValueError("duration_ms must cover the last cue")
        return self


class TranslationEntry(CanonicalModel):
    cue_id: str = Field(pattern=r"^seg_\d{6}$")
    text: str = Field(min_length=1)


class Translation(CanonicalModel):
    schema_version: Literal[1] = 1
    language: str = Field(min_length=1)
    source_transcript_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entries: tuple[TranslationEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_cue_ids(self) -> Self:
        cue_ids = [entry.cue_id for entry in self.entries]
        if len(cue_ids) != len(set(cue_ids)):
            raise ValueError("translation cue IDs must be unique")
        return self

    def validate_against(self, transcript: Transcript) -> Self:
        if self.source_transcript_sha256 != transcript_sha256(transcript):
            raise ValueError("translation source transcript hash does not match")
        expected = [segment.id for segment in transcript.segments]
        actual = [entry.cue_id for entry in self.entries]
        if actual != expected:
            raise ValueError("translation cue IDs must exactly match the source transcript")
        return self


def canonical_transcript_bytes(transcript: Transcript) -> bytes:
    """Serialize a transcript to deterministic UTF-8 JSON used for storage and hashing."""

    payload = transcript.model_dump(mode="json", exclude_none=True)
    payload["provenance"]["model"] = transcript.provenance.model
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (text + "\n").encode("utf-8")


def transcript_sha256(transcript: Transcript) -> str:
    return hashlib.sha256(canonical_transcript_bytes(transcript)).hexdigest()


def canonical_translation_bytes(translation: Translation) -> bytes:
    """Serialize a translation artifact to deterministic UTF-8 JSON."""

    payload = translation.model_dump(mode="json", exclude_none=True)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (text + "\n").encode("utf-8")
