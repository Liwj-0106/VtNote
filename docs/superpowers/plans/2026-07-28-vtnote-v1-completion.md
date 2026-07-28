# VtNote V1 Remaining Development Implementation Plan

> **For Codex:** REQUIRED SUB-SKILLS: use `superpowers:subagent-driven-development`,
> `superpowers:test-driven-development`, `superpowers:requesting-code-review`, and
> `superpowers:verification-before-completion`. Execute one implementation task at a time.

**Goal:** Complete the VtNote V1 path from supported URL/local input to immutable transcript,
optional translation/AI notes, local web UI, supervised worker, safe exports, and release evidence.

**Architecture:** Keep FastAPI and a separate durable SQLite worker. Platform acquisition,
transcription, and chat are explicit typed adapters. `transcript.json` is the only immutable source
of truth; translation and notes are independent derived branches. React is built once and served
same-origin by FastAPI on loopback.

**Tech stack:** Python 3.11 Conda, FastAPI, SQLAlchemy 2, SQLite WAL, httpx, yt-dlp,
yt-dlp-ejs/Deno, FFmpeg/FFprobe, faster-whisper/CTranslate2, React, TypeScript, Vite, Vitest,
Testing Library, Playwright.

**Baseline:** `e4a8fdfe137c04d19e1ac9cd6dfd44520b73d5eb`; 238 backend tests passed before this
plan. The historical plan remains at `docs/superpowers/plans/2026-07-18-vtnote-v1.md`.

---

## Global constraints

Every task inherits these constraints and must test the ones it touches:

1. Production binds only `127.0.0.1:8765`. React and FastAPI are same-origin. No permissive
   CORS. Every mutation checks exact Host, Origin, and double-submit CSRF.
2. Code is under `D:\Workspace\Project\VtNote`; persistent data defaults to
   `D:\Workspace\Project\VtNote-data`; runtime cache defaults to
   `D:\Workspace\Codex\cache\VtNote-runtime`. Tests use a task-specific directory under
   `D:\Workspace\Codex\cache\`.
3. User-owned media/subtitles are read-only and never deleted. App-owned disposable assets move
   into a recoverable 24-hour trash area before purge. Mutations, moves, failed cleanup, and purge
   are audited. A material deletion is backed up under the task-specific Codex cache first.
4. `transcript.json` is deterministic and immutable. SRT/VTT/TXT/Markdown are generated on demand.
   Translation stores the source transcript SHA-256 and has exactly one text for every cue ID.
5. Subtitle order is preferred-language manual, preferred-language automatic, other-language
   manual, other-language automatic; within a group VTT, SRT, ASS, JSON, then stable opaque track
   ID. Only a typed candidate error advances to the next track.
6. Platform HTTP is direct-only and never inherits proxy, PAC, SOCKS, netrc, Cookie, or browser
   state. DNS, redirects, connected peer IP, allowlist, and response size are checked at every hop.
7. The initial YouTube runtime is `yt-dlp==2026.7.4` (runtime display `2026.07.04`),
   `yt-dlp-ejs==0.8.0`, and Deno `2.8.1` Windows x64. Assets, hashes, and `DENO_DIR` live on D.
   System Node, automatic upgrades, plugins, and remote EJS components are forbidden. Missing
   EJS/Deno disables only YouTube URL input.
8. Cloud ASR uses only Tencent Cloud API 3.0
   `POST https://asr.tencentcloudapi.com/`, actions `CreateRecTask` and
   `DescribeTaskStatus`, version `2019-06-14`, region `ap-guangzhou`, model
   `16k_zh_en_2.0`, mono channel, `ResTextFormat=3`, and `SentenceMaxLength=20`.
9. Tencent audio is 16 kHz mono 32 kbps OGG/Opus. Encoded bytes `<=4,500,000` use one
   `SourceType=1` Base64 request. Larger eligible audio uses one private `ap-guangzhou` COS object and a
   six-hour single-object pre-signed GET URL. THR-002 caps duration at five hours and encoded audio
   at 96 MiB. V1 has no cloud slicing, public bucket, automatic bucket creation, Flash endpoint, or
   second paid ASR.
10. `CreateRecTask` has no documented idempotency key. Persist the returned uint64 `TaskId` as a
    string before polling and resume only `DescribeTaskStatus` after a crash. A possibly-sent create
    without `TaskId` is `submission_unknown` and is never repeated automatically. Strictly validate
    `ResultDetail` timestamps; query retry is safe and bounded by the 24-hour result window.
    Persist `next_poll_at` and let a submission reconciler perform one due query per claim; never
    sleep/poll inside the sole worker. Provider `success|failed` deletes COS immediately.
    `submission_unknown` or local cancel retains COS until a known remote terminal state or the
    six-hour URL expiry plus a 30-minute cleanup grace.
11. Persist only bounded RequestId/TaskId, provider status, audio hash, safe COS bucket/object
    locator, and safe error code. Never persist credentials, Base64, pre-signed URL, provider
    message, headers, raw response, or absolute user path. Custom prompts are the sole
    sensitive-text exception: defaults and task snapshots store only a versioned DPAPI-protected
    envelope, never plaintext.
12. Provider credentials are versioned atomic secret bundles in Credential Manager/DPAPI. Changing
    any field rotates the whole bundle, increments connection revision, and invalidates test/upload
    consent. Tencent V1 supports only SecretId/SecretKey, not non-refreshable temporary STS tokens.
13. Local ASR is fixed at faster-whisper `1.2.1`, CTranslate2 `4.8.1`,
    `large-v3-turbo`, `device="cuda"`, `compute_type="int8_float16"`, VAD on, segment timestamps,
    and one GPU stage globally. The model is the fixed Dropbox Dash revision/hash in ADR-008.
    CPU is diagnostic only; no silent fallback. A durable, explicit D-drive installer owns model
    download, verification, cancellation, recovery, and atomic publication; task execution uses
    `local_files_only`.
14. Translation and notes both depend only on transcribe and may run independently. V1 calls only
    the official Aliyun Bailian China (Beijing) workspace Chat Completions endpoint through an
    explicit adapter; arbitrary/foreign/relay endpoints are rejected. Translation is off by
    default. Notes use original transcript, default to `zh-Hans` while honoring an explicit task
    output language, and expose summary, key points, and custom prompt templates. Static policy
    validation, billable profile capability testing, and revision-bound real-text consent are
    distinct states; a profile is usable only after the latter two are current.
15. Translation accepts complete, unique, ordered cue IDs; a structural failure gets exactly one
    retry round that splits the failed at-most-30-cue batch into at most two at-most-15-cue
    subbatches. Every translation/map/reduce request is non-streaming `json_object`, bounded by cue
    count and UTF-8 bytes. Notes publish only citations that resolve to cue ID/start/end in the
    current chunk or explicit citation lineage.
16. Optional branch failure produces `completed_with_warnings`; it never hides, deletes, or reruns
    successful source/transcribe. A stage retry creates a new attempt.
17. An unknown paid outcome has no ordinary one-click retry.
    `POST /api/tasks/{id}/retry` requires `expected_attempt`; it accepts `strategy=local`, or
    `strategy=cloud_confirmed` with `acknowledge_possible_charge=true`, current cloud profile ID,
    and connection/profile revisions. The new attempt stores an explicit override snapshot and
    never reads current defaults. A `chat_submission_unknown` AI retry keeps `strategy=same` but
    also requires `acknowledge_possible_charge=true`.
18. THR-001 through THR-005 use the implementation values in ADR-014. The UI reads them from the
    server. The 30–50 item POC may change them before release.
19. Default tests have no live socket, credentials, or billable calls. Live POC is explicit,
    separately marked, and uses user-authorized content.
20. Actual release FFmpeg, Deno/EJS, CUDA libraries, models, wheels, and transitive components need
    hashes, source/notice/license evidence and SBOM. Prefer an audited LGPL FFmpeg build; use a GPL
    build only with an explicit distribution decision and complete obligations.
21. Database upgrade archives legacy Volc/arbitrary `openai_compatible` connections and clears their
    defaults/tests/consents. Historical rows remain read-only; queued/running legacy snapshots fail
    closed before secret access or network I/O and offer local ASR or reconfigured domestic AI.
22. V1 excludes login-only/private/paid/DRM media, Cookie/browser helpers, proxy support, playlists,
    batching, OCR, screenshots, RAG/chat, player/editor, desktop shell, plugins, auto-updaters,
    foreign model APIs, and unreviewed model relays.
23. Production changes are test-first: focused RED for the expected reason, minimal GREEN,
    focused/full verification, independent specification review, independent code/security review,
    then one intentional commit.

## Shared verification commands

Use a fresh task directory for each task:

