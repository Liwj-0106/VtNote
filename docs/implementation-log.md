# VtNote implementation log

## 2026-07-29

### Task 2 completion: durable worker leases and stage transitions

- Added a SQLite-backed worker store whose claim, heartbeat, terminal transition, recovery, and
  resource-lease operations each own a short `BEGIN IMMEDIATE` transaction. Handler execution
  occurs only after the claim transaction commits.
- Claims select only the latest stage attempt, enforce the shared dependency graph, and use stable
  task-created/stage/item/attempt ordering. Completion, failure, heartbeat, and cancellation are
  guarded by owner, attempt, recovery generation, unexpired lease, and active task/item state.
- Added at-least-once crash recovery, cooperative cancellation checkpoints, immutable retry
  overrides, worker heartbeat records, bounded idle backoff, and claim-owned generic resource
  leases. Resource ownership survives heartbeat renewal and is released on every terminal or
  recovery path.
- Added real file-backed, independent-engine concurrency coverage plus dependency, stale-owner,
  cancellation-race, recovery, resource, aggregation, worker-loop, and immutable-context tests.
  Both requirements and code-quality reviews found no remaining Critical or Important issue.
- No network or billable request was made, no dependency changed, and no project or user file was
  deleted. The focused suite passed 19 tests and the full backend suite passed 351 tests;
  `compileall`, `pip check`, and whitespace checks passed.

### Task 3 completion: unified source adapter contracts

- Made `vtnote.sources` the sole owner of source probes, subtitle tracks, adapter outcomes, and
  candidate-level failures. Removed the duplicate API probe protocol and DTOs.
- Added runtime validation for six source kinds, non-empty bounded titles, nonnegative durations,
  remote/local canonical-URL rules, three subtitle kinds, and unique pre-filter candidate
  ordinals.
- Opaque `trk_<sha256>` references are derived only from source kind, normalized language,
  subtitle kind, format, and stable ordinal. Resource URLs, query strings, credentials, and
  adapter-supplied arbitrary IDs cannot enter the reference generator.
- API probe responses now use the canonical `subtitle_tracks` schema and never serialize the
  private redirect trace. The trace remains centrally revalidated by the existing source URL
  policy for compatibility until pinned transport replaces it.
- Subtitle fallback now advances only for a bounded `SubtitleCandidateError` or a locally
  converted subtitle-parse failure. Transport, filesystem, programming, and arbitrary adapter
  validation errors propagate without silently downloading audio.
- No network or billable request was made, no dependency changed, and no project or user file was
  deleted. The focused suite passed 47 tests and the full backend suite passed 364 tests.

## 2026-07-28

### Task 1B concurrency and transaction review follow-up

- Serialized stage retry validation and insertion with SQLite `BEGIN IMMEDIATE`, so the
  expected-attempt check and final cloud-profile revision/authorization validation operate under
  one write reservation. A concurrent profile update cannot invalidate the snapshot between final
  validation and attempt persistence.
- Reject retry calls made through a Session with pending or already-flushed unrelated writes
  without rolling those writes back. Harmless read-only autobegin transactions are discarded
  before acquiring the retry reservation.
- Classified SQLite extended BUSY/LOCKED result codes by their primary code while continuing to
  re-raise unrelated operational failures. A competing retry now receives the stable refresh
  conflict instead of a misleading non-retryable-stage error.
- Added deterministic two-Session, profile-update serialization, pending-write preservation,
  `BEGIN IMMEDIATE`, extended-result-code, and unrelated-I/O regression coverage. The expanded
  focused suite passed 149 tests and the full backend suite passed 332 tests; `compileall`,
  `pip check`, and `git diff --check` passed.
- No network or billable request was made, no dependency changed, and no project or user file was
  deleted.

### Task 1 completion: lifecycle and execution-evidence contracts

- Added a task-level terminal-reason code and made repeated cancellation idempotent only for a
  confirmed user cancellation. Cancellation now uses a status compare-and-swap before atomically
  updating item/stage states, so a stale request cannot overwrite a concurrently completed task;
  a successful stage retry clears the old terminal reason.
- Added bounded stage progress, execution evidence, and provider status persistence plus public
  views. Progress messages, fallback reasons, compiled provider IDs, and provider statuses use
  explicit registries; model provenance must match the immutable task snapshot. Both writes and
  reads reject or hide invalid/sensitive values, including opaque Tencent TaskId, RequestId, LogId,
  and SecretId-shaped tokens.
