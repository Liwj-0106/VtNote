# Task 3B takeover report

Base commit: `95b9bc4`

The previous implementer was interrupted before commit. The current worktree contains
the Task 3B draft and tests. Treat the existing production code as unfinished, but
preserve the test-first behavior already established.

## Verified current state (2026-07-24)

Command:

```powershell
D:\ProgramData\Anaconda3\envs\vtnote\python.exe -m pytest -q `
  tests/test_source_contracts.py tests/test_media.py tests/test_uploads.py `
  tests/test_tasks.py tests/test_api.py tests/test_runtime_assets.py
```

Result: `91 passed, 3 failed`.

Expected RED failures:

1. A `cloud_audio` asset moved to the 24-hour trash cannot yet be restored/reused by
   `FfmpegMediaProcessor.convert_for_cloud()`.
2. A failed FFmpeg call can leave `cloud.ogg`; the next attempt could adopt the
   partial canonical output instead of using typed staging and atomic promotion.
3. If upload failure cleanup cannot trash the registered partial asset, no
   `upload_cleanup` failure event is persisted.

The draft also added tests intended to reject UNC/network shares and Windows device
namespaces. Confirm they fail on the old implementation and pass for the right reason.

## Completion evidence (2026-07-24)

- Kept all three takeover regression tests unchanged. Initial RED was `91 passed, 3 failed`; the failures covered trash restore/reuse, failed FFmpeg canonical-output adoption, and missing upload-cleanup failure audit.
- Implemented UUID-typed conversion staging, atomic promotion, failed-media registration plus recoverable trash, retained cloud-media restoration, and safe upload-cleanup failure events. No network adapter, ASR call, or worker loop was added.
- Focused verification: `94 passed` for `test_source_contracts.py`, `test_media.py`, `test_uploads.py`, `test_tasks.py`, `test_api.py`, and `test_runtime_assets.py`.
- Full verification: `234 passed`; `python -m compileall -q src`, `python -m pip check`, and `git diff --check` passed. Pytest used separate `D:\Workspace\Codex\cache\VtNote-runtime` base-temp paths to avoid Windows SQLite-handle collisions between concurrently launched validation commands.
- Self-review: multipart code does not use `request.form()`, `UploadFile`, or system temporary files; process execution uses direct argv with `shell=False`; upload source locators are opaque asset IDs; no trusted local original is copied, moved, or deleted.

## Lifecycle review fix evidence (2026-07-24)

- Root cause: `_run_conversion()` promoted staging to the canonical path and registered an active role before `convert_for_cloud()` or `prepare_for_local()` performed its final media contract check. A successful FFmpeg call with an invalid result therefore poisoned role-based retry reuse.
- RED: three new focused regressions failed as expected. A Matroska/Opus result was accepted as cloud output, an 8 kHz local PCM result remained at the canonical path, and a zero-byte failed staging file leaked without a cleanup event.
- GREEN: output validators now run on staging before any promotion/registration. Cloud validation includes the OGG container token, Opus, mono, and 16 kHz OpusHead input-rate contract; local validation includes PCM s16le, 16 kHz, and mono. Invalid non-empty staging becomes recoverable `failed_media` trash and valid retries succeed.
- Zero-byte review: Task 3B previously had exact audited cleanup only for upload incoming paths. `RuntimeAssetService.discard_empty_conversion_staging()` now applies the same constrained pattern to an exact item UUID, staging UUID, and audio extension; it exposes no arbitrary-path delete API.
- Idempotency evidence covers valid active reuse, trash restore, and a canonical file surviving before database registration. Focused verification passed `98 passed`; full verification passed `238 passed`; D-drive `compileall`, `pip check`, and `git diff --check` passed.
- Compatibility boundary: validators also reject an invalid already-active/restored canonical asset, but this fix intentionally does not migrate or delete an invalid active row created by the prior `e6207f9` draft. Task 3B is pre-release; earlier schema work recorded that `VtNote-data\vtnote.db` did not exist, and this fix did not inspect or touch `D:\Workspace\Project\VtNote-data`. Supporting arbitrary-path deletion or in-place role mutation would exceed this lifecycle fix.
- Scope/self-review: only media conversion validation/lifecycle code, its tests, and required logs changed. No platform adapter, ASR, worker loop, trusted local original, or `D:\Workspace\Project\VtNote-data` content was touched.

## Completion contract

- Read `.superpowers/sdd/task-3b-brief.md` first; it is the binding task scope.
- Complete the three RED cases without weakening/removing their assertions.
- Review the entire uncommitted Task 3B diff for spec compliance, especially:
  no `UploadFile`/`request.form()`/system temp, no shell, opaque upload locators,
  recoverable partial uploads, immutable user originals, and no platform network/ASR.
- Run the focused command above, then full pytest, compileall, pip check, and
  `git diff --check`.
- Update `docs/implementation-log.md`.
- Commit exactly one focused Task 3B implementation commit from base `95b9bc4`.
- Do not push and do not touch `D:\Workspace\Project\VtNote-data`.
- Append final test/commit/self-review evidence below before reporting DONE.