```powershell
$env:PYTHONPYCACHEPREFIX='D:\Workspace\Codex\cache\VtNote-completion\<task>\pycache'
& 'D:\ProgramData\Anaconda3\envs\vtnote\python.exe' -m pytest -q -p no:cacheprovider `
  --basetemp='D:\Workspace\Codex\cache\VtNote-completion\<task>\pytest'
& 'D:\ProgramData\Anaconda3\envs\vtnote\python.exe' -m compileall -q src tests
& 'D:\ProgramData\Anaconda3\envs\vtnote\python.exe' -m pip check
git diff --check
```

Frontend tasks additionally use an npm cache under
`D:\Workspace\Codex\cache\VtNote-completion\npm-cache`:

```powershell
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run test -- --run
npm --prefix frontend run build
```

Before dispatching a task, the controller creates its SDD brief with one checkbox per numbered
step, the exact named tests below, the focused RED command, and the expected failure reason.
Implementation must not begin until that RED output is recorded. A task report records GREEN,
full-suite, review, and commit evidence.

| Task | Required named-test focus | Focused RED/GREEN command | Initial expected RED |
|---|---|---|---|
| 1 | canceled-idempotency, stage-evidence round-trip, upload total formula | `python -m pytest -q tests/test_database.py tests/test_tasks.py tests/test_pipeline_contract.py tests/test_uploads.py` | missing columns; canceled rejection; old request formula |
| 1A | DPAPI protection, atomic legacy migration/rollback, public redaction | `python -m pytest -q tests/test_sensitive_text.py tests/test_configuration.py tests/test_tasks.py tests/test_api.py` | missing protector; plaintext snapshot |
| 1B | stale attempt, local/cloud-confirmed overrides, charge acknowledgement | `python -m pytest -q tests/test_tasks.py tests/test_api.py -k retry` | retry DTO/override absent |
| 2 | dependency-ready claim, atomic lease, stale owner, GPU lease | `python -m pytest -q tests/test_worker_leases.py tests/test_worker_stage_transitions.py` | worker modules absent |
| 3 | shared probe type, opaque `trk_` refs, typed candidate failure | `python -m pytest -q tests/test_source_contracts.py tests/test_api.py -k source` | duplicate DTO/current track shape |
| 4 | hostile proxy, DNS/peer pin, redirect, body limit | `python -m pytest -q tests/test_platform_transport.py tests/test_url_security.py` | transport absent |
| 5 | sole yt-dlp handler, runtime version/hash, no Node/remote component | `python -m pytest -q tests/test_ytdlp_bridge.py tests/test_youtube_runtime.py` | bridge/runtime absent |
| 6 | platform probe/tracks/fetch/audio-once/error taxonomy | `python -m pytest -q tests/test_platform_sources.py tests/test_api.py -k probe` | adapters absent |
| 7 | subtitle-zero-audio, fatal-stop, audio handoff, crash publish | `python -m pytest -q tests/test_source_stage.py` | handler absent |
| 8 | Tencent secret bundle, fixed error map, legacy quarantine, due-query/COS state | `python -m pytest -q tests/test_provider_credentials.py tests/test_tencent_contract.py tests/test_cloud_submissions.py tests/test_configuration.py tests/test_database.py tests/test_tasks.py tests/test_api.py` | bundle/contract/submission model absent |
| 9 | TC3 create/query, one-query claim, Base64/COS deferred cleanup, speech profile test | `python -m pytest -q tests/test_tencent_asr_adapter.py tests/test_tencent_cos.py tests/test_api.py -k tencent` | clients/tester/reconciler absent |
| 10A | manifest/hash, durable install, resume/cancel/atomic publish | `python -m pytest -q tests/test_model_assets.py tests/test_api.py -k local_whisper` | manifest/service/routes absent |
| 10 | GPU-only load, legacy migration/rejection, lazy segment iteration | `python -m pytest -q tests/test_local_whisper_adapter.py tests/test_configuration.py tests/test_database.py` | adapter absent; `device=auto` |
| 11 | ASR modes, fallback/unknown, one immutable publish | `python -m pytest -q tests/test_worker_asr_e2e.py` | transcribe handler absent |
| 12 | Bailian canonical workspace, capability/text consent, ambiguous POST, legacy quarantine | `python -m pytest -q tests/test_bailian_chat_adapter.py tests/test_configuration.py tests/test_database.py tests/test_tasks.py tests/test_api.py` | chat client/consent/migration absent |
| 13 | cue/byte batches, json every call, exact IDs, split retry, source hash | `python -m pytest -q tests/test_translation_processor.py` | translator absent |
| 14 | templates/bytes/protected prompt, JSON map/reduce, citation lineage, AI labels | `python -m pytest -q tests/test_notes_processor.py` | generator absent |
| 15 | consent-gated branches, chat unknown acknowledgement, warning/stage retry | `python -m pytest -q tests/test_worker_ai_e2e.py` | AI handlers absent |
| 16 | readiness/read models/trash restore/execution export/API p95 | `python -m pytest -q tests/test_readiness.py tests/test_api.py tests/test_exports.py tests/test_runtime_assets.py` | routes/read models absent |
| 17 | CSRF/no replay/abort/poll schedule/no browser storage | `npm --prefix frontend run test -- --run src/api/client.test.ts` | frontend absent |
| 18 | setup/settings/secrets/model install/trash restore | `npm --prefix frontend run test -- --run src/pages/SetupPage.test.tsx src/pages/SettingsPage.test.tsx src/pages/ConnectionsPage.test.tsx src/pages/StoragePage.test.tsx` | pages absent |
| 19 | probe/upload/review/deduplicated create/rights notice | `npm --prefix frontend run test -- --run src/pages/CreateTaskPage.test.tsx src/components/FilePicker.test.tsx src/components/ProfileSelect.test.tsx` | page/components absent |
| 20 | history/detail/retry/result/citation/a11y/full-text mode | `npm --prefix frontend run test -- --run src/pages/TaskHistoryPage.test.tsx src/pages/TaskDetailPage.test.tsx src/components/TranscriptViewer.test.tsx` | pages/components absent |
| 21 | supervisor lifecycle/loopback/static deep link/API 404 | `python -m pytest -q tests/test_launcher.py tests/test_static_app.py` | launcher/static app absent |
| 22 | logging redaction/rotation/maintenance/diagnostic bundle | `python -m pytest -q tests/test_logging_security.py tests/test_maintenance.py` | logging/maintenance absent |
| 23 | lock/hash/SBOM/buildconf/license evidence | `python -m pytest -q tests/test_release_evidence.py` | evidence tool/files absent |
| 24 | offline pipeline/crash/security plus browser journeys | `python -m pytest -q tests/e2e && npm --prefix frontend run e2e` | E2E suites absent |
| 25 | manifest gates/redacted resume/aggregate metrics | `python -m pytest -q tests/test_poc_harness.py` | harness absent |

---

### Task 1: Close lifecycle and evidence-model contract debt

**Files:**

- Modify: `src/vtnote/models.py`
- Modify: `src/vtnote/database.py`
- Modify: `src/vtnote/tasks.py`
- Modify: `src/vtnote/pipeline.py`
- Modify: `src/vtnote/uploads.py`
- Modify: `tests/test_database.py`
- Modify: `tests/test_tasks.py`
- Modify: `tests/test_pipeline_contract.py`
- Modify: `tests/test_uploads.py`

**Interfaces:**

```python
class StageProgress(TypedDict):
    current: int | None
    total: int | None
    unit: Literal["bytes", "segments", "cues", "chunks", "items"] | None
    message_code: str

class ExecutionEvidence(TypedDict, total=False):
    source_method: str
    selected_track_id: str
    asr_route: str
    provider: str
    model: str
    fallback_reason: str

TaskService.cancel_task(task_id: str) -> TaskView
```

**Steps:**

1. Add failing tests proving repeated cancellation of an already canceled task returns the current
   view; completed/failed tasks remain rejected; stage progress/evidence/provider status round-trip
   without secrets or arbitrary provider text; and
   `max_request_bytes=max(media_limit, subtitle_limit)+metadata_limit+overhead_limit`.
2. Run `tests/test_database.py tests/test_tasks.py tests/test_pipeline_contract.py
   tests/test_uploads.py` and confirm the new assertions fail for missing columns/behavior and the
   current upload request-size formula.
3. Add additive SQLite columns `progress_json`, `execution_evidence_json`, and
   `provider_status_code`; validate bounded typed values at service boundaries.
4. Make cancel idempotent only when the terminal reason is user cancellation. Preserve optimistic
   concurrency and existing retry conflict rules.
5. Run focused tests, the shared backend verification, specification review, and code/security
   review.
6. Commit: `feat: close task lifecycle and stage evidence contracts`.

### Task 1A: Protect custom prompts across defaults, snapshots, and retries

**Files:**

- Create: `src/vtnote/sensitive_text.py`
- Create: `tests/test_sensitive_text.py`
- Modify: `src/vtnote/models.py`
- Modify: `src/vtnote/database.py`
- Modify: `src/vtnote/configuration.py`
- Modify: `src/vtnote/tasks.py`
- Modify: `src/vtnote/api.py`
- Modify: `tests/test_configuration.py`
- Modify: `tests/test_tasks.py`
- Modify: `tests/test_api.py`

**Interfaces:**

```python
class SensitiveTextProtector(Protocol):
    def protect(self, purpose: str, plaintext: str) -> ProtectedTextEnvelope: ...
    def unprotect(self, purpose: str, envelope: ProtectedTextEnvelope) -> str: ...