- Made the additive legacy cancellation backfill run only when the old database actually gains the
  new column, preventing later startups from reclassifying current-schema records.
- Corrected browser multipart request sizing to
  `max(media_limit, subtitle_limit) + metadata_limit + overhead_limit`.
- Added RED/GREEN coverage for cancellation concurrency and idempotency, retry cleanup, migration
  scope, JSON-safe progress bounds, closed evidence codes, secret-free fail-closed reads, and upload
  sizing. Independent specification, code/security, and Tencent-boundary reviews found no remaining
  Critical or Important issues.
- No external model/API call was made, no dependency was added, and no project or user file was
  deleted. The isolated final focused suite passed 90 tests, the full suite passed 277 tests, and
  D-drive `compileall`, `pip check`, and `git diff --check` passed.

### Domestic-provider V1 decision and plan alignment

- Froze V1 external model calls to domestic services only. Tencent Cloud standard Recording File
  Recognition is the sole compiled cloud ASR adapter, and Aliyun Bailian's official China
  (Beijing) workspace endpoint is the sole compiled chat adapter. OpenAI compatibility describes
  only Bailian's wire protocol and does not authorize an OpenAI or arbitrary relay endpoint.
- Chose Tencent `CreateRecTask` plus `DescribeTaskStatus` so a returned task ID can be persisted and
  recovered without repeating a paid submission. Encoded OGG/Opus files at or below 4,500,000 bytes
  use inline Base64; larger eligible files use a short-lived URL for a private, app-owned COS
  object in `ap-guangzhou`.
- Defined `submission_unknown` for a crash or timeout before a Tencent task ID is durably known.
  The worker never automatically resubmits that paid request; local ASR remains available, while
  any cloud resubmission requires an explicit possible-duplicate-charge acknowledgement.
- Audited the user's private `Liwj-0106/Biji` repository read-only from commit
  `6b09f600bc767a8fa26efce9f5e03a85c9fab841`. Its Tencent request/polling and audio-normalization
  flow informed the boundary review, but no source was copied; its synchronous in-process pipeline,
  chunking workaround, and arbitrary relay-style LLM client were not adopted.
- Closed independent contract-review findings before implementation: Tencent COS is deleted
  immediately only after a known provider terminal state; cancellation/unknown submission defers
  cleanup until provider completion or URL expiry plus grace. A persistent one-query-per-claim
  reconciler prevents a 24-hour cloud wait from monopolizing the main worker.
- Simplified Tencent credentials to long-lived SecretId/SecretKey in a least-privilege subaccount,
  fixed COS to `ap-guangzhou`, defined the `zh_en_dialects` scope and official error-code mapping,
  and replaced the unreliable silent profile test with a user-authorized short speech sample.
- Added deterministic quarantine for legacy Volc/arbitrary chat configurations, separate static
  policy validation/profile capability testing/audio-or-text data consent, Bailian ambiguous-POST
  no-retry behavior, UTF-8 request limits, JSON at every AI layer, citation lineage, and AI
  provenance/review labels.
- Updated the product, technical, website, source/reference, traceability, and remaining-plan
  documents to this provider decision. This entry records research and planning only; production
  source, tests, dependencies, runtime configuration, credentials, and user data were not changed
  or deleted by this documentation pass.

### Remaining-scope research and implementation-plan refresh

- Re-audited the current code boundary at `e4a8fdf`: Tasks 1, 2, 3A, and 3B foundations exist;
  durable worker execution, live platform adapters, cloud/local ASR calls, AI processing, React,
  launcher, and release qualification remain.
- Refreshed external reference evidence for BiliNote, BiliSum, AI Video Transcriber, yt-dlp
  EJS/Deno, faster-whisper, and Volc Flash. No third-party source was copied.
- Added ADR-014 with conservative server-owned implementation limits that unblock test-first
  implementation while keeping final release values gated by the 30–50 item POC.
- Added ADR-015: Volc `api_key` and AppKey are one versioned atomic Credential Manager secret
  bundle referenced by the database; changing either field invalidates test/upload consent.
