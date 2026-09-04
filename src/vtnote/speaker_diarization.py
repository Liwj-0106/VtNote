"""Small dependency-free acoustic clustering for optional speaker labels.

This intentionally provides coarse speaker turns, not biometric identity.  It is
derived from the prepared 16 kHz mono PCM file and never blocks transcription.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from vtnote.schemas import SpeakerAssignment, SpeakerMap, Transcript, transcript_sha256


class SpeakerDiarizationError(RuntimeError):
    pass


def _feature(samples: np.ndarray) -> np.ndarray:
    if samples.size < 320:
        raise SpeakerDiarizationError("speaker_audio_too_short")
    signal = samples.astype(np.float32) / 32768.0
    signal -= float(signal.mean())
    energy = float(np.log(np.mean(signal * signal) + 1e-8))
    zcr = float(np.mean(np.abs(np.diff(np.signbit(signal)))))
    window_size = min(4096, signal.size)
    window = signal[:window_size] * np.hanning(window_size)
    spectrum = np.log1p(np.abs(np.fft.rfft(window)))
    bands = np.array_split(spectrum, 12)
    band_energy = np.array([float(band.mean()) for band in bands], dtype=np.float32)
    vector = np.concatenate((np.array([energy, zcr], dtype=np.float32), band_energy))
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else vector


def diarize_transcript(audio_path: Path, transcript: Transcript) -> SpeakerMap:
    """Assign deterministic coarse speaker labels to canonical transcript cues."""

    try:
        with wave.open(str(audio_path), "rb") as handle:
            if handle.getnchannels() != 1 or handle.getframerate() != 16_000:
                raise SpeakerDiarizationError("speaker_audio_invalid")
            frames = handle.readframes(handle.getnframes())
    except (OSError, EOFError, wave.Error):
        raise SpeakerDiarizationError("speaker_audio_unavailable") from None
    samples = np.frombuffer(frames, dtype="<i2")
    features: list[np.ndarray | None] = []
    for segment in transcript.segments:
        start = max(0, segment.start_ms * 16)
        end = min(samples.size, segment.end_ms * 16)
        try:
            features.append(_feature(samples[start:end]))
        except SpeakerDiarizationError:
            features.append(None)
    valid = [feature for feature in features if feature is not None]
    if not valid:
        raise SpeakerDiarizationError("speaker_audio_too_short")

    # Online cosine clustering is bounded, deterministic and adequate for a
    # local optional hint. Repeated adjacent labels are intentionally stable.
    centroids: list[np.ndarray] = []
    counts: list[int] = []
    labels: list[int] = []
    previous = 0
    for feature in features:
        if feature is None:
            labels.append(previous)
            continue
        similarities = [float(np.dot(feature, centroid)) for centroid in centroids]
        if not centroids or (max(similarities) < 0.82 and len(centroids) < 8):
            label = len(centroids)
            centroids.append(feature.copy())
            counts.append(1)
        else:
            label = int(np.argmax(similarities))
            counts[label] += 1
            centroid = centroids[label] * (counts[label] - 1) + feature
            norm = float(np.linalg.norm(centroid))
            centroids[label] = centroid / norm if norm > 0 else centroid
        labels.append(label)
        previous = label
    assignments = tuple(
        SpeakerAssignment(segment_id=segment.id, speaker=f"speaker_{label + 1:02d}")
        for segment, label in zip(transcript.segments, labels, strict=True)
    )
    return SpeakerMap(
        source_transcript_sha256=transcript_sha256(transcript),
        speaker_count=len(set(labels)),
        assignments=assignments,
    )