class ProtectedTextEnvelope(BaseModel):
    schema_version: Literal[1]
    protection: Literal["windows_dpapi_current_user"]
    ciphertext_b64: str
```

**Steps:**

1. Add named RED tests
   `test_custom_prompt_is_dpapi_protected_in_defaults_and_task_snapshot`,
   `test_public_views_return_only_has_custom_prompt`,
   `test_retry_decrypts_the_same_immutable_prompt`,
   `test_legacy_plaintext_default_is_protected_and_cleared`,
   `test_legacy_plaintext_snapshot_is_atomically_protected_and_cleared`,
   `test_sensitive_text_migration_rolls_back_and_blocks_on_protection_failure`, and
   `test_prompt_never_enters_log_error_export_url_or_browser_storage`.
2. Run
   `python -m pytest -q tests/test_sensitive_text.py tests/test_configuration.py
   tests/test_tasks.py tests/test_api.py`; expected RED is the absent protector and current plaintext
   `notes_custom_prompt` snapshot.
3. Implement injected Windows DPAPI protection, a test fake, additive protected columns/envelope,
   and one atomic startup migration that protects all legacy defaults/snapshots and clears plaintext.
   If any row fails, roll back the batch, publish only `sensitive_snapshot_migration_required`, and
   block affected execution. Do not return decrypted text from GET APIs.
4. Creation/replacement requests may contain plaintext in memory; protect it before commit. Notes
   execution decrypts only inside the attempt and never attaches the value to diagnostics.
5. Run the focused command, shared backend verification, and two reviews.
6. Commit: `feat: protect custom prompts at rest`.

### Task 1B: Add explicit ASR retry override contracts

**Files:**

- Modify: `src/vtnote/models.py`
- Modify: `src/vtnote/tasks.py`
- Modify: `src/vtnote/api.py`
- Modify: `src/vtnote/configuration.py`
- Modify: `tests/test_tasks.py`
- Modify: `tests/test_api.py`

**Interfaces:**

```python
class RetryInput(InputModel):
    item_id: str
    stage: str
    expected_attempt: int
    strategy: Literal["same", "local", "cloud_confirmed"] = "same"
    cloud_profile_id: str | None = None
    connection_revision: int | None = None
    profile_revision: int | None = None
    acknowledge_possible_charge: bool = False

TaskService.retry_stage(
    item_id: str,
    stage: str,
    expected_attempt: int,
    override: RetryOverrideSnapshot,
) -> ItemView
```

**Steps:**

1. Add named RED tests
   `test_retry_rejects_stale_expected_attempt`,
   `test_unknown_cloud_rejects_same_retry`,
   `test_unknown_cloud_local_retry_snapshots_local_override`,
   `test_unknown_cloud_requires_charge_ack_for_cloud_retry`,
   `test_cloud_confirmed_requires_current_tested_authorized_revisions`, and
   `test_retry_override_never_reads_current_defaults`.
2. Run `python -m pytest -q tests/test_tasks.py tests/test_api.py`; expected RED is the current
   two-field retry payload and lack of an attempt override snapshot.
3. Store a bounded, non-secret immutable override on the new stage attempt. Build cloud overrides
   through `ConfigurationService` from the explicitly selected current profile, never defaults.
4. Preserve ordinary `same` semantics for non-unknown failed/canceled stages and all existing
   conflict/dependency guards.
5. Run focused/full verification and two reviews.
6. Commit: `feat: add explicit asr retry strategies`.

### Task 2: Implement durable worker leases and stage transitions

**Files:**

- Create: `src/vtnote/worker_store.py`
- Create: `src/vtnote/worker.py`
- Create: `tests/test_worker_leases.py`
- Create: `tests/test_worker_stage_transitions.py`
- Modify: `src/vtnote/models.py`
- Modify: `src/vtnote/tasks.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class StageClaim:
    stage_run_id: str
    item_id: str
    stage: str
    attempt: int
    worker_id: str
    lease_expires_at: datetime

class WorkerStore:
    def claim_next(self, worker_id: str, now: datetime,
                   lease_duration: timedelta) -> StageClaim | None: ...
    def heartbeat(self, claim: StageClaim, now: datetime) -> None: ...
    def complete(self, claim: StageClaim, result: StageResult) -> None: ...
    def fail(self, claim: StageClaim, failure: StageFailure) -> None: ...
    def recover_expired(self, now: datetime) -> tuple[str, ...]: ...

class StageHandler(Protocol):
    def run(self, context: StageContext) -> StageResult: ...
```

**Steps:**

1. Write named failing tests for atomic single claim, owner/attempt/expiry guarded completion,
   heartbeat, worker heartbeat record, expired recovery, graceful stop, cancellation checkpoint,
   and global GPU resource lease. Include
   `test_claim_requires_all_stage_dependencies_successful_or_skipped`,
   `test_claim_does_not_take_failed_or_canceled_upstream`,
   `test_claim_allows_parallel_translate_and_notes`,
   `test_claim_ignores_canceled_task_and_superseded_attempt`, and
   `test_claim_order_is_created_at_stage_order_item_id_attempt`.
2. Run the two new test files and confirm failure because worker/store modules are absent.
3. Implement transactionally guarded claims using SQLite compare/update semantics and the shared
   pipeline dependency graph. Claim ordering is stable by task creation, stage order, item ID, and
   attempt. Never hold a transaction during external work.
4. Implement a worker loop with injected clock/sleeper/handlers, bounded idle backoff, cooperative
   stop, and startup expired-lease recovery.
5. Verify stale workers cannot overwrite a new attempt and resource leases release on every
   terminal path.
6. Run focused/full verification and two reviews.
7. Commit: `feat: add durable worker leases and recovery`.

### Task 3: Unify source domain and typed failure contracts

**Files:**

- Modify: `src/vtnote/sources.py`
- Modify: `src/vtnote/api.py`
- Modify: `tests/test_source_contracts.py`
- Modify: `tests/test_api.py`

**Interfaces:**

```python
@dataclass(frozen=True)
class SourceProbeResult:
    source_kind: Literal["bilibili", "youtube", "local_media", "uploaded_media",
                         "local_subtitle", "uploaded_subtitle"]
    canonical_url: str | None
    title: str
    duration_ms: int | None
    subtitle_tracks: tuple[SubtitleTrack, ...]

class SourceAdapter(Protocol):
    def probe(self, canonical_source: str) -> SourceProbeResult: ...
    def fetch_subtitle(self, probe: SourceProbeResult, track: SubtitleTrack) -> SubtitleOutcome: ...
    def fetch_audio(self, probe: SourceProbeResult, item_id: str) -> AudioOutcome: ...
```

**Steps:**

1. Add failing tests for one shared API/worker probe type, opaque track IDs, strict source kinds,
   stable ordering, candidate-only fallback, and secret-free serialization. A persisted track ref
   is `trk_<64 lowercase hex>` generated from platform, normalized language, kind, format, and the
   candidate's stable ordinal; it never contains or hashes a URL, query, token, or credential.
2. Remove the duplicate probe DTOs from `api.py` and implement the canonical source types and
   typed errors in `sources.py`.
3. Keep current injected API probe tests passing without adding any network path.
4. Run focused/full verification and two reviews.
5. Commit: `refactor: unify source adapter contracts`.

### Task 4: Build direct-only pinned HTTPS transport

**Files:**

- Create: `src/vtnote/platform_transport.py`
- Create: `tests/test_platform_transport.py`
- Modify: `src/vtnote/url_security.py`
- Modify: `tests/test_url_security.py`

**Interfaces:**

```python
class PinnedHttpsTransport:
    def request(self, request: SourceHttpRequest, policy: UpstreamHostPolicy) -> SourceHttpResponse: ...

@dataclass(frozen=True)
class UpstreamHostPolicy:
    platform: Literal["bilibili", "youtube"]
    stage: Literal["page", "extractor_aux", "resource"]
    exact_hosts: frozenset[str]
    allowed_suffixes: frozenset[str]
