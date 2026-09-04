"""Resolve the model identities allowed to produce a stage result."""

from __future__ import annotations

from vtnote.local_asr_contract import (
    LocalAsrSnapshotError,
    resolve_local_asr_snapshot,
)
from vtnote.models import StageRunRecord


def _local_model(snapshot: dict[str, object]) -> str | None:
    try:
        return resolve_local_asr_snapshot(snapshot).model
    except LocalAsrSnapshotError:
        return None


def allowed_stage_models(row: StageRunRecord) -> tuple[str, ...]:
    snapshot = row.item.task.pipeline_snapshot_json
    if not isinstance(snapshot, dict):
        return ()

    models: list[str] = []
    if row.stage == "transcribe":
        override = row.retry_override_json
        if isinstance(override, dict):
            strategy = override.get("strategy")
            override_asr = override.get("asr")
            if strategy == "cloud_confirmed":
                override_profile = (
                    override_asr.get("profile")
                    if isinstance(override_asr, dict)
                    else None
                )
                if (
                    isinstance(override_profile, dict)
                    and isinstance(override_profile.get("model"), str)
                ):
                    return (override_profile["model"],)
                return ()
            if strategy == "local":
                local_model = _local_model(snapshot)
                return (local_model,) if local_model is not None else ()
        local_model = _local_model(snapshot)
        asr = snapshot.get("asr")
        mode = asr.get("mode") if isinstance(asr, dict) else None
        profile = asr.get("profile") if isinstance(asr, dict) else None
        if mode in {"local", "auto"} and isinstance(local_model, str):
            models.append(local_model)
        if mode not in {"cloud", "auto"}:
            profile = None
    elif row.stage in {"translate", "notes"}:
        section = snapshot.get("translation" if row.stage == "translate" else "notes")
        profile = section.get("profile") if isinstance(section, dict) else None
        if row.stage == "notes":
            override = row.retry_override_json
            override_notes = override.get("notes") if isinstance(override, dict) else None
            override_profile = (
                override_notes.get("profile")
                if isinstance(override_notes, dict)
                else None
            )
            if isinstance(override_profile, dict):
                profile = override_profile
    else:
        profile = None
    if isinstance(profile, dict) and isinstance(profile.get("model"), str):
        models.append(profile["model"])
    return tuple(models)
