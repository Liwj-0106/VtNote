export type ReadinessStatus = "ready" | "partial" | "blocked";

export interface Readiness {
  status: ReadinessStatus;
  core: Record<string, boolean>;
  capabilities: Record<string, boolean>;
  local_model_state: string;
  limits: {
    max_task_sources: number;
    max_media_bytes: number;
    max_subtitle_bytes: number;
  };
}

export interface SubtitleTrack {
  id: string;
  language: string;
  format: string;
  kind: string;
  ui_label: string;
  is_translated: boolean;
  is_live_chat: boolean;
}

export interface SourceProbe {
  source_kind: "bilibili" | "youtube";
  canonical_url: string;
  title: string | null;
  duration_ms: number | null;
  subtitle_tracks: SubtitleTrack[];
}

export interface StageRun {
  id: string;
  stage: "source" | "transcribe" | "translate" | "notes";
  attempt: number;
  status: string;
  error_code: string | null;
  error_message: string | null;
  warning: string | null;
  progress: {
    current: number;
    total?: number;
    unit: string;
    message_code: string;
  } | null;
  execution_evidence: Record<string, string> | null;
  provider_status_code: string | null;
  external_submission_state: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TaskItem {
  id: string;
  position: number;
  source_kind: string;
  source_locator: string;
  source_display_name: string | null;
  status: string;
  title: string | null;
  stage_runs: StageRun[];
  created_at: string;
  updated_at: string;
}

export interface Task {
  id: string;
  status: string;
  options: Record<string, unknown>;
  pipeline_snapshot: Record<string, unknown>;
  items: TaskItem[];
  terminal_reason_code: string | null;
  created_at: string;
  updated_at: string;
}

export interface TranscriptSegment {
  id: string;
  start_ms: number;
  end_ms: number;
  text: string;
}

export interface Transcript {
  schema_version: 1;
  language: string;
  duration_ms: number;
  provenance: {
    method: string;
    provider: string;
    model?: string | null;
  };
  segments: TranscriptSegment[];
}

export interface Translation {
  schema_version: 1;
  language: string;
  source_transcript_sha256: string;
  entries: Array<{ cue_id: string; text: string }>;
}

export interface NoteResult {
  id: string;
  markdown: string;
  generated_by_ai?: boolean;
  template?: string;
  output_language?: string;
  requested_model?: string;
  response_model?: string;
}

export interface ConnectionView {
  id: string;
  name: string;
  protocol: "tencent_recording_asr" | "aliyun_bailian";
  base_url: string;
  parameters: Record<string, unknown>;
  has_secret: boolean;
  configured_fields: Record<string, boolean>;
  revision: number;
  tested: boolean;
  test_ok: boolean | null;
  test_message: string | null;
  cleanup_pending: boolean;
}

export interface ProfileView {
  id: string;
  name: string;
  purpose: "cloud_asr" | "translation" | "notes";
  connection_id: string;
  protocol: "tencent_recording_asr" | "aliyun_bailian";
  base_url: string;
  model: string;
  context_length: number;
  options: Record<string, unknown>;
  revision: number;
  tested: boolean;
  test_ok: boolean | null;
  test_message: string | null;
  upload_authorized: boolean;
  capability_fingerprint: Record<string, unknown> | null;
  chat_data_authorized: boolean;
}

export interface DefaultsView {
  asr_mode: "auto" | "cloud" | "local";
  cloud_asr_profile_id: string | null;
  translation_enabled: boolean;
  translation_profile_id: string | null;
  translation_target_language: string;
  notes_enabled: boolean;
  notes_profile_id: string | null;
  notes_template: "summary" | "key_points" | "custom";
  notes_output_language: string;
  has_custom_prompt: boolean;
  local_whisper_options: Record<string, unknown>;
}

export interface StorageSummary {
  data_root: string;
  runtime_cache_root: string;
  retention_hours: number;
  active: { count: number; size_bytes: number };
  trash: { count: number; size_bytes: number };
}

export interface TrashAsset {
  id: string;
  item_id: string;
  role: string;
  state: "trash";
  size_bytes: number;
  purge_after: string | null;
}

export interface ModelStatus {
  model_name: string;
  revision: string;
  state: string;
  total_bytes: number;
  downloaded_bytes: number;
  completed_files: number;
  current_file: string | null;
  current_file_bytes: number;
  cancel_requested: boolean;
  error_code: string | null;
}