```

`SourceHttpResponse` supports `read(n)`, `readline()`, iteration, `close()`, and context manager;
all reads share one total body limit.

**Steps:**

1. Write socket-free failing tests with injected resolver/connector for hostile proxy variables,
   public-IP filtering, DNS rebinding, peer mismatch, per-hop redirect validation, scheme/port
   rejection, header stripping, compressed/decompressed body limits, timeouts, and all response
   read styles.
2. Confirm focused RED.
3. Implement the smallest explicit HTTPS transport with `trust_env=False`, manual redirects,
   pinned address/peer checks, bounded headers/body, and no Cookie/netrc behavior.
4. Page and extractor-aux policies use reviewed platform hosts/suffixes. A media/subtitle resource
   policy contains only exact public HTTPS hosts emitted by the controlled extractor for that
   probe; it carries no cookies/auth headers, revalidates every redirect/peer, and expires with the
   stage. Reject resource URLs obtained from user options or persisted opaque IDs.
5. Ensure error objects contain only safe host/category data and never URL query or response body.
6. Run focused/full verification and two reviews.
7. Commit: `feat: add pinned direct-only platform transport`.

### Task 5: Add controlled yt-dlp bridge and YouTube runtime readiness

**Files:**

- Create: `src/vtnote/ytdlp_bridge.py`
- Create: `src/vtnote/youtube_runtime.py`
- Create: `tests/test_ytdlp_bridge.py`
- Create: `tests/test_youtube_runtime.py`
- Modify: `pyproject.toml`
- Modify: `environment.yml`

**Interfaces:**

```python
def build_controlled_ytdlp(
    transport: PinnedHttpsTransport,
    runtime: YoutubeRuntime,
    output_root: Path,
) -> yt_dlp.YoutubeDL: ...

def inspect_youtube_runtime(settings: Settings) -> YoutubeRuntimeStatus: ...

class VtNoteRequestHandlerRH(yt_dlp.networking.common.RequestHandler):
    def _send(self, request: yt_dlp.networking.common.Request) -> yt_dlp.networking.Response: ...

class VtNoteYoutubeDL(yt_dlp.YoutubeDL):
    def build_request_director(self, handlers, preferences=None) -> RequestDirector: ...
```

**Steps:**

1. Add failing tests for exact package/runtime version normalization, D-drive paths, hashes,
   `DENO_DIR`, missing EJS/Deno partial availability, no system Node, no remote components,
   no plugins/config files, controlled output template, and rejection of arbitrary yt-dlp options.
   Add `test_only_vtnote_request_handler_is_registered`,
   `test_handler_maps_all_response_read_and_close_shapes`,
   `test_page_aux_and_ephemeral_resource_host_policies_are_distinct`,
   `test_extracted_resource_host_is_exact_public_https_only`, and
   `test_default_urllib_requests_websockets_handlers_are_absent`.
2. Add `yt-dlp-ejs==0.8.0` to locked Python dependencies without downloading Deno at runtime.
3. Implement runtime inspection, the yt-dlp Request/Response mapping layer, and a controlled
   `YoutubeDL` subclass whose request director registers only `VtNoteRequestHandlerRH`. Ignore
   user/system yt-dlp configuration and reject any extractor path that tries a default handler.
4. Assert Bilibili readiness remains available when the YouTube runtime is incomplete.
5. Run focused/full verification and two reviews.
6. Commit: `feat: control yt-dlp and youtube runtime assets`.

### Task 6: Implement Bilibili and YouTube source adapters

**Files:**

- Create: `src/vtnote/platform_sources.py`
- Create: `tests/test_platform_sources.py`
- Modify: `src/vtnote/sources.py`
- Modify: `src/vtnote/api.py`

**Interfaces:**

```python
class YtDlpSourceAdapter(SourceAdapter):
    platform: Literal["bilibili", "youtube"]
```

**Steps:**

1. Add fixture-driven failing tests for canonical URL forms, extractor identity, title/duration,
   `manual|automatic|unconfirmed` tracks, conservative unconfirmed ordering/UI label, language
   normalization, opaque IDs, per-track fetch validation, exactly one audio fetch after all
   candidate failures, local filename containment, typed `removed|temporary|auth_required|
   region_restricted|unsupported|adapter_drift|invalid_content` classification, and no media fetch
   during probe.
2. Confirm no test uses a real socket.
3. Implement separate platform instances over the controlled bridge and pinned transport. Do not
   add Bilibili player API fallback in V1.
4. Wire the real probe registry into FastAPI while preserving injectable test adapters.
5. Run focused/full verification and two reviews.
6. Commit: `feat: add public bilibili and youtube adapters`.

### Task 7: Orchestrate the source stage and immutable subtitle publication

**Files:**

- Create: `src/vtnote/source_stage.py`
- Create: `tests/test_source_stage.py`
- Modify: `src/vtnote/worker.py`
- Modify: `src/vtnote/runtime_assets.py`
- Modify: `src/vtnote/artifacts.py`

**Steps:**

1. Add failing tests for local/uploaded subtitle, local/uploaded media, platform subtitle success
   with zero audio calls, candidate fallback, fatal safety error, exactly one audio handoff, source
   evidence, cancellation, crash before/after atomic publish, and cleanup/trash ownership.
2. Implement a source stage handler using existing subtitle parsers, media validation, runtime
   assets, and atomic artifact writer.
3. Publish source subtitle original only when one exists; publish canonical transcript directly for
   valid subtitle inputs; otherwise return one app-owned audio handoff for transcribe.
4. Run focused/full verification and two reviews.
5. Commit: `feat: orchestrate subtitle-first source stage`.

### Task 8: Add Tencent credentials, submission persistence, and pure protocol contract

**Files:**

- Create: `src/vtnote/provider_credentials.py`
- Create: `src/vtnote/tencent_contract.py`
- Create: `src/vtnote/cloud_submissions.py`
- Create: `tests/test_provider_credentials.py`
- Create: `tests/test_tencent_contract.py`
- Create: `tests/test_cloud_submissions.py`
- Modify: `src/vtnote/models.py`
- Modify: `src/vtnote/database.py`
- Modify: `src/vtnote/configuration.py`
- Modify: `src/vtnote/api.py`
- Modify: `src/vtnote/secrets.py`
- Modify: `tests/test_configuration.py`
- Modify: `tests/test_database.py`
- Modify: `tests/test_tasks.py`
- Modify: `tests/test_api.py`

**Interfaces:**

```python
class TencentCredentialBundle(BaseModel):
    schema_version: Literal[1]
    secret_id: SecretStr
    secret_key: SecretStr

class ProfileTestInput(InputModel):
    test_kind: Literal["provider_profile", "cos_sentinel"] = "provider_profile"
    acknowledge_billable_request: bool = False
    speech_sample_upload_id: str | None = None

# POST /api/profiles/{profile_id}/test accepts ProfileTestInput

class TencentPreflight:
    def evaluate(self, audio: PreparedAudio, profile: CloudProfileSnapshot,
                 limits: TencentLimits) -> CloudEligibility: ...

class CloudSubmissionStore:
    def prepare(self, stage_run_id: str, audio_sha256: str,
                cos_locator: CosLocator | None) -> CloudSubmission: ...
    def mark_sending(self, submission_id: str) -> CloudSubmission: ...
    def mark_submitted(self, submission_id: str, task_id: str,
                       request_id: str, submitted_at: datetime) -> CloudSubmission: ...
    def mark_unknown(self, submission_id: str, safe_code: str) -> CloudSubmission: ...
    def schedule_query(self, submission_id: str, next_poll_at: datetime) -> CloudSubmission: ...
```

**Steps:**

1. Add failing tests for atomic Tencent SecretId/SecretKey bundle create/rotate/delete, explicit
   rejection of temporary STS/session-token fields,
   read responses containing only `has_secret`/configured-field booleans, malformed legacy entry
   requiring re-entry, revision invalidation, exact Base64 formula, 4,500,000-byte inline boundary,
   five-hour/96-MiB/`zh_en_dialects` checks, COS-required routing, and no secret/pre-signed URL in
   DB/task snapshot/log/API. Add
   `test_tencent_connection_forces_canonical_api_and_guangzhou_region`,
   `test_tencent_profile_forces_large_model_2_and_subtitle_format`, and
   `test_cos_config_rejects_public_non_guangzhou_or_malformed_bucket_and_prefix`.
2. Implement versioned provider-specific secret schemas inside one credential entry. Preserve the
   single-key Bailian UX with a one-field bundle.
3. For `tencent_recording_asr`, accept only base URL `https://asr.tencentcloudapi.com`, fixed
   ASR/COS region `ap-guangzhou`, model/version/format/language scope, and allowlisted private COS
   parameters; reject rather than ignore caller variation.
