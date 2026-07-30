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

### Task 4 completion: direct-only pinned HTTPS transport

- Added an explicit HTTP/1.1-over-TLS connector that dials only vetted DNS addresses while retaining
  the normalized hostname for SNI, certificate verification, and `Host`. It does not consult proxy
  environment variables, netrc, browser state, cookies, or credential helpers.
- Added reviewed exact-host page/extractor policies and an expiring resource policy that accepts
  only a typed, in-memory controlled-extractor host projection. Every redirect hop revalidates
  HTTPS/443, host policy, the complete public DNS answer set, and the connected peer IP.
- Request sanitization strips authentication, Cookie, Proxy, connection-specific, hop-by-hop, and
  caller-owned Host/encoding headers. Response handling ignores `Set-Cookie`, bounds header count,
  header lines, request targets, redirects, connect/read timeouts, wire bytes, and decoded bytes.
- Added one shared incremental body reader for `read`, `readline`, iteration, close, and context
  management. Gzip/deflate output is bounded inside the decompressor, and ambiguous framing,
  duplicate encodings, truncated streams, and oversized declared or observed bodies fail closed.
- Safe transport exceptions expose only a fixed category and normalized allowed host; URL queries,
  DNS addresses, headers, bodies, and low-level socket/decompressor exceptions are not chained into
  public errors.
- No real network or billable request was made, no dependency changed, and no project or user file
  was deleted. The focused suite passed 38 tests and the full backend suite passed 384 tests;
  `compileall` and `pip check` passed.

### Task 5 completion: controlled yt-dlp bridge and YouTube runtime readiness

- Added immutable runtime manifests and granular offline readiness inspection for pinned
  `yt-dlp==2026.7.4`, `yt-dlp-ejs==0.8.0`, and Deno 2.8.1. Distribution display versions normalize
  to numeric triples, while EJS integration, package/executable hashes, D-drive containment,
  reparse points, executable version, and exact `DENO_DIR` are checked independently.
- Left release hashes intentionally unconfigured until approved artifacts are recorded. The
  current machine truthfully reports YouTube unavailable with `ejs_missing`,
  `manifest_unconfigured`, `deno_missing`, `deno_dir_missing`, and
  `deno_dir_env_mismatch`, while Bilibili remains available.
- Added a controlled yt-dlp subclass whose request director contains only
  `VtNoteRequestHandlerRH`; urllib, requests, websockets, proxy discovery, cookies, browser
  credentials, client certificates, remote EJS components, system Node fallback, user plugins,
  external downloaders, postprocessors, and arbitrary option dictionaries are not registered.
- Bound probe instances to reviewed page/extractor-aux policies and resource instances to one
  expiring exact-host policy. Request extensions, proxies, authentication headers, policy
  selection, and unexpected transport exceptions fail with bounded diagnostics.
- Mapped the Task 4 bounded response into yt-dlp without a second body buffer or limit. All
  read/line/iteration/context/close paths share the underlying response and close it once.
  Probing always calls `extract_info(..., download=False)`, and yt-dlp's injected chapter output
  template is removed after initialization so only the static controlled template remains.
- Added `yt-dlp-ejs==0.8.0` to both dependency manifests without installing it or downloading
  Deno. No live platform request or package/runtime download was made. The focused suite passed
  36 tests and the full backend suite passed 420 tests.

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

### Tasks 10-11: GPU-only local ASR and durable cloud/local routing