- Replaced the oversized remaining work description with
  `docs/superpowers/plans/2026-07-28-vtnote-v1-completion.md`: 25 independently testable/reviewable
  tasks covering lifecycle evidence, durable worker, secure source transport, yt-dlp/EJS/Deno,
  Bilibili/YouTube, Volc/local ASR, AI, React, supervisor, maintenance, release evidence, offline
  fault injection, and authorized live POC.
- This entry records research and planning only. No production source, tests, dependencies,
  environment, project configuration, or user data were changed or deleted in this documentation
  pass.

## 2026-07-24

### Product-document final external-contract review

- Starting from documentation commit `5ec52f8`, closed the final independent
  review's two Important external-contract gaps without changing source, tests,
  dependencies, environment, project configuration, or user data.
- Completed the Volc flash request/response contract with
  `X-Api-Sequence: -1`, secret `user.uid`/AppKey handling, and
  `X-Api-Status-Code` routing for success, silent audio, request/input errors,
  server busy/internal errors, unknown valid provider codes, and malformed or
  missing outcomes. HTTP 200 alone is not treated as business success, and no
  classified path permits blind paid resubmission.
- Added the current yt-dlp full-YouTube dependency chain. The initial research
  combination is `yt-dlp==2026.7.4`, `yt-dlp-ejs==0.8.0`, and Deno 2.8.1,
  pinned and hashed under managed D-drive storage with Deno cache on D:. It
  forbids automatic updates, fallback to the existing C-drive system Node, and
  runtime remote EJS downloads; readiness, corpus, license, and SBOM gates are
  explicit. The current environment remains not ready because EJS/Deno are not
  installed.
- Expanded the official source register through SRC-039 and synchronized the
  PRD, website specification, ADRs, reference audit, and traceability matrix.
  The OpenAI audio response-format inconsistency is now a per-model capability
  test rather than an absolute claim.
- Final validation found 19 FR, 10 NFR, 5 THR, 13 ADR, and 39 SRC registrations
  with zero trace/source mapping delta, broken relative link, BOM, trailing
  whitespace, legacy live-citation token, or out-of-scope changed file. The
  unchanged codebase passed 238 tests in the final 13.28-second run; D-drive `compileall`,
  `pip check`, and `git diff --check` passed.
- This pass remained research/documentation-only. No third-party project source
  was copied and no repository or user file was deleted.

### Product-document Important review follow-up

- Starting from documentation commit `3240f5679976bd85681bd504ff2f505877a98cc6`,
  closed the independent review's Important findings without changing source,
  tests, dependencies, environment, project configuration, or user data.
- Registered the user-provided `bilinote.net` page as an unverified,
  high-volatility BiliNote Pro marketing input distinct from the upstream
  README's `bilinote.app` link and the local 2.4.4 archive. Registered the
  private ChatGPT URL as unread/unreviewed after the recorded login redirect
  and enterprise Chrome-policy block; it is not evidence until the user exports
  or pastes its body.
- Fixed the platform network contract at `trust_env=false`, made the one
  direct-only YouTube timeout an environment observation rather than a platform
  conclusion, added China-network POC/local-file fallback, and required a new
  ADR/threat model/user consent before any explicit trusted proxy.
- Restored the approved local ASR default
  `large-v3-turbo/int8_float16/VAD/segment/GPU concurrency=1`, fixed model/CUDA
  artifacts to managed D-drive storage, and made POC a validation/review gate
  rather than permission for silent default changes. CPU is diagnostic/future
  fallback, not a V1 release requirement.
- Expanded the Volc flash contract for 16 kHz mono OGG/Opus,
  Base64 `audio.data`, fixed resource/model, duration/binary/Base64/language
  preflight, routing/error classes, unknown paid outcome, revision-bound
  authorization, sanitized `X-Tt-Logid`, and no raw cloud response persistence.
  Added a bounded Volc/OpenAI/AssemblyAI paid-ASR matrix without unmeasured
  quality or price ranking.
- Added FFmpeg official legal/license evidence. A read-only check of
  `D:\ProgramData\Anaconda3\envs\vtnote\Library\bin\ffmpeg.exe` reported 7.1.1
  with `--enable-gpl --enable-version3 --enable-libx264 --enable-libx265` and
  `--enable-shared --disable-static`, and no `--enable-nonfree`, so this developer
  build is treated as GPL v3+; the actual release build remains a separate
  buildconf/source/SBOM/NOTICE gate. VtNote directly reuses FFmpeg rather than
  implementing codecs or containers.