4. Add a `cloud_submissions` table keyed by local UUID/stage attempt. Store uint64 Tencent TaskId as
   text, provider/date/request ID, audio hash, non-secret COS locator, state
   `prepared|sending|submitted|submission_unknown|terminal`, `next_poll_at`, `poll_attempt`,
   `last_query_at`, `signed_url_expires_at`, `cleanup_due_at`, `remote_terminal_at`, timestamps,
   and result expiry. Add due-query/cleanup indexes and database-upgrade tests. Never store the
   pre-signed URL or raw provider payload.
5. Implement the fixed official error-code table from ADR-006, including HTTP-200 `Response.Error`,
   pure request builders, TC3 canonical-signature vectors, preflight, and task-state/result parsers.
   No network call belongs in this task.
6. Migrate legacy `volc_bigasr_flash` configurations: archive active/archived provider rows, clear
   defaults and test/upload consent, retain history read-only, and mark queued/running snapshots
   `legacy_provider_requires_reconfiguration` before any secret read/network call. Prove zero old
   endpoint calls and allow explicit local retry.
7. Add the common `ProfileTestInput` request DTO and stable
   `billable_test_ack_required|speech_test_sample_required` errors to the existing tester/API
   protocol so Tasks 9 and 12 use the same production signature.
8. Run focused/full verification and two reviews.
9. Commit: `feat: add tencent cloud transcription contracts`.

### Task 9: Implement Tencent ASR and private COS adapters

**Files:**

- Create: `src/vtnote/tencent_asr.py`
- Create: `src/vtnote/tencent_cos.py`
- Create: `tests/test_tencent_asr_adapter.py`
- Create: `tests/test_tencent_cos.py`
- Modify: `src/vtnote/diagnostics.py`
- Modify: `src/vtnote/api.py`
- Modify: `tests/test_api.py`
- Modify: `pyproject.toml`
- Modify: `environment.yml`

**Interfaces:**

```python
class CloudAsrOutcomeKind(str, Enum):
    SUCCESS = "success"
    STOP = "stop"
    FALLBACK_ALLOWED = "fallback_allowed"
    UNKNOWN = "submission_unknown"

class TencentRecordingClient:
    def create(self, audio: PreparedAudio, context: TencentRequestContext,
               submission: CloudSubmission) -> TencentTaskRef: ...
    def query(self, task: TencentTaskRef) -> CloudAsrOutcome: ...

class TencentCosStager:
    def put(self, audio: PreparedAudio, context: CosContext) -> CosLocator: ...
    def presign_get(self, locator: CosLocator, ttl: timedelta) -> SensitiveUrl: ...
    def delete(self, locator: CosLocator) -> None: ...

class TencentConnectivityTester(ConnectionTester, ProfileTester):
    def test_connection(self, connection, secret, *, follow_redirects: Literal[False]) -> ConnectivityResult: ...
    def test_profile(self, profile, secret, request: ProfileTestInput,
                     *, follow_redirects: Literal[False]) -> ConnectivityResult: ...

class TencentSubmissionReconciler:
    def reconcile_one_due(self, now: datetime) -> ReconcileResult | None: ...
```

**Steps:**

1. Add mocked RED tests for exact TC3 canonical request/headers/body, `CreateRecTask` inline and URL
   shapes, one create call, every fixed official error code including HTTP-200 `Response.Error`,
   TaskId string safety, persistent poll schedule/jitter, one query per lease, fairness/restart,
   pre-send failure, post-send unknown, strict ResultDetail timestamp mapping, result expiry, and
   full redaction scan.
2. Implement ASR HTTP with `httpx` `trust_env=False`, fixed host, no redirects, and no automatic
   create retry. Query may use the bounded retry/poll policy from THR-002.
3. Pin `cos-python-sdk-v5==1.9.44`. Construct its `requests.Session` explicitly with
   `trust_env=False`, disable redirects, SDK retries, alternate-domain switching, and debug logging;
   validate the derived official bucket host before any call.
4. Add COS mocked tests for deterministic UUID object keys, streamed upload, six-hour single-object
   pre-sign, no URL persistence/logging, provider-terminal immediate delete, deferred delete for
   `submission_unknown`/cancel until URL expiry plus 30 minutes, cancel-before-submit immediate
   delete, one-day-lifecycle readiness, cleanup failure audit/recovery, and no delete before a local
   recoverable encoded-audio copy exists.
5. Convert validated `ResultDetail` to ordered provider-neutral segments. A success without usable
   timestamps returns `provider_result_missing_timestamps`; never parse Tencent's presentation text
   with ad-hoc colon splitting.
6. Implement the acknowledged profile test with a user-selected, validated 2–10 second speech sample
   staged through the normal upload boundary. It makes exactly one inline create call, polls that
   task, requires a successful non-empty `ResultDetail`, warns that the sample is uploaded/may be
   billable, and never uses COS. Static connection validation alone cannot set
   `profile_capability_tested`. Implement a separate acknowledged COS readiness test that creates,
   pre-signs, verifies, and deletes a tiny sentinel.
7. Wire `TencentConnectivityTester` and the persistent due-query/cleanup reconciler into production
   composition. Each reconcile claim performs at most one query or delete then releases its lease;
   cancellation does not stop reconciliation. Only a successful current profile can be
   upload-authorized. COS is required only for audio above the inline threshold.
8. Prove raw response, provider message, Base64, pre-signed URL, and credentials are absent from
   persistence, diagnostics, and exception text.
9. Run focused/full verification and two reviews.
10. Commit: `feat: implement tencent durable cloud transcription`.

### Task 10A: Add an explicit durable local-model installer

**Files:**

- Create: `assets/models/large-v3-turbo.manifest.json`
- Create: `src/vtnote/model_assets.py`
- Create: `tests/test_model_assets.py`
- Modify: `src/vtnote/models.py`
- Modify: `src/vtnote/database.py`
- Modify: `src/vtnote/worker.py`
- Modify: `src/vtnote/api.py`
- Modify: `tests/test_api.py`

**Manifest contract:**

```json
{
  "schema_version": 1,
  "model_name": "large-v3-turbo",
  "repo_id": "dropbox-dash/faster-whisper-large-v3-turbo",
  "revision": "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
  "files": [
    {"path": "config.json", "size": 2263, "sha256": "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e"},
    {"path": "model.bin", "size": 1617884929, "sha256": "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"},
    {"path": "preprocessor_config.json", "size": 340, "sha256": "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"},
    {"path": "tokenizer.json", "size": 2710337, "sha256": "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd"},
    {"path": "vocabulary.json", "size": 1068114, "sha256": "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"}
  ]
}
```

**Public APIs:**

- `GET /api/assets/local-whisper`
- `POST /api/assets/local-whisper/install` with
  `{"acknowledge_download":true,"expected_revision":"0a363e..."}`
- `POST /api/assets/local-whisper/cancel`

**Steps:**

1. Add named RED tests
   `test_model_manifest_is_exact_and_self_consistent`,
   `test_install_requires_explicit_ack_and_free_space`,
   `test_download_is_direct_only_and_d_drive_only`,
   `test_download_resumes_only_matching_etag_revision_and_size`,
   `test_each_file_hash_is_checked_before_atomic_publish`,
   `test_hash_failure_and_cancel_move_staging_to_recoverable_trash`,
   `test_install_progress_and_lease_survive_worker_restart`, and
   `test_runtime_never_downloads_an_uninstalled_model`.
2. Run `python -m pytest -q tests/test_model_assets.py tests/test_api.py`; expected RED is the
   missing manifest/install record/service/routes.
3. Add a durable managed-asset install record with lease, byte/file progress, ETag, cancellation,
   error code, and timestamps. Reuse the worker supervisor but keep model installs outside media
   task stage aggregation.
4. Stream only manifest-listed files from the fixed revision through a direct-only HTTPS downloader
   with public-peer/redirect checks, D-drive partial files, bounded buffers, safe Range resume, and
   per-file SHA-256. Never use Hugging Face's default cache or C drive.
5. Publish the complete verified directory atomically, record manifest/revision/file hashes for
   provenance/readiness, and configure runtime loading by absolute path with
   `local_files_only=True`.
6. Run focused/full verification and two reviews.
7. Commit: `feat: add managed local whisper model installation`.

### Task 10: Implement GPU-only faster-whisper adapter

**Files:**

- Create: `src/vtnote/local_asr.py`
- Create: `tests/test_local_whisper_adapter.py`
- Modify: `src/vtnote/models.py`
- Modify: `src/vtnote/database.py`
- Modify: `src/vtnote/configuration.py`
- Modify: `tests/test_configuration.py`
- Modify: `tests/test_database.py`

**Interfaces:**

```python
class FasterWhisperTranscriber:
    def transcribe(self, audio: PreparedAudio, context: TranscriptionContext) -> AsrResult: ...
```

**Steps:**