- Added a lazy `faster-whisper` adapter fixed to the managed `large-v3-turbo` revision, CUDA, `int8_float16`, VAD, segment timestamps, and `local_files_only=True`. It never selects CPU, reports CUDA absence explicitly, checks cancellation between lazy segments, and records model-file and CUDA-library provenance.
- Migrated only the mutable default from `device=auto` to `device=cuda`. Historical task snapshots remain immutable and fail with `legacy_local_asr_snapshot_requires_retry`.
- Added snapshot-only ASR routing for local, cloud, and auto modes. Tencent create is attempted at most once; known auto-mode failures and unknown outcomes can fall back locally, while unknown outcomes retain a possible-charge warning and are never blindly resubmitted.
- Added an explicit `waiting_external` stage state so cloud polling is handled by the durable Tencent reconciler without occupying the main worker. Parsed sentence results, not raw provider responses, are persisted for query-only crash recovery and immutable publication.
- Added private-COS routing, deterministic object reuse after a crash, provider-terminal cleanup scheduling, cancellation that stops the local stage without abandoning remote cleanup, and a global GPU lease.
- Added a compact normalized local-ASR recovery artifact before final transcript publication. A process loss after inference or file publication resumes without repeating inference, while conflicting pre-existing content remains immutable.
- Added end-to-end coverage for local/cloud/auto routing, inline/COS submission, known/unknown fallback, provider expiry, cancellation, main-loop fairness, exact snapshot credential revisions, crash windows, and publication races. No user-owned media is modified or deleted.

### Task 12: domestic Aliyun Bailian chat boundary

- Replaced active arbitrary `openai_compatible` configuration with one compile-time `aliyun_bailian` adapter. A lowercase DNS-label workspace ID derives the exact Beijing business-workspace endpoint; foreign regions, relays, credentials in URLs, ports, paths, queries, redirects, environment proxies, and `/models` discovery are not accepted.
- Added a non-streaming `json_object` Chat Completions contract with a 64-KiB request and 256-KiB response boundary, one explicitly bounded 429 retry, no replay after ambiguous POST outcomes, strict response/result validation, safe request/model/usage normalization, and prompt/credential-safe representations and errors.
- Added structured Bailian API-key bundles, conservative context/output limits, revision-bound capability fingerprints, a no-charge static policy validation, one explicitly acknowledged billable capability test, and independent chat-data consent. Consent covers subtitle cues, title/metadata, target/output language, and a custom prompt; it explicitly excludes audio.
- Added startup migration that archives legacy arbitrary chat connections/profiles, clears their defaults/test/consent state, fails active legacy snapshots with `legacy_chat_endpoint_blocked`, and blocks legacy credential access before secret-store or transport use.
- Updated durable task snapshots so translation and notes require both a current capability test and current chat-data consent. Provider/model/options/workspace/key changes invalidate both gates without altering historical snapshots.
- Added adapter, configuration, API, migration, lifecycle, security, snapshot, and redaction regression coverage. No real model request was sent, no credential was persisted in SQLite or logs, and no user file was modified or deleted.

### Task 13: cue-aligned transcript translation

- Added deterministic domestic-chat translation requests that keep fixed system instructions separate from transcript JSON data and explicitly carry the target language, cue IDs, timestamps, and text.
- Batches are greedily bounded by both 30 cues and the complete canonical 64-KiB request body. A single cue that cannot fit is rejected without truncation or a provider call.
- Translation responses must contain one exact ordered cue-ID set, nonempty text, and no unknown fields. A structurally invalid batch receives one retry round only, split into no more than two 15-cue subbatches; transport, filtering, truncation, oversized-response, and unknown-submission errors are not replayed.
- Added cancellation checks before and after each remote call and before publication. All batches are validated in memory before one source-hash-bound translation is atomically published, so a failed or canceled attempt leaves no partial artifact.
- Added focused coverage for target propagation, UTF-8 request sizing, order/ID/schema failures, retry bounds, response bounds, static-instruction isolation, cancellation, source hashing, and atomic publication. The full suite passed with 686 tests; no real model request was sent.

### Task 14: cited AI note generation

- Added summary, key-point, and custom-prompt note templates with explicit output-language data. Fixed system instructions remain separate from transcript/custom-prompt JSON; the plaintext custom prompt is held only for the active calls and is not added to the note document or provenance.
- Map inputs are ordered transcript cues bounded by both 48 KiB of canonical cue JSON and the complete 64-KiB chat request. Generation stops before any call when one cue cannot fit or more than 24 initial chunks would be required.
- Added deterministic reduce grouping with a four-level maximum. Every map citation must exactly match a cue in its source chunk; every reduce citation must also descend from a child citation, preventing a later model call from inventing unrelated transcript evidence.
- Added a strict note schema with task/transcript hash, template/language, requested and actual model, cited summary, and cited key points. Markdown is rendered locally with stable cue IDs, human-readable time ranges, AI provenance, and a warning to verify names, numbers, terminology, and citations.
- Cancellation and all schema/filter/truncation/unknown-submission/oversize failures stop without replay or partial publication. Only a fully validated document is atomically written. The full suite passed with 708 tests; no real model request was sent.

