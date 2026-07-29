"""Cue-aligned transcript translation over the strict domestic chat contract."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence

from vtnote.artifacts import write_translation_json
from vtnote.chat import (
    AiLimits,
    ChatClient,
    ChatMessage,
    ChatProfileSnapshot,
    ChatRequest,
    canonical_chat_request_bytes,
)
from vtnote.paths import StoragePaths
from vtnote.schemas import (
    Transcript,
    TranscriptSegment,
    Translation,
    TranslationEntry,
    transcript_sha256,
)


_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,63})$")


class TranslationError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class TranslationCanceled(TranslationError):
    def __init__(self) -> None:
        super().__init__("translation_canceled")


class Translator:
    SYSTEM_PROMPT = (
        "Translate each cue into the target language in the supplied data. "
        "Preserve meaning, names, terminology, and cue boundaries. "
        "Return one JSON object with exactly this shape: "
        '{"translations":[{"cue_id":"seg_000001","text":"translated text"}]}. '
        "Return every cue exactly once, in input order, with no extra fields."
    )

    def __init__(
        self,
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> None:
        self._cancel_check = cancel_check or (lambda: False)

    def _check_canceled(self) -> None:
        if self._cancel_check():
            raise TranslationCanceled()

    @staticmethod
    def _validate_language(target_language: str) -> str:
        if (
            not isinstance(target_language, str)
            or _LANGUAGE_RE.fullmatch(target_language) is None
        ):
            raise TranslationError("translation_target_language_invalid")
        return target_language

    @classmethod
    def _request(
        cls,
        cues: Sequence[TranscriptSegment],
        target_language: str,
        profile: ChatProfileSnapshot,
    ) -> ChatRequest:
        data = {
            "target_language": target_language,
            "cues": [
                {
                    "cue_id": cue.id,
                    "start_ms": cue.start_ms,
                    "end_ms": cue.end_ms,
                    "text": cue.text,
                }
                for cue in cues
            ],
        }
        return ChatRequest(
            model=profile.model,
            messages=(
                ChatMessage(role="system", content=cls.SYSTEM_PROMPT),
                ChatMessage(
                    role="user",
                    content=json.dumps(
                        data,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            ),
            response_format="json_object",
            temperature=profile.temperature,
            max_tokens=profile.max_tokens,
            enable_thinking=profile.enable_thinking,
        )

    @classmethod
    def _fits_request(
        cls,
        cues: Sequence[TranscriptSegment],
        target_language: str,
        profile: ChatProfileSnapshot,
        limits: AiLimits,
    ) -> bool:
        return (
            len(
                canonical_chat_request_bytes(
                    cls._request(cues, target_language, profile)
                )
            )
            <= limits.max_request_bytes
        )

    @classmethod
    def _batches(
        cls,
        transcript: Transcript,
        target_language: str,
        profile: ChatProfileSnapshot,
        limits: AiLimits,
    ) -> tuple[tuple[TranscriptSegment, ...], ...]:
        batches: list[tuple[TranscriptSegment, ...]] = []
        current: list[TranscriptSegment] = []
        for cue in transcript.segments:
            candidate = [*current, cue]
            if (
                len(candidate) <= limits.translation_batch_cues
                and cls._fits_request(
                    candidate,
                    target_language,
                    profile,
                    limits,
                )
            ):
                current = candidate
                continue
            if current:
                batches.append(tuple(current))
                current = []
            if not cls._fits_request(
                (cue,),
                target_language,
                profile,
                limits,
            ):
                raise TranslationError("translation_cue_oversize")
            current = [cue]
        if current:
            batches.append(tuple(current))
        return tuple(batches)

    @staticmethod
    def _parse_entries(
        content: str,
        expected_cues: Sequence[TranscriptSegment],
        limits: AiLimits,
    ) -> tuple[TranslationEntry, ...]:
        if len(content.encode("utf-8")) > limits.max_response_bytes:
            raise TranslationError("translation_response_oversize")
        try:
            payload = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            raise TranslationError("translation_response_invalid") from None
        if not isinstance(payload, dict) or set(payload) != {"translations"}:
            raise TranslationError("translation_response_invalid")
        raw_entries = payload["translations"]
        if not isinstance(raw_entries, list):
            raise TranslationError("translation_response_invalid")
        expected_ids = [cue.id for cue in expected_cues]
        if len(raw_entries) != len(expected_ids):
            raise TranslationError("translation_response_invalid")
        entries: list[TranslationEntry] = []
        for raw, expected_id in zip(raw_entries, expected_ids, strict=True):
            if (
                not isinstance(raw, dict)
                or set(raw) != {"cue_id", "text"}
                or raw.get("cue_id") != expected_id
                or not isinstance(raw.get("text"), str)
                or not raw["text"].strip()
            ):
                raise TranslationError("translation_response_invalid")
            entries.append(
                TranslationEntry(
                    cue_id=expected_id,
                    text=raw["text"].strip(),
                )
            )
        return tuple(entries)

    def _call_batch(
        self,
        cues: Sequence[TranscriptSegment],
        target_language: str,
        profile: ChatProfileSnapshot,
        client: ChatClient,
        limits: AiLimits,
    ) -> tuple[TranslationEntry, ...]:
        self._check_canceled()
        request = self._request(cues, target_language, profile)
        if len(canonical_chat_request_bytes(request)) > limits.max_request_bytes:
            raise TranslationError("translation_cue_oversize")
        response = client.complete(request)
        self._check_canceled()
        return self._parse_entries(response.content, cues, limits)

    def _translate_batch(
        self,
        cues: tuple[TranscriptSegment, ...],
        target_language: str,
        profile: ChatProfileSnapshot,
        client: ChatClient,
        limits: AiLimits,
    ) -> tuple[TranslationEntry, ...]:
        try:
            return self._call_batch(
                cues,
                target_language,
                profile,
                client,
                limits,
            )
        except TranslationError as error:
            if error.code != "translation_response_invalid":
                raise
        retry_size = limits.translation_retry_batch_cues
        retry_batches = tuple(
            cues[index : index + retry_size]
            for index in range(0, len(cues), retry_size)
        )
        if len(retry_batches) > 2:
            raise TranslationError("translation_response_invalid")
        retried: list[TranslationEntry] = []
        for retry_batch in retry_batches:
            retried.extend(
                self._call_batch(
                    retry_batch,
                    target_language,
                    profile,
                    client,
                    limits,
                )
            )
        return tuple(retried)

    def translate(
        self,
        transcript: Transcript,
        target_language: str,
        profile: ChatProfileSnapshot,
        client: ChatClient,
        limits: AiLimits,
    ) -> Translation:
        if not isinstance(transcript, Transcript):
            raise ValueError("Transcript is required")
        if not isinstance(profile, ChatProfileSnapshot):
            raise ValueError("ChatProfileSnapshot is required")
        if not isinstance(limits, AiLimits):
            raise ValueError("AiLimits is required")
        language = self._validate_language(target_language)
        self._check_canceled()
        translated_entries: list[TranslationEntry] = []
        for batch in self._batches(transcript, language, profile, limits):
            translated_entries.extend(
                self._translate_batch(
                    batch,
                    language,
                    profile,
                    client,
                    limits,
                )
            )
        self._check_canceled()
        translation = Translation(
            language=language,
            source_transcript_sha256=transcript_sha256(transcript),
            entries=tuple(translated_entries),
        )
        return translation.validate_against(transcript)

    def translate_and_write(
        self,
        transcript: Transcript,
        target_language: str,
        profile: ChatProfileSnapshot,
        client: ChatClient,
        limits: AiLimits,
        *,
        paths: StoragePaths,
        item_id: str,
    ) -> Translation:
        translation = self.translate(
            transcript,
            target_language,
            profile,
            client,
            limits,
        )
        self._check_canceled()
        write_translation_json(paths, item_id, translation, transcript)
        return translation