1. Add failing tests for lazy import/model creation, exact model/device/compute/VAD values, D-drive
   model/cache roots, explicit CUDA unavailable error, no CPU fallback, full iteration of the lazy
   segment generator, cancellation between segments, normalized timestamps/text, and provenance.
   Add `test_existing_default_device_auto_migrates_to_cuda`,
   `test_legacy_task_snapshot_auto_is_rejected_with_retry_action`, and
   `test_provenance_has_model_revision_file_hashes_and_cuda_library_versions`.
2. Change both ORM and service defaults from `device="auto"` to `device="cuda"`; migrate current
   default rows. Preserve immutable historical task snapshots but reject `auto` at execution with
   `legacy_local_asr_snapshot_requires_retry`.
3. Implement the adapter with injected model factory for tests. It accepts only Task 10A's verified
   local model directory/revision and passes `local_files_only=True`; it never starts a download.
4. Rely on the Task 2 GPU lease for global concurrency one.
5. Run focused/full verification and two reviews.
6. Commit: `feat: add gpu-only faster-whisper transcription`.

### Task 11: Route cloud/local ASR and publish once

**Files:**

- Create: `src/vtnote/transcribe_stage.py`
- Create: `tests/test_worker_asr_e2e.py`
- Modify: `src/vtnote/worker.py`
- Modify: `src/vtnote/tasks.py`
- Modify: `src/vtnote/artifacts.py`

**Steps:**

1. Add failing end-to-end tests for platform subtitle bypass, local mode, cloud mode without valid
   consent, auto without cloud profile, cloud success, explicit stop, server fallback, unknown
   outcome fallback with warning, cloud-mode unknown stop, inline/COS routing, crash after COS put,
   crash before/after TaskId persistence, query-only recovery, provider result expiry, local cancel
   before submit, local cancel after TaskId while Tencent continues remotely, `submission_unknown`
   deferred COS cleanup, provider-terminal deletion/failure recovery, late cloud response, fair
   progress of unrelated stages while a cloud task waits, and immutable publication race.
2. Implement routing exclusively from the task snapshot; never read current defaults after enqueue.
3. Permit local fallback only for the typed outcome matrix. Store safe evidence and possible-billing
   warning. Do not expose ordinary cloud retry for unknown outcome.
4. Initial cloud handling submits at most once and yields to the persistent submission reconciler;
   it never loops/sleeps inside the main worker. Reconciler success queues atomic publication, while
   local cancellation affects only the user stage and not remote safety cleanup.
5. Atomically publish one transcript, retain a local recoverable copy/audit trail, and move app-owned
   audio into recoverable trash. Delete COS only under the provider-terminal/expiry rules in ADR-006.
6. Run focused/full verification and two reviews.
7. Commit: `feat: orchestrate cloud and local transcription`.

### Task 12: Implement the Aliyun Bailian domestic chat adapter and capability test

**Files:**

- Create: `src/vtnote/chat.py`
- Create: `tests/test_bailian_chat_adapter.py`
- Modify: `src/vtnote/models.py`
- Modify: `src/vtnote/database.py`
- Modify: `src/vtnote/configuration.py`
- Modify: `src/vtnote/api.py`
- Modify: `src/vtnote/tasks.py`
- Modify: `tests/test_configuration.py`
- Modify: `tests/test_database.py`
- Modify: `tests/test_tasks.py`
- Modify: `tests/test_api.py`

**Interfaces:**

```python
class ChatClient(Protocol):
    def complete(self, request: ChatRequest) -> ChatResponse: ...

class DomesticChatAdapter(Protocol):
    def capabilities(self) -> ChatCapabilities: ...
    def complete(self, request: ChatRequest) -> ChatResponse: ...

class AliyunBailianChatAdapter(DomesticChatAdapter): ...
```

**Steps:**

1. Add mocked RED tests that accept only a DNS-label `workspace_id`, construct exactly
   `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`, reject arbitrary,
   foreign, relay, userinfo/path/query/port, and redirected endpoints, append exactly
   `/chat/completions`, use bearer auth, `stream=false`, and never inherit environment proxy.
2. Add tests for timeout/no automatic ambiguous POST retry, bounded response, official
   `response_format={"type":"json_object"}`, required `JSON` prompt wording, empty content,
   missing choices, unknown fields, `finish_reason=length`, content filtering/refusal,
   400/401/403/404 stop, only 429 `Retry-After` one bounded retry, 500/503/read-timeout/reset
   `chat_submission_unknown` with no auto retry, requested/actual model and usage/request-ID
   normalization, and secret/custom-prompt redaction.
3. Implement only `aliyun_bailian` in the compile-time registry. Do not expose arbitrary Base URL,
   foreign API, nonofficial relay, or `/models`; model matches the fixed 1–128 character policy,
   is user-entered from the same Beijing workspace, and is verified by the profile test.
4. Implement the common `ProfileTestInput` tester contract. Static
   `connection_policy_validated` makes no paid request and never enables a profile. A confirmed
   profile test sends one minimal non-streaming JSON request, may incur a small charge, and records
   a safe fingerprint of endpoint, credential/profile revisions, model, response format, thinking
   mode, and production options as `profile_capability_tested`.
5. Add independent revision-bound `POST /api/profiles/{id}/authorize-chat-data` and
   `POST /api/profiles/{id}/revoke-chat-data` APIs. Consent lists subtitle cues, title/metadata,
   target/output language and custom prompt, explicitly states no audio, and is invalidated by any
   fingerprint field change. Capability testing never grants data consent.
6. Migrate every legacy arbitrary `openai_compatible` connection/profile/default to archived,
   untested, unconsented read-only history. At execution, old queued/running snapshots become
   `legacy_chat_endpoint_blocked` before credential lookup or transport; prove secret reads and
   network calls are both zero.
7. Reject profiles whose context/max-token settings cannot satisfy ADR-014.
8. Run focused/full verification and two reviews.
9. Commit: `feat: add aliyun bailian chat adapter`.

### Task 13: Implement cue-aligned translation

**Files:**

- Create: `src/vtnote/translation.py`
- Create: `tests/test_translation_processor.py`
- Modify: `src/vtnote/artifacts.py`

**Interfaces:**

```python
class Translator:
    def translate(self, transcript: Transcript, target_language: str,
                  profile: ChatProfileSnapshot, client: ChatClient,
                  limits: AiLimits) -> Translation: ...
```

**Steps:**

1. Add RED tests for target-language propagation, batching by both 30 cues and the complete 64-KiB
   UTF-8 request budget, single-cue oversize failure without truncation, source order, exact ID-set
   validation, reordered/missing/duplicate/empty/unknown IDs, invalid/unknown-field JSON, empty
   content/missing choices/length finish/filtering, 256-KiB response limit, `stream=false` and
   `json_object` on every call, one retry round split into at most two 15-cue subbatches, no second
   round, cancellation, source hash, and no partial artifact.
2. Implement deterministic prompts with JSON data separated from instructions; user transcript is
   never concatenated into system control text.
3. Validate all batches before atomically writing one translation artifact.
4. Run focused/full verification and two reviews.
5. Commit: `feat: add cue-aligned transcript translation`.

### Task 14: Implement cited AI note generation

**Files:**

- Create: `src/vtnote/notes.py`
- Create: `tests/test_notes_processor.py`
- Modify: `src/vtnote/artifacts.py`

**Interfaces:**

```python
class NoteGenerator:
    def generate(self, transcript: Transcript, profile: ChatProfileSnapshot,
                 client: ChatClient,
                 template: Literal["summary", "key_points", "custom"],
                 output_language: str, custom_prompt: str | None,
                 limits: AiLimits) -> NoteDocument: ...

class NoteDocument:
    generated_by_ai: Literal[True]
    task_id: str
    transcript_sha256: str
    requested_model: str
    response_model: str
    def validate_against(self, transcript: Transcript) -> None: ...
```

**Steps:**

1. Add RED tests for all three templates, explicit output-language propagation, DPAPI-unprotected
   custom prompt isolation inside the call only, required/forbidden custom-prompt combinations,
   ordered 48-KiB chunks within the complete 64-KiB request budget, 24-chunk and four-level limits,
   single-cue oversize failure without truncation, deterministic reduce order, `stream=false` and
   `json_object` at every map/reduce level, empty/missing/length/filter/unknown-field rejection,
   citation schema and lineage, missing/mismatched/unrelated cue rejection, 256-KiB response bound,
   cancellation, AI/task/hash/requested+actual-model labels, review warning, and no partial Markdown.
2. Implement map/reduce prompts that carry stable cue IDs and explicit citation lineage through
   every level. Render final Markdown locally with human-readable times, machine-stable cue
   references, `generated_by_ai`, task/transcript/model provenance, and a reminder to verify names,
   numbers, terminology, and citations.
3. Validate every citation against cue ID/start/end and its current chunk/lineage before atomic
   publication.
4. Run focused/full verification and two reviews.
5. Commit: `feat: generate cited ai notes`.

### Task 15: Orchestrate independent AI branches and stage-only retry