### Task 15: durable optional AI stage orchestration

- Added independent translation and notes worker handlers after the immutable transcript stage.
  Each handler reads only the task snapshot, verifies the exact Bailian profile/test capability and
  revision-bound text-data consent against SQLite, and performs no secret-store or network access
  when that authorization is missing or stale.
- Production handler construction now shares one strict Bailian credential resolver while keeping
  translation and notes as separate durable branches. Custom prompts are unprotected only inside
  the concrete notes attempt, remain user-data rather than system instructions, and are absent from
  the generated Markdown.
- Added artifact-aware crash recovery. A valid source-hash-bound translation or note metadata file
  published before a process loss completes the recovered attempt without a duplicate model call;
  a conflicting or corrupt artifact fails closed.
- Persisted `chat_submission_unknown`, a possible-duplicate-charge warning, and a closed safe error
  code. Automatic replay remains prohibited. An explicit AI retry creates only a new AI attempt,
  keeps the immutable task snapshot, and requires a durable `charge_acknowledged=true` override.
  Source and transcription attempts and artifacts are not rerun or replaced.
- Optional branch failures preserve the original transcript and any successful sibling result,
  aggregating to `completed_with_warnings`. Focused AI orchestration, configuration, retry, API, and
  worker-transition coverage uses only scripted clients; no real model request or credential was
  used.

### Task 16: UI-facing read models and local storage controls

- Added read-only health and capability inspection for SQLite, owned storage, FFmpeg, the managed
  YouTube runtime, CUDA, and the pinned local model. Inspection performs no downloads or setup and
  returns only typed capability state plus fixed upload limits; credentials and configuration
  secrets are not included.
- Added stable cursor pagination for task history and public UTC lifecycle timestamps for tasks,
  items, and stages. Existing diagnostic redaction remains the only path into stage evidence and
  warning read models.
- Added typed transcript, translation, note, and sanitized execution-summary reads. Translation
  artifacts are revalidated against the immutable transcript, result reads are size-bounded, note
  files remain UUID-addressed below the item root, and execution summaries omit source locators and
  configuration snapshots.
- Added deterministic JSON/Markdown execution-summary rendering and safe ASCII attachment names for
  on-demand exports.
- Added aggregate managed-cache status, verified trash listing, and CSRF-protected restore. Public
  trash records omit relative paths and hashes; restore still uses the existing typed
  `RuntimeAssetService` boundary and writes its cleanup audit event.
- Added focused API/readiness tests and ran the complete backend suite: 720 tests passed. No cloud
  request was sent and no user file was modified or deleted.

### Tasks 17-20: Claude-inspired local web interface

- Added a React/TypeScript/Vite interface with a warm paper-and-ink palette, restrained terracotta
  accents, local SVG icons, and no third-party web resources. The desktop navigation collapses
  from 232px to 64px; mobile uses a focus-trapped drawer with Escape close and a skip link.
- Implemented compact task creation, probe/upload progress, domestic-only Tencent ASR and Aliyun
  Bailian configuration, readiness/setup, defaults, storage recovery, task history, durable stage
  inspection, transcript-first results, translation, cited Markdown notes, and safe exports.
- Kept browser persistence to one sidebar boolean. Credentials, source URLs, task identifiers,
  custom prompts, and processing state are not persisted in web storage.
- Verified the real interface at 1440px expanded/collapsed and 375px with a headless Chromium
  session. Fixed a native browser `fetch` binding defect and a collapsed-brand overlap found by
  that inspection. Frontend lint, eight focused tests, and the production build passed.