- Fixed AI-note templates/defaults and the direct-original, chronological
  chunk/map/reduce, cue-resolvable timestamp-reference contract. Pinned the
  yt-dlp extractor evidence commit, added WhisperX PyPI evidence, and recorded
  youtube-transcript-api as reference-only/not adopted.
- Final follow-up validation found 19 FR, 10 NFR, 5 THR, and 34 SRC registrations
  with no requirement/source mapping delta, broken relative links, UTF-8 BOM,
  trailing whitespace, legacy live-citation token, or out-of-scope changed file.
  The unchanged codebase passed `238` tests in 11.57 seconds; D-drive
  `compileall`, `pip check`, and `git diff --check` also passed.
- No repository or user file was deleted. This follow-up used only
  `apply_patch` for repository edits and did not access or modify global Codex
  configuration.

### Product research, PRD, website, and technical-decision baseline

- Created `docs/product-requirements.md`, `docs/website-specification.md`,
  `docs/technical-decisions.md`, `docs/reference-projects.md`,
  `docs/research-sources.md`, and `docs/traceability.md`. The documents define
  stable FR/NFR IDs, V1/V1.1/Later boundaries, all task/failure/cancel/retry/
  warning states, a 30–50-video POC, website acceptance, ADRs, reference-project
  adoption/rejection, official-source corrections, and requirement-to-plan/test
  traceability.
- Audited the local `D:\Workspace\Project\BiliNote-master` archive without
  changing it. The archive README self-reports 2.4.4, but no Git metadata exists,
  so no local commit or upstream-release equivalence is claimed. No BiliNote or
  other third-party source code was copied.
- Audited first-party sources for Bilibili Open Platform, yt-dlp,
  faster-whisper, WhisperX, VideoLingo, Argos Translate, OpenAI, Volcengine,
  BibiGPT, Videosays, and AssemblyAI as of 2026-07-24. Added explicit corrections
  for historical OpenAI output-format, Videosays pricing, AssemblyAI self-hosting,
  Bilibili subtitle-access, BiliNote provenance, and OpenAI privacy wording.
- Added prominent historical-material notices to
  `docs/deep-research-report-1.md` and `docs/deep-research-report-2.md`; their
  original bodies and unresolved legacy citation tokens remain untouched.