**Files:**

- Create: `src/vtnote/ai_stages.py`
- Create: `tests/test_worker_ai_e2e.py`
- Modify: `src/vtnote/worker.py`
- Modify: `src/vtnote/tasks.py`
- Modify: `src/vtnote/pipeline.py`

**Steps:**

1. Add RED tests for translation/notes independence, either/both disabled, missing/stale
   `profile_capability_tested` or `chat_data_consent_revision` causing zero credential/network
   access, either failure with original available, `chat_submission_unknown` no auto retry and
   charge-acknowledged explicit retry, `completed_with_warnings`, stage-only retry, a new attempt,
   crash recovery, no source/transcribe rerun, and no duplicate note artifact.
2. Implement translation and notes handlers registered independently after transcribe.
   They resolve the explicit target/output language and profile from the task snapshot; the notes
   handler decrypts a protected custom prompt only for the duration of that attempt.
3. Aggregate task status only from durable stage state; preserve successful branch artifacts and
   evidence across retry.
4. Run focused/full verification and two reviews.
5. Commit: `feat: orchestrate optional ai processing stages`.

### Task 16: Complete backend read models, readiness, results, and storage APIs

**Files:**

- Create: `src/vtnote/readiness.py`
- Create: `tests/test_readiness.py`
- Modify: `src/vtnote/api.py`
- Modify: `src/vtnote/tasks.py`
- Modify: `src/vtnote/config.py`
- Modify: `src/vtnote/exports.py`
- Modify: `src/vtnote/runtime_assets.py`
- Modify: `tests/test_api.py`
- Modify: `tests/test_exports.py`
- Modify: `tests/test_runtime_assets.py`

**Public APIs:**

- `GET /api/health`
- `GET /api/readiness`
- `GET /api/storage`
- `GET /api/storage/trash`
- `POST /api/storage/trash/{asset_id}/restore`
- `GET /api/tasks?limit=&cursor=&status=`
- `GET /api/tasks/{task_id}`
- `GET /api/items/{item_id}/transcript`
- `GET /api/items/{item_id}/translations/{language}`
- `GET /api/items/{item_id}/notes`
- `GET /api/items/{item_id}/execution-summary?format=json|markdown`
- Existing export API with safe `Content-Disposition`

**Steps:**

1. Add failing API tests for secret-free health/readiness, partial capability flags, server limits,
   cursor pagination, created/duration/stage timing/progress/result availability/evidence, note
   listing/read, safe download filename, unknown cloud action restrictions, and no absolute paths.
   Add tests for trash list/restore state/ownership/root revalidation/audit and deterministic
   execution-summary JSON/Markdown containing safe source/route/model/fallback/authorization/
   warning evidence but no generated-now timestamp or sensitive fields.
2. Implement typed read models and readiness inspectors for data/cache/SQLite/FFmpeg/GPU/model
   install state/yt-dlp/EJS/Deno without triggering download or external requests.
3. Implement CSRF-protected restore for app-owned recoverable assets only. Restore to the recorded
   typed app path after reparse/root/state checks and write one audit event; never expose a generic
   delete or arbitrary destination.
4. Add `exports.render_execution_summary` as a deterministic pure function over sanitized task
   snapshot/stage evidence. Keep it separate from immutable `transcript.json` and generated
   transcript-format exports.
5. Keep `/api/*` errors JSON and existing CSRF/Host/Origin behavior unchanged.
6. Add THR-004 benchmark test with 100 tasks and a recorded p95 assertion isolated from CI-host
   scheduling noise by measuring service calls in one process.
7. Run focused/full verification and two reviews.
8. Commit: `feat: expose readiness and complete result read models`.

### Task 17: Create React/Vite foundation and safe same-origin API client

**Files:**

- Create: `frontend/package.json`
- Create: `frontend/package-lock.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/eslint.config.js`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/app/App.tsx`
- Create: `frontend/src/app/router.tsx`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/types.ts`
- Create: `frontend/src/styles/tokens.css`
- Create: `frontend/src/test/setup.ts`
- Create: `frontend/src/api/client.test.ts`

**Steps:**

1. Add failing Vitest tests for CSRF acquisition, `credentials:"same-origin"`, mutation header,
   403 without replay, abort, error mapping, download stream, one in-flight poll, exact ADR-014
   foreground/background/error schedule, terminal stop, and no storage persistence.
2. Install exact frontend dependencies with npm cache on D and commit `package-lock.json`.
3. Implement the minimal typed client, router shell, local design tokens, system font stack, skip
   link, focus outline, reduced-motion base, and no third-party resources.
4. Run all frontend verification and a production build.
5. Run two reviews.
6. Commit: `feat: establish vtnote react frontend`.

### Task 18: Implement setup and settings UI

**Files:**

- Create: `frontend/src/pages/SetupPage.tsx`
- Create: `frontend/src/pages/SettingsPage.tsx`
- Create: `frontend/src/pages/ConnectionsPage.tsx`
- Create: `frontend/src/pages/StoragePage.tsx`
- Create: `frontend/src/components/SecretField.tsx`
- Create: `frontend/src/components/InlineNotice.tsx`
- Create: `frontend/src/components/ConfirmDialog.tsx`
- Create: `frontend/src/pages/SetupPage.test.tsx`
- Create: `frontend/src/pages/SettingsPage.test.tsx`
- Create: `frontend/src/pages/ConnectionsPage.test.tsx`
- Create: `frontend/src/pages/StoragePage.test.tsx`
- Create: `frontend/src/components/SecretField.test.tsx`

**Steps:**

1. Add failing page tests for partial readiness, YouTube-only disablement, Tencent
   SecretId/SecretKey fields and explicit STS rejection, private `ap-guangzhou` COS bucket/prefix
   and lifecycle status, Aliyun Bailian Beijing workspace ID/canonical endpoint/model, rejection of
   arbitrary/foreign/relay endpoints, no secret value/mask in DOM, distinct policy validation,
   billable capability test, audio-upload consent and text-data consent revisions, common test
   confirmation errors, user-selected short-speech Tencent profile test and COS sentinel test,
   local model source/size/install/progress/cancel/resume/hash state, GPU guidance, protected
   custom-prompt replacement/
   `has_custom_prompt`, defaults, storage/trash/audit visibility, restore action, and no permanent
   delete action.
2. Implement pages from `docs/website-specification.md` using the shared API client and accessible
   native controls.
3. Scan rendered DOM, response mocks, URL, errors, and web storage for credentials. A custom prompt
   may exist only in its active edit control and outgoing create/replace request; assert it is not
   echoed by GET responses or retained in URL, history state, logs, errors, localStorage, or
   sessionStorage.
4. Run frontend/full backend verification and two reviews.
5. Commit: `feat: add setup and settings experience`.

### Task 19: Implement task creation, probe, and upload UI

**Files:**

- Create: `frontend/src/pages/CreateTaskPage.tsx`
- Create: `frontend/src/components/FilePicker.tsx`
- Create: `frontend/src/components/ProfileSelect.tsx`
- Create: `frontend/src/pages/CreateTaskPage.test.tsx`
- Create: `frontend/src/components/FilePicker.test.tsx`
- Create: `frontend/src/components/ProfileSelect.test.tsx`
- Modify: `src/vtnote/api.py`
- Modify: `tests/test_api.py`

**Steps:**

1. Add failing tests for URL probe, local media/subtitle upload, server-provided size limits,
   progress/cancel, ASR auto/cloud/local choices, explicit `zh_en_dialects` scope and known
   unsupported-language local routing, privacy/cost summary, translation off default, notes default
   only after current capability test plus text-data consent, review text listing exactly which
   subtitle/title/language/custom-prompt data goes to Bailian and that audio does not, double-submit
   prevention, success `replace` navigation, refresh GET-only, content-rights/platform-terms
   acknowledgement, and errors without local absolute path.
2. Add only the API metadata needed for resumable UI state; do not add resumable uploads or batch
   creation.
3. Implement the create flow with clear source/ASR/optional-processing review before POST.
4. Run frontend/backend verification and two reviews.
5. Commit: `feat: add safe task creation and uploads`.

### Task 20: Implement task history and detail/result UI

**Files:**

- Create: `frontend/src/pages/TaskHistoryPage.tsx`
- Create: `frontend/src/pages/TaskDetailPage.tsx`
- Create: `frontend/src/components/StatusBadge.tsx`
- Create: `frontend/src/components/StageTimeline.tsx`
- Create: `frontend/src/components/TranscriptViewer.tsx`
- Create: `frontend/src/components/ExportMenu.tsx`
- Create: `frontend/src/components/EmptyState.tsx`
- Create: `frontend/src/pages/TaskHistoryPage.test.tsx`
- Create: `frontend/src/pages/TaskDetailPage.test.tsx`
- Create: `frontend/src/components/StageTimeline.test.tsx`
- Create: `frontend/src/components/TranscriptViewer.test.tsx`
- Create: `frontend/src/components/ExportMenu.test.tsx`