### Task 21: production SPA serving and process supervision

- FastAPI now serves the built SPA and hashed assets only when a valid frontend build exists.
  Explicit UI routes receive GET/HEAD fallback, while API, disabled documentation, unknown paths,
  and mutation methods retain JSON 404/405 security boundaries.
- Added `python -m vtnote` and the `vtnote` console entry point. The loopback-only supervisor starts
  explicit API and worker argv without a shell, checks port conflicts before spawn, performs
  bounded child restarts, and terminates then kills only unresponsive children during shutdown.
- Added production construction for source, Tencent/local ASR, Aliyun translation/notes, and the
  managed local-model installer. Production API docs remain disabled.
- Backed up and removed generated TypeScript build metadata/emitted config files from source
  control; the source tree now ignores those build products. The API/static/launcher focused suite
  passed with 44 tests and no cloud request.

### Task 22: secure logs, maintenance, and diagnostics

- Added per-process rotating JSON logs under the configured D-drive runtime root. Log records are
  single-line, field-bounded, and redact credential-shaped values, bearer tokens, custom prompts,
  raw-response fields, and Windows paths before serialization.
- Added a lease-guarded maintenance loop inside the supervised worker service. Each pass purges due
  recoverable trash through the existing audited runtime-asset boundary and performs at most one
  due Tencent query/expiry/COS cleanup action. Provider cleanup resolves the immutable submission
  stage and never stores credentials in its lease or result state.
- Added a deterministic metadata-only diagnostic zip and a settings-page download action. The
  bundle reports only version/platform/storage availability and database reachability; it excludes
  database rows, credentials, prompts, transcript content, logs, and configured absolute paths.
- Focused logging, rotation, redaction, diagnostics, maintenance lease, provider worker, and
  launcher checks passed. Frontend lint and the production build also passed; no live provider
  request was sent.

### Task 23: reproducible dependencies and release evidence

- Added an exact verified Python environment lock alongside the native Conda pins, plus installation,
  backup/restore/removal guidance, a release checklist, and a third-party component/license register.
- Froze the YouTube runtime evidence for `yt-dlp-ejs 0.8.0` and the official Deno 2.8.1 Windows x64
  artifact. Added a user-acknowledged installer that accepts only the official zip/executable
  hashes, extracts only `deno.exe`, and writes it below the D-drive managed runtime root.
- Installed the pinned EJS package and verified official Deno runtime on this development machine.
  The launcher selects the exact managed `DENO_DIR`; offline readiness now reports YouTube available
  with no remote EJS components or system Node fallback.
- Added deterministic release-evidence collection for source revision, Python/frontend locks,
  FFmpeg version/build flags/binary, Deno/EJS, and the local-model manifest. Absolute paths are
  rejected from the output. The current Conda FFmpeg is truthfully classified
  `development_gpl_only` because its build contains `--enable-gpl`; it cannot be labeled an LGPL
  release candidate.
- Focused YouTube/runtime tests passed with 71 checks, and the release-evidence test passed. The Deno
  archive and checksum evidence remain in the task-specific D-drive cache and are not committed or
  redistributed.

### Task 24: offline release qualification

- Added an offline API-to-worker fixture for uploaded SRT files. It creates a durable task through
  the real multipart API, claims the source stage from SQLite, publishes the immutable normalized
  transcript and source copy, and regenerates Markdown through the public export endpoint.
- Added a process-loss recovery fixture proving that an expired lease is reclaimed on the same
  stage-run ID and attempt, with one incremented recovery generation instead of a duplicated run.
- Added a combined security fixture for exact Host, Origin, double-submit CSRF, SSRF/private-address
  rejection, hostile upload filenames, and diagnostic-bundle path exclusion.
- Added Playwright journeys for the Claude-inspired shell, sidebar-only browser persistence, mobile
  focus trapping and Escape behavior, horizontal-overflow prevention, domestic-only provider
  labels, diagnostics download, and automated WCAG checks. The checks found and corrected a
  tertiary-text contrast defect before passing.