- Baseline verification used the existing `vtnote` Conda environment and a
  dedicated `D:\Workspace\Codex\cache\VtNote-product-docs\` temp root: 238 tests
  passed with one pre-existing pytest `cache_dir` warning. No source, test,
  production configuration, dependency, or user-data file was changed.
- During source-tool discovery, a global Codex MCP entry was added outside the
  repository in error. It was immediately removed after a full configuration
  backup; exact comparison proved that only the added stanza disappeared.
  Backup and SHA-256/mtime rollback evidence are retained under
  `D:\Workspace\Codex\cache\VtNote-product-docs\`. No other global configuration
  content changed.
- No project or user file was deleted. The repository changes in this section
  are documentation-only; final verification and the focused documentation
  commit are recorded in the task report.

### Task 3B: upload/local ingress and media primitives

- Added bounded multipart streaming on the existing task-creation route. The deterministic wire contract is one UTF-8 JSON `metadata` part followed by one file part; browser uploads are written only to typed `D:` runtime paths, receive opaque asset locators, and retain only a bounded sanitized display name.
- Added upload/local-source validation for subtitles and media, typed disposable runtime assets with recoverable trash handling, shell-free bounded FFmpeg/ffprobe argv execution, cloud Opus conversion (16 kHz mono input contract at 32 kb/s), and dependency-injected subtitle/audio source contracts. No platform adapter, ASR call, or worker loop was added.
- Hardened conversion recovery: cloud/local outputs are staged under UUID-typed paths and promoted only after FFmpeg succeeds; non-empty failed staging output is registered and moved to 24-hour trash. A retained cloud output can be restored/reused, and upload cleanup failures record a safe lifecycle event.
- Verified focused Task 3B tests (94 passed) and the full suite (234 passed) using isolated runtime-cache pytest roots; `compileall`, `pip check`, and `git diff --check` passed. No user originals or `D:\Workspace\Project\VtNote-data` content was modified or deleted.

### Task 3B lifecycle review fix

- Moved cloud/local conversion contract validation onto UUID-typed staging files before canonical promotion or active runtime-asset registration. Cloud output now also requires an OGG container in addition to Opus, mono audio, and a 16 kHz OpusHead input rate; local output requires 16 kHz mono PCM.
- Invalid non-empty outputs become `failed_media` assets in recoverable trash, allowing a later valid retry to publish normally. Zero-byte FFmpeg staging files are removed only through an exact item/staging UUID path and write a persistent cleanup event.
- Added RED/GREEN regressions for a successful FFmpeg call returning a non-OGG Opus container, invalid local PCM, and zero-byte staging cleanup, plus characterization of valid active, restored, and move-before-registration canonical reuse. Focused tests passed 98/98 and the full suite passed 238/238; D-drive `compileall`, `pip check`, and diff checks passed.

## 2026-07-18

- Established the original research documents as the Git baseline on `main`.
- Created `feature/vtnote-v1` for implementation.
- No existing project files were modified or deleted.
- Refined the implementation plan into independently reviewable backend, pipeline, AI, frontend, and integration tasks before production code was written.

### Task 1: backend foundation and compact transcript core

- Created `pyproject.toml` with the Python 3.11 package metadata, runtime dependency ranges, pytest dependency, and pytest cache/temp locations on `D:`.
- Created `src/vtnote/__init__.py`, `config.py`, `paths.py`, `schemas.py`, `subtitles.py`, `artifacts.py`, `models.py`, and `database.py`.
- Created `tests/test_schemas.py`, `tests/test_subtitles.py`, `tests/test_storage.py`, and `tests/test_database.py`.
- Added the fixed transcript v1 artifact contract (`schema_version`, language, duration, provenance, and timed segments), SHA-256-bound translation entries, SRT/VTT/ASS parsing, deterministic SRT/VTT/ASS/TXT/Markdown exports, immutable transcript writes, replaceable generated-artifact writes, strict owned-path construction, SQLAlchemy 2 models, and SQLite WAL bootstrap.
- Used the existing Conda environment at `D:\ProgramData\Anaconda3\envs\vtnote`; installed Pydantic 2, pydantic-settings, SQLAlchemy 2, and pytest with pip cache/temp directed to `D:\Workspace\Codex\cache\VtNote-runtime`.
- Moved transient `src/vtnote.egg-info` metadata created by the package check to `D:\Workspace\Codex\cache\VtNote-runtime\backups\task1-package-check\vtnote.egg-info` instead of deleting it.
- Modified this implementation log to record Task 1 work. No other project files were modified, and no project files were deleted.

### Task 1 review-fix pass

- Modified `pyproject.toml` so the documented plain `python -m pytest -q` command imports the `src` package without caller-provided environment variables.
- Modified `schemas.py` and subtitle parsing to enforce `seg_000001`-style IDs and keep `speaker` out of the fixed transcript artifact contract while retaining multilingual language values.
- Modified `paths.py`, `config.py`, and `artifacts.py` with UUID/extension/language-validated typed item paths, same-drive staging, runtime-audio placement, destination-root revalidation, and symlink/reparse-point rejection. The checks reduce accidental or pre-existing path substitution but are not claimed to defeat a privileged process racing the final filesystem call.
- Created `src/vtnote/exports.py` and `tests/test_exports.py` for repeatable, on-demand JSON/SRT/VTT/TXT/Markdown regeneration from immutable transcript and validated translation JSON; no durable export files are created.
- Modified translation writes to validate source hash and exact ordered cue IDs before replacement. Modified SRT/VTT/ASS export handling for representability and stable ASS literal escaping.
- Modified SQLite bootstrap/models/tests for serialized bounded WAL initialization, connection-local busy timeout/foreign-key pragmas, UTC-aware timestamp round trips, mutable JSON options, and deterministic relationship ordering.
- Modified `tests/test_database.py`, `tests/test_schemas.py`, `tests/test_storage.py`, and `tests/test_subtitles.py` with focused regression coverage. No dependencies were added, and no project files were deleted.

### Task 2: FastAPI, configuration/task services, and local security

- Added FastAPI, httpx, keyring, and uvicorn runtime dependency ranges and installed them into the existing `vtnote` Conda environment with pip cache/temp under `D:\Workspace\Codex\cache\VtNote-runtime`.
- Refactored the pre-release schema directly after confirming `D:\Workspace\Project\VtNote-data\vtnote.db` did not exist. Added separate provider-connection, processor-profile, and default-setting tables; durable non-secret task pipeline snapshots; fixed item artifact paths; profile/context revision state; cloud-upload authorization revisions; and case-insensitive unique names.
- Added injected memory/keyring secret stores, direct configuration services, secret replacement/clear/rollback behavior, purpose/protocol compatibility, purpose-specific option whitelists, sanitized connectivity state, safe defaults, and fixed Volcengine flash resource metadata.
- Added durable task enqueue/list/get/cancel and stage-only retry services. Enqueue snapshots schema-v1 non-secret pipeline choices and creates only queued task/item/stage rows; it has no worker or external-media execution path.
- Added FastAPI routes for CSRF issuance, source probing, connections, profiles, defaults, tasks, cancel/retry, upload authorization, connectivity tests, and original/translation on-demand exports. Missing real probe/connectivity adapters return 501.
- Added exact Host/Origin and double-submit CSRF enforcement without CORS; public HTTPS YouTube/Bilibili source validation with IP/private-DNS/fake-suffix/non-443 rejection and redirect revalidation; separate HTTPS-or-loopback-HTTP provider URL validation; and one sanitized API error envelope.
- Added Task 2 tests for configuration revisions and compatibility, secret redaction/rollback, defaults and task overrides, immutable snapshots, queue/cancel/retry semantics, export routes, adapter injection, API statuses, local security, URL/DNS/redirect defenses, fixed pipeline defaults, and Task 1 regressions. No project files were deleted.

### Task 2 review-fix pass

- Disabled FastAPI OpenAPI/Swagger/ReDoc endpoints by default; they are available only through the explicit development setting. Added an outer sanitized error boundary and centralized bounded diagnostic redaction for structured credentials, bearer values, known secrets, and credential references.
- Replaced in-place secret replacement with a prepared credential-reference rotation, and replaced destructive connection/profile deletion with soft archive semantics. Archived configuration remains internally resolvable for already queued task snapshots while public CRUD hides it; active profiles must be archived before their connection.
- Added `connection_id` to non-secret profile snapshots, repaired defaults when profiles are archived, validated dormant profile references, enabled the first successfully tested notes profile once, and persisted an explicit opt-out marker so later user choices are preserved.
- Made PATCH null/no-op/display-name behavior explicit, restricted loopback HTTP to OpenAI-compatible connections, made cancellation idempotent, guarded stage retry prerequisites and duplicate-attempt races, and redacted absolute local source paths from public task views.
- Extended source-probe results with duration, subtitle descriptors, and the reported redirect chain. The Task 3 adapter remains a trusted network boundary: it must disable automatic redirects and validate each peer before I/O; Task 2 validates the complete reported chain but does not claim DNS pinning.
- Browser multipart media/subtitle staging is deliberately deferred to Task 3. Task 2 continues to accept an existing local path only at its internal service boundary and does not claim that contract is usable by browser file inputs.
- Modified the Task 2 source/tests and this log. No project files were deleted.

### Task 2 final boundary-fix pass

- Added the only supported stage-diagnostic write methods. They redact known configuration secrets/references and credential-shaped values before assigning ORM fields or committing; direct diagnostic model mutation is documented as unsupported internal behavior, while public reads retain defense-in-depth redaction.
- Replaced implicit linear retry ordering with an explicit dependency map. Translation and notes each depend on transcription, so a failed or canceled translation no longer blocks a notes-only retry.
- Completed configuration archive lifecycle handling. Active names use SQLite partial unique indexes, nonterminal task snapshots keep their exact archived profile/connection bindings, unreferenced deletes hard-purge records, and `purge_unreferenced_archived()` releases archives after terminal tasks.
- Added a durable credential-cleanup queue containing opaque references and operational metadata only. Credential rotation/deletion records cleanup in the same database transaction, failed secret-store deletion remains visible through non-secret status, cleanup is retryable, and queued references/secrets participate in diagnostic redaction.
- Moved Host/Origin/CSRF checks inside the sanitized middleware boundary and explicitly rejects non-ASCII CSRF values. Defaults PATCH now rejects explicit `null` for non-nullable fields while retaining nullable profile IDs and custom notes prompts.
- Added non-secret credential-cleanup status/retry API routes and focused lifecycle, persistence, retry, PATCH, and malformed-header regression tests. No project files were deleted.

### Task 2 independent-review follow-up

- Added compatibility for databases created before active-name partial indexes. SQLite `create_all()` is not treated as a constraint migration: when a requested connection/profile name collides with an archived row, the archived row is transactionally retired to a reserved UUID-derived internal name before reuse. The operation rolls back as one unit on failure, the reserved prefix is rejected from user input, and active-name uniqueness remains database-enforced on both legacy and new schemas.
- The archived row name becomes non-operational after a collision. Existing task `pipeline_snapshot_json` remains the provenance source for the original display name; Task 3 execution must not derive provenance from the retired configuration-row name.
- Extended archive retention to retryable terminal tasks. A task snapshot remains pinned while the task is nonterminal or any item/stage's latest attempt is failed/canceled; historical failures stop pinning after a later successful attempt.
- Replaced the global active-stage retry block with explicit conflict rules. Translation and notes can be retried in parallel without downgrading running item/task state, while same-stage and upstream source/transcription retries remain blocked by conflicting active work.
- Added a real legacy-schema fixture plus compatibility, rollback, terminal retry, latest-attempt, and parallel-conflict regression tests. No table rebuild was added, and no project files were deleted.

### Task 3A: durable pipeline and runtime-asset foundation

- Extracted the shared stage order, dependency, retry-conflict, terminal-state, and item/task aggregation rules into `pipeline.py`. `TaskService` now consumes the shared rules while retaining its existing API behavior; translation and notes remain independent branches after transcription.
- Extended the SQLite models with source display names, external request/log/submission recovery fields, runtime assets and cleanup events, named resource leases, and worker heartbeats. Added an idempotent additive upgrade that preserves the complete Task 2 schema and rows, supplies a zero recovery count without rebuilding tables, and serializes multi-process startup with SQLite `BEGIN IMMEDIATE`.
- Added typed runtime paths for incoming uploads, item-owned uploads, downloaded audio, cloud OGG, local prepared audio, and recoverable trash. Relative paths are canonical POSIX paths under the runtime root, extensions and roles are allowlisted, and reparse ancestors are rechecked at filesystem boundaries.
- Added immutable source-subtitle persistence and transcript recovery semantics: identical canonical content is accepted after a crash, conflicting content is rejected, and existing transcript/source bytes are never overwritten.
- Added `RuntimeAssetService` as the lifecycle authority for registered disposable files. It validates UUID-relative role paths, reserves each canonical original path across active/trash states, verifies size and SHA-256, performs same-drive moves, records safe cleanup outcomes, supports idempotent trash/restore, purges only due trash after checking task/item activity, recovers filesystem/database crash mismatches, and rejects arbitrary deletion paths. Registered media prevents item deletion so the database cannot silently orphan a managed file.
- Recorded the verified Conda/native environment in `environment.yml`: Python 3.11.15, FFmpeg 7.1.1, CUDA runtime 12.8.90, cuBLAS 12.8.4.1, cuDNN 9.10.2.21, yt-dlp 2026.7.4, python-multipart 0.0.32, faster-whisper 1.2.1, and CTranslate2 4.8.1. CTranslate2 reported one CUDA device; no model or cloud request was used.
- Added focused pipeline, migration, artifact, path, environment, cleanup, reparse, active-task, and crash-recovery tests. No project files or user files were deleted, and `D:\Workspace\Project\VtNote-data` was not touched.

### Task 3A independent-review fix

- Kept typed owned paths lexical until every ancestor has been checked for symlink/junction/reparse substitution; resolved containment is now only the final check. A cross-item audio junction can no longer alias one item's registered media to another item's file.
- Made due purge authorization and deletion one SQLite-linearizable operation. The service rejects caller-pending Session mutations, clears harmless read autobegins, acquires `BEGIN IMMEDIATE` before loading status, expires cached ORM state, and retains the write reservation through runtime-file unlink and asset-row/event commit.
- Added deterministic two-Session race coverage proving a competing queued transition cannot execute between purge authorization and file deletion, while an active transition that commits first is observed and causes purge refusal. No Task 3B behavior or project files were removed.
