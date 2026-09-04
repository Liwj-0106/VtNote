export type ReadinessStatus = "ready" | "partial" | "blocked";

export type LocalAsrEngine = "faster_whisper" | "sensevoice_sherpa_onnx";

export interface Readiness {
  status: ReadinessStatus;
  core: Record<string, boolean>;
  capabilities: Record<string, boolean>;
  local_model_state: string;
  local_asr_engines?: Partial<Record<LocalAsrEngine, { state: string }>>;
  limits: {
    max_task_sources: number;
    max_batch_sources: number;
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
  result_type: "single" | "collection";
  source_kind: "bilibili" | "douyin" | "youtube";
  canonical_url: string;
  title: string | null;
  duration_ms: number | null;
  author?: string;
  published_at?: string;
  thumbnail_url?: string;
  description?: string;
  subtitle_tracks: SubtitleTrack[];
  collection?: SourceCollection;
}

export interface SourceCollectionItem {
  id: string;
  canonical_url: string;
  title: string;
  duration_ms: number | null;
}

export interface SourceCollection {
  id: string;
  title: string;
  total_items: number;
  truncated: boolean;
  items: SourceCollectionItem[];
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
  thumbnail_url?: string | null;
  published_at?: string | null;
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

export interface SpeakerMap {
  schema_version: 1;
  source_transcript_sha256: string;
  method: "local_acoustic_clustering" | "moss_transcribe_diarize";
  speaker_count: number;
  assignments: Array<{ segment_id: string; speaker: string }>;
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
  protocol:
    | "tencent_recording_asr"
    | "aliyun_bailian"
    | "tencent_tokenhub"
    | "openai_chat_completions"
    | "anthropic_messages"
    | "google_gemini"
    | "azure_openai";
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
  protocol:
    | "tencent_recording_asr"
    | "aliyun_bailian"
    | "tencent_tokenhub"
    | "openai_chat_completions"
    | "anthropic_messages"
    | "google_gemini"
    | "azure_openai";
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

export interface SpeechTestSample {
  id: string;
  duration_ms: number;
  size_bytes: number;
  available_for_minutes: number;
}

export interface DefaultsView {
  asr_mode: "auto" | "cloud" | "local";
  local_asr_engine: LocalAsrEngine;
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

export interface NotesPromptView {
  prompt: string;
  is_custom: boolean;
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

export interface ExportSettings {
  directory: string;
  default_directory: string;
  is_default: boolean;
}

export interface SavedExport {
  directory: string;
  files: Array<{ kind: "audio" | "transcript" | "notes" | "archive"; filename: string }>;
}

export interface BatchProbeResult {
  input_url: string;
  canonical_url?: string;
  title?: string | null;
  source_kind?: "bilibili" | "douyin" | "youtube";
  status: "ready" | "failed" | "duplicate" | "collection_requires_separate_import";
  duplicate_of?: number;
  error_code?: string;
}

export interface BatchSourceProbe {
  results: BatchProbeResult[];
  valid_sources: Array<{
    kind: "bilibili" | "douyin" | "youtube";
    url: string;
  }>;
}

export interface LibraryEntity {
  id: string;
  name: string;
  created_at?: string;
  updated_at?: string;
  task_count?: number;
}

export interface LibraryMetadata {
  collections: LibraryEntity[];
  tags: LibraryEntity[];
  total_count?: number;
  unclassified_count?: number;
}

export interface LibraryOrganization {
  collections: LibraryEntity[];
  tags: LibraryEntity[];
}

export interface LibraryMatch {
  kind: "title" | "source" | "transcript" | "note" | "excerpt";
  item_id: string;
  segment_id: string | null;
  start_ms: number | null;
  end_ms: number | null;
  snippet: string;
}

export interface LibrarySearchResult {
  task: Task;
  match: LibraryMatch | null;
  collections: LibraryEntity[];
  tags: LibraryEntity[];
}

export interface LibraryExcerpt {
  id: string;
  item_id: string;
  segment_id: string;
  start_ms: number;
  end_ms: number;
  text: string;
  note: string | null;
  stale: boolean;
  created_at: string;
  updated_at: string;
}