**Steps:**

1. Add failing tests for pagination/filter, stage attempts and timing, indeterminate progress without
   denominator, cancel/retry actions, the exact `local`/`cloud_confirmed` unknown-outcome forms and
   charge acknowledgement, `chat_submission_unknown` charge-acknowledged AI retry, remote Tencent
   reconciliation continuing after local cancel, transcript/translation/note availability, AI
   generation/review labels, warning isolation, cue citation navigation, execution-summary export,
   safe filenames, terminal polling stop, and empty/error states.
2. Implement history and detail routes without a global state library. Use URL/search state and
   request-local state only.
3. Ensure failed translation/notes never obscure the original transcript.
4. For long transcripts, use a bounded/virtualized reading view with stable cue anchors and a
   user-selectable non-virtualized accessible full-text mode. Test focus/scroll position across mode
   changes and citation jumps.
5. Run keyboard/focus/aria-live tests, axe scans, WCAG 2.2 AA contrast evidence, color-not-sole-
   status checks, reduced motion, 375/768/1024/1440 viewports, and 200% zoom browser checks.
6. Run frontend/backend verification and two reviews.
7. Commit: `feat: add task history and result views`.

### Task 21: Serve the SPA and supervise API/worker

**Files:**

- Create: `src/vtnote/launcher.py`
- Create: `src/vtnote/__main__.py`
- Create: `tests/test_launcher.py`
- Create: `tests/test_static_app.py`
- Modify: `src/vtnote/api.py`
- Modify: `pyproject.toml`

**Steps:**

1. Add failing tests for loopback-only bind, port conflict, API/worker child lifecycle, bounded
   restart, nonzero diagnostics, Ctrl-C/Windows termination, no orphan worker, production docs
   disabled, static index, `/tasks/<uuid>` SPA fallback, `/api/nope` JSON 404, and GET/HEAD-only
   fallback.
2. Implement a small supervisor using explicit argv with no shell and cooperative child shutdown.
3. Mount built frontend assets only when present; readiness reports a missing build rather than
   crashing developer API mode.
4. Run focused/full verification and two reviews.
5. Commit: `feat: supervise worker and serve production ui`.

### Task 22: Integrate structured logging, cleanup scheduler, and diagnostic bundle

**Files:**

- Create: `src/vtnote/logging_setup.py`
- Create: `src/vtnote/maintenance.py`
- Create: `tests/test_logging_security.py`
- Create: `tests/test_maintenance.py`
- Modify: `src/vtnote/launcher.py`
- Modify: `src/vtnote/runtime_assets.py`
- Modify: `src/vtnote/diagnostics.py`

**Steps:**

1. Add failing tests for JSON logs, rotation, bounded fields, redaction of all secret/path/prompt/raw
   response forms, 24-hour trash purge, audit rows for move/purge/failure, startup restore,
   maintenance lease, due Tencent query/cleanup recovery after process restart, no COS deletion
   before provider terminal or URL expiry plus grace, one-day lifecycle reported only as a fallback
   rather than an exact timer, launcher shutdown, and a secret-free diagnostic zip.
2. Implement structured logging and maintenance as a supervised process/service using existing
   runtime-asset operations and the Tencent submission reconciler. A maintenance claim performs one
   bounded external action and releases its lease.
3. Before any material purge in test/development tooling, copy the target into the task-specific
   Codex backup directory and record the action.
4. Run repository-wide secret/path fixture scans and focused/full verification.
5. Run two reviews.
6. Commit: `feat: add secure logging and runtime maintenance`.

### Task 23: Freeze reproducible dependencies and release evidence tooling

**Files:**

- Create: `requirements.lock`
- Create: `docs/installation.md`
- Create: `docs/release-checklist.md`
- Create: `docs/third-party-notices.md`
- Create: `tools/collect_release_evidence.mjs`
- Create: `tests/test_release_evidence.py`
- Modify: `environment.yml`
- Modify: `pyproject.toml`
- Modify: `docs/implementation-log.md`

**Steps:**

1. Add failing tests for a complete dependency lock, no unbounded production dependency, Deno/EJS/
   yt-dlp/FFmpeg/model/CUDA hash records, FFmpeg `-version/-buildconf`, license/NOTICE/source
   references, secret-free output, and refusal to label the current GPL development FFmpeg as an
   LGPL release artifact.
2. Generate deterministic lock/evidence formats without downloading or redistributing unapproved
   binaries.
3. Document Conda setup, D-drive assets, first-run readiness, credential entry, direct-only platform
   limitation, backup/restore, upgrade validation, and removal.
4. Make LGPL FFmpeg the preferred release candidate and require an explicit recorded decision when
   only a GPL build passes functional validation.
5. Run focused/full verification and two reviews.
6. Commit: `build: lock dependencies and collect release evidence`.

### Task 24: Add offline end-to-end and fault-injection release suite

**Files:**

- Create: `tests/e2e/test_offline_pipeline.py`
- Create: `tests/e2e/test_crash_recovery.py`
- Create: `tests/e2e/test_security_boundaries.py`
- Create: `frontend/e2e/core-journeys.spec.ts`
- Create: `frontend/playwright.config.ts`
- Modify: `frontend/package.json`
- Modify: `docs/traceability.md`
- Modify: `docs/implementation-log.md`

**Steps:**

1. Add fixture-based end-to-end tests covering all four local inputs, Bilibili/YouTube subtitle
   fixtures, cloud success/fallback/unknown, local ASR fixture adapter, translation/notes success
   and independent failure, exports regenerated from JSON, no durable media, and every public route.
2. Add process fault injection at source download, transcode, cloud request, local ASR, translation,
   notes, cleanup, API, and worker shutdown. Verify durable recovery and no duplicate paid call or
   transcript overwrite.
3. Add security journeys for Host/Origin/CSRF, hostile proxy environment, SSRF/redirect/DNS
   rebinding, filename/path traversal, command argv, secret/database/log/dist scan, and production
   docs/CORS/bind settings.
4. Add Playwright keyboard/focus/aria-live/viewport/zoom/reduced-motion flows, automated axe
   checks, recorded WCAG 2.2 AA contrast checks, non-color status assertions, long-transcript
   accessible-full-text mode, rights acknowledgement, and model install/cancel/recovery UI.
5. Run backend, frontend, browser, compile, dependency, diff, and secret-scan verification from a
   clean dedicated D-drive test directory.
6. Update every FR/NFR traceability row with authoritative code/test evidence; leave live-only
   claims explicitly unverified.
7. Run independent specification, code, security, and release reviews; resolve all Critical and
   Important findings.
8. Commit: `test: add vtnote offline release qualification`.

### Task 25: Run the authorized live POC and freeze release decisions

**Files:**

- Create: `tools/poc_manifest.schema.json`
- Create: `tools/run_live_poc.py`
- Create: `tests/test_poc_harness.py`
- Create: `docs/poc/README.md`
- Create after execution: `docs/poc/<date>-summary.md`
- Modify after execution: `docs/product-requirements.md`
- Modify after execution: `docs/technical-decisions.md`
- Modify after execution: `docs/traceability.md`
- Modify after execution: `docs/implementation-log.md`

**Steps:**

1. Write offline tests for manifest validation, explicit `--allow-network --allow-billing`, content
   authorization acknowledgement, redacted event capture, no credentials in results, resume without
   repeating completed paid requests, raw-metric preservation, and aggregate calculations.
2. Implement the harness so it refuses to run without 30–50 authorized samples, required category
   coverage, current Tencent ASR/COS and Aliyun Bailian consent, and a dedicated D-drive output
   directory.
3. Run all offline harness tests. Do not make a live request until the user supplies/approves the
   corpus, credentials, Aliyun Bailian Beijing workspace profile/model, and expected billing.
4. With that authorization, execute the PRD corpus for platform/subtitle, Tencent standard/local
   ASR, inline/COS routes and cleanup, Aliyun translation/notes, direct-only networking, EJS/Deno,
   performance, memory, cost, timestamp, and recovery metrics.
5. Record failures rather than excluding them. Freeze THR-001–005, provider eligibility, YouTube/
   Bilibili release status, FFmpeg distribution route, and any scoped release disclaimer from
   evidence.
6. Run the complete Task 24 suite again, final verification-before-completion, and independent
   release review.
7. Commit: `docs: record vtnote v1 live qualification`.

## Completion boundary

Tasks 1–24 can be completed without secrets or billable network calls. Task 25 implementation can
also be completed offline, but its live execution and the final “release qualified” claim require
the user-authorized corpus and provider accounts. If those external inputs are absent, the project
must report “implementation complete; live release qualification pending” rather than inventing
results.
