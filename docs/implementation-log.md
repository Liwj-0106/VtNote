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
