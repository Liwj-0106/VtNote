import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import { api } from "../../api/client";
import type { TranscriptSegment } from "../../api/types";
import { formatTimestamp } from "../../app/format";
import { PauseIcon, PlayIcon } from "../../app/icons";

type Outcomes = {
  audio: boolean;
};

const PLAYBACK_RATES = [0.75, 1, 1.25, 1.5, 2] as const;

export type TranscriptPlayerHandle = {
  seekTo: (startMs: number) => void;
};

function activeSegmentAt(segments: TranscriptSegment[], currentMs: number) {
  let low = 0;
  let high = segments.length - 1;
  let candidate: TranscriptSegment | null = null;
  while (low <= high) {
    const middle = Math.floor((low + high) / 2);
    const segment = segments[middle];
    if (segment.start_ms <= currentMs) {
      candidate = segment;
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  return candidate && currentMs < candidate.end_ms ? candidate.id : null;
}

export const TranscriptPlayer = forwardRef<
  TranscriptPlayerHandle,
  {
    itemId: string;
    segments: TranscriptSegment[];
    onActiveSegmentChange: (segmentId: string | null) => void;
    onAvailabilityChange?: (available: boolean) => void;
  }
>(function TranscriptPlayer(
  { itemId, segments, onActiveSegmentChange, onAvailabilityChange },
  ref,
) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const pendingSeek = useRef<number | null>(null);
  const [available, setAvailable] = useState<boolean | null>(null);
  const [playing, setPlaying] = useState(false);
  const [currentSeconds, setCurrentSeconds] = useState(0);
  const [durationSeconds, setDurationSeconds] = useState(
    (segments.at(-1)?.end_ms ?? 0) / 1000,
  );
  const [playbackRate, setPlaybackRate] = useState(1);

  useEffect(() => {
    const controller = new AbortController();
    api
      .request<Outcomes>(`/api/items/${itemId}/outcomes`, {
        signal: controller.signal,
      })
      .then((outcomes) => {
        setAvailable(outcomes.audio);
        onAvailabilityChange?.(outcomes.audio);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setAvailable(false);
          onAvailabilityChange?.(false);
        }
      });
    return () => controller.abort();
  }, [itemId, onAvailabilityChange]);

  useEffect(() => {
    setCurrentSeconds(0);
    setDurationSeconds((segments.at(-1)?.end_ms ?? 0) / 1000);
  }, [itemId, segments]);

  useImperativeHandle(ref, () => ({
    seekTo(startMs: number) {
      const nextSeconds = Math.max(0, startMs / 1000);
      setCurrentSeconds(nextSeconds);
      const element = audio.current;
      if (!element || element.readyState < 1) {
        pendingSeek.current = nextSeconds;
        element?.load();
        return;
      }
      element.currentTime = nextSeconds;
      void element.play();
    },
  }));

  const updateTime = (element: HTMLAudioElement) => {
    setCurrentSeconds(element.currentTime);
    onActiveSegmentChange(
      activeSegmentAt(segments, Math.round(element.currentTime * 1000)),
    );
  };

  const togglePlayback = () => {
    const element = audio.current;
    if (!element) return;
    if (element.paused) void element.play();
    else element.pause();
  };

  const cyclePlaybackRate = () => {
    const currentIndex = PLAYBACK_RATES.indexOf(
      playbackRate as (typeof PLAYBACK_RATES)[number],
    );
    const nextRate = PLAYBACK_RATES[(currentIndex + 1) % PLAYBACK_RATES.length];
    setPlaybackRate(nextRate);
    if (audio.current) audio.current.playbackRate = nextRate;
  };

  if (!available) return null;

  return (
    <div className="transcript-player" role="group" aria-label="音频播放">
      <audio
        ref={audio}
        preload="none"
        src={`/api/items/${encodeURIComponent(itemId)}/audio?format=m4a&inline=true`}
        onCanPlay={(event) => {
          if (pendingSeek.current === null) return;
          event.currentTarget.currentTime = pendingSeek.current;
          pendingSeek.current = null;
          void event.currentTarget.play();
        }}
        onDurationChange={(event) =>
          setDurationSeconds(
            Number.isFinite(event.currentTarget.duration)
              ? event.currentTarget.duration
              : 0,
          )
        }
        onTimeUpdate={(event) => updateTime(event.currentTarget)}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => {
          setPlaying(false);
          onActiveSegmentChange(null);
        }}
        onError={() => {
          setAvailable(false);
          onAvailabilityChange?.(false);
        }}
      />
      <button
        type="button"
        className="transcript-player-toggle"
        aria-label={playing ? "暂停" : "播放"}
        onClick={togglePlayback}
      >
        {playing ? <PauseIcon /> : <PlayIcon />}
      </button>
      <time>{formatTimestamp(Math.round(currentSeconds * 1000))}</time>
      <input
        type="range"
        min="0"
        max={Math.max(durationSeconds, 0.1)}
        step="0.1"
        value={Math.min(currentSeconds, Math.max(durationSeconds, 0.1))}
        aria-label="播放位置"
        onChange={(event) => {
          const nextSeconds = Number(event.currentTarget.value);
          setCurrentSeconds(nextSeconds);
          if (audio.current?.readyState) audio.current.currentTime = nextSeconds;
          else pendingSeek.current = nextSeconds;
        }}
      />
      <time>{formatTimestamp(Math.round(durationSeconds * 1000))}</time>
      <button
        type="button"
        className="transcript-player-rate"
        aria-label={`播放速度 ${playbackRate} 倍`}
        onClick={cyclePlaybackRate}
      >
        {playbackRate}×
      </button>
    </div>
  );
});
