# VtNote implementation log

## 2026-07-24

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
