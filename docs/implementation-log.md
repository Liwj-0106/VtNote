# VtNote implementation log

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