- The three backend E2E tests and three browser journeys passed. They use only local fixtures and
  did not send a provider request or modify a user-owned source file.

### Task 25: guarded live-POC harness

- Added a versioned manifest contract for 30–50 explicitly authorized samples. Validation enforces
  minimum Bilibili, YouTube, local-media, Mandarin, English, and mixed-language counts plus complete
  duration, subtitle, audio-condition, provider-route, and recovery coverage.
- Live initialization requires independent `--allow-network` and `--allow-billing` switches, an
  owner-approved CNY ceiling, current Tencent upload and Bailian text-consent revision attestations,
  the official Bailian Beijing region, direct-only network evidence, and a dedicated D-drive output.
- Added a resumable atomic evidence journal tied to a SHA-256 of the exact manifest and its
  authorized sample IDs. Completed or possibly submitted billable stages cannot be recorded twice;
  cumulative recorded billing cannot exceed the approved ceiling.
- Raw numeric measurements remain available for deterministic aggregates. Credential-shaped fields,
  prompts, URLs, source paths, tokens, and raw responses are excluded or redacted from results.
- Four offline harness tests passed. No live POC was run because no user-authorized 30–50-sample
  corpus, provider credentials, or billing approval was supplied in this implementation session.
  The honest release state remains: implementation complete; live release qualification pending.

### Tencent capability-test sample lifecycle

- Replaced the manual sample-ID field with a native 2–10 second audio/video picker. The browser
  streams the sample through the same bounded multipart, filename, media-probe, and D-drive
  ownership boundary as normal uploads, then receives only an opaque one-shot item ID.
- Capability-test samples are represented by hidden canceled tasks so they cannot be claimed by a
  worker or clutter task history. The profile-test endpoint accepts only those dedicated items;
  passing an ordinary task item cannot trigger its media cleanup.
- After the Tencent test attempt, source and converted test audio move into the existing 24-hour
  recoverable trash. Unused samples remain available for one hour and are then moved to the same
  trash by the lease-guarded maintenance pass.
- Focused API, task-history, maintenance, and runtime-asset lifecycle coverage passed with 51
  checks. Frontend lint and production build passed after the file-picker integration.

### Final offline verification

- The complete backend suite passed: 739 tests in 31.81 seconds. The only warning is the known
  pytest `cache_dir` notice caused by intentionally disabling the cache provider for that run; it
  is unrelated to application behavior.
- Frontend verification passed with six Vitest files/eight tests, ESLint, a production Vite build,
  and three single-worker Playwright journeys in installed Chrome. The initial aggregate command
  exposed that Vitest also collected `e2e/*.spec.ts`; the Vitest include boundary now explicitly
  contains only `src/**/*.test.{ts,tsx}`, after which the full frontend sequence passed.
- `pip check` reported no broken requirements and `npm audit --audit-level=high` reported zero
  vulnerabilities. Python bytecode compilation, `git diff --check`, and a credential-pattern scan
  over runtime, frontend, and tooling sources passed.
- A real production API process started with isolated D-drive data/runtime roots on loopback,
  returned `status=ok` from `/api/health`, served the built SPA root with HTTP 200, and was then
  stopped. No provider request was sent.

### Bilibili live probe compatibility

- Made source-probe failures visible before a source becomes ready and added a specific diagnostic
  for proxy Fake-IP DNS answers.
- Added fixed public yt-dlp request headers and a per-bridge Bilibili anonymous session containing
  only `buvid3`, `b_nut`, and `sid`. Browser/login cookies remain rejected, values are never logged
  or persisted, and the in-memory session is cleared when the request handler closes.
- Added transport tests for cookie name/domain/value allowlists, cross-platform rejection, fixed
  public headers, and in-memory cleanup. The focused source/transport suite passed with 72 checks.
- After adding Bilibili domains to the local Clash Verge Fake-IP exclusion list, the production API
  successfully probed `BV1wVKHeKEbB`: canonical Bilibili URL, 330.41-second duration, and no
  platform subtitle tracks. The item therefore requires audio ASR.
