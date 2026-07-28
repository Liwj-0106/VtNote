"""Purpose-bound protection for the sole supported sensitive text field."""

from __future__ import annotations

import base64
import binascii
import ctypes
import os
import sys
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from vtnote.models import (
    DefaultSettingsRecord,
    SensitiveTextMigrationRecord,
    TaskRecord,
)


DEFAULT_PROMPT_PURPOSE = "defaults:notes_custom_prompt"
MIGRATION_COMPLETE = "complete"
MIGRATION_REQUIRED = "sensitive_snapshot_migration_required"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class SensitiveTextProtectionError(RuntimeError):
    """A safe error that never contains native details or protected content."""


class SensitiveTextMigrationRequired(RuntimeError):
    pass


class ProtectedTextEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    protection: Literal["windows_dpapi_current_user"] = (
        "windows_dpapi_current_user"
    )
    ciphertext_b64: str = Field(min_length=1)


class SensitiveTextProtector(Protocol):
    def protect(self, purpose: str, plaintext: str) -> ProtectedTextEnvelope: ...

    def unprotect(
        self, purpose: str, envelope: ProtectedTextEnvelope
    ) -> str: ...


def task_prompt_purpose(task_id: str) -> str:
    return f"task:{task_id}:notes_custom_prompt"


def _purpose_bytes(purpose: str) -> bytes:
    if not isinstance(purpose, str) or not purpose or len(purpose) > 256:
        raise SensitiveTextProtectionError("sensitive text purpose is invalid")
    return purpose.encode("utf-8")


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    return (
        _DataBlob(
            len(data),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
        ),
        buffer,
    )


class WindowsDpapiSensitiveTextProtector:
    """Windows current-user DPAPI with UI disabled and purpose entropy."""

    @staticmethod
    def _libraries() -> tuple[object, object]:
        if sys.platform != "win32":
            raise SensitiveTextProtectionError(
                "sensitive text protection is unavailable"
            )
        try:
            crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            crypt32.CryptProtectData.argtypes = [
                ctypes.POINTER(_DataBlob),
                ctypes.c_wchar_p,
                ctypes.POINTER(_DataBlob),
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.POINTER(_DataBlob),
            ]
            crypt32.CryptProtectData.restype = ctypes.c_int
            crypt32.CryptUnprotectData.argtypes = [
                ctypes.POINTER(_DataBlob),
                ctypes.POINTER(ctypes.c_wchar_p),
                ctypes.POINTER(_DataBlob),
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.POINTER(_DataBlob),
            ]
            crypt32.CryptUnprotectData.restype = ctypes.c_int
            kernel32.LocalFree.argtypes = [ctypes.c_void_p]
            kernel32.LocalFree.restype = ctypes.c_void_p
        except Exception:
            raise SensitiveTextProtectionError(
                "sensitive text protection is unavailable"
            ) from None
        return crypt32, kernel32

    def protect(self, purpose: str, plaintext: str) -> ProtectedTextEnvelope:
        if not isinstance(plaintext, str):
            raise SensitiveTextProtectionError("sensitive text is invalid")
        crypt32, kernel32 = self._libraries()
        input_blob, input_buffer = _blob(plaintext.encode("utf-8"))
        entropy_blob, entropy_buffer = _blob(_purpose_bytes(purpose))
        output_blob = _DataBlob()
        try:
            ok = crypt32.CryptProtectData(
                ctypes.byref(input_blob),
                None,
                ctypes.byref(entropy_blob),
                None,
                None,
                _CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(output_blob),
            )
            if not ok:
                raise SensitiveTextProtectionError(
                    "sensitive text protection failed"
                )
            ciphertext = ctypes.string_at(
                output_blob.pbData, output_blob.cbData
            )
            return ProtectedTextEnvelope(
                ciphertext_b64=base64.b64encode(ciphertext).decode("ascii")
            )
        except SensitiveTextProtectionError:
            raise
        except Exception:
            raise SensitiveTextProtectionError(
                "sensitive text protection failed"
            ) from None
        finally:
            _ = input_buffer, entropy_buffer
            if output_blob.pbData:
                kernel32.LocalFree(
                    ctypes.cast(output_blob.pbData, ctypes.c_void_p)
                )

    def unprotect(
        self, purpose: str, envelope: ProtectedTextEnvelope
    ) -> str:
        validated = ProtectedTextEnvelope.model_validate(envelope)
        try:
            ciphertext = base64.b64decode(
                validated.ciphertext_b64, validate=True
            )
        except (ValueError, binascii.Error):
            raise SensitiveTextProtectionError(
                "protected sensitive text is invalid"
            ) from None
        crypt32, kernel32 = self._libraries()
        input_blob, input_buffer = _blob(ciphertext)
        entropy_blob, entropy_buffer = _blob(_purpose_bytes(purpose))
        output_blob = _DataBlob()
        try:
            ok = crypt32.CryptUnprotectData(
                ctypes.byref(input_blob),
                None,
                ctypes.byref(entropy_blob),
                None,
                None,
                _CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(output_blob),
            )
            if not ok:
                raise SensitiveTextProtectionError(
                    "sensitive text unprotection failed"
                )
            try:
                return ctypes.string_at(
                    output_blob.pbData, output_blob.cbData
                ).decode("utf-8")
            except UnicodeDecodeError:
                raise SensitiveTextProtectionError(
                    "protected sensitive text is invalid"
                ) from None
        except SensitiveTextProtectionError:
            raise
        except Exception:
            raise SensitiveTextProtectionError(
                "sensitive text unprotection failed"
            ) from None
        finally:
            _ = input_buffer, entropy_buffer
            if output_blob.pbData:
                kernel32.LocalFree(
                    ctypes.cast(output_blob.pbData, ctypes.c_void_p)
                )


class MemorySensitiveTextProtector:
    """Opaque purpose-bound fake for tests; tokens never encode plaintext."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}

    def protect(self, purpose: str, plaintext: str) -> ProtectedTextEnvelope:
        _purpose_bytes(purpose)
        if not isinstance(plaintext, str):
            raise SensitiveTextProtectionError("sensitive text is invalid")
        token = base64.b64encode(os.urandom(32)).decode("ascii")
        self._values[(purpose, token)] = plaintext
        return ProtectedTextEnvelope(ciphertext_b64=token)

    def unprotect(
        self, purpose: str, envelope: ProtectedTextEnvelope
    ) -> str:
        _purpose_bytes(purpose)
        validated = ProtectedTextEnvelope.model_validate(envelope)
        try:
            return self._values[(purpose, validated.ciphertext_b64)]
        except KeyError:
            raise SensitiveTextProtectionError(
                "sensitive text unprotection failed"
            ) from None


def sensitive_text_migration_status(session: Session) -> str:
    row = session.get(SensitiveTextMigrationRecord, 1)
    return row.status if row is not None else MIGRATION_COMPLETE


def require_sensitive_text_migration(session: Session) -> None:
    if sensitive_text_migration_status(session) != MIGRATION_COMPLETE:
        raise SensitiveTextMigrationRequired(MIGRATION_REQUIRED)


def _protect_legacy_rows(
    session: Session, protector: SensitiveTextProtector
) -> None:
    defaults = session.scalars(select(DefaultSettingsRecord)).all()
    for row in defaults:
        if (
            row.notes_custom_prompt
            and row.notes_custom_prompt_envelope_json is None
        ):
            row.notes_custom_prompt_envelope_json = protector.protect(
                DEFAULT_PROMPT_PURPOSE, row.notes_custom_prompt
            ).model_dump(mode="json")
        row.notes_custom_prompt = None

    tasks = session.scalars(select(TaskRecord)).all()
    for row in tasks:
        options = dict(row.options)
        option_prompt = options.pop("notes_custom_prompt", None)
        snapshot = dict(row.pipeline_snapshot_json)
        notes_value = snapshot.get("notes")
        notes = dict(notes_value) if isinstance(notes_value, dict) else None
        if notes is not None:
            snapshot_prompt = notes.pop("custom_prompt", None)
            prompt = snapshot_prompt if isinstance(snapshot_prompt, str) else option_prompt
            if prompt and notes.get("custom_prompt_envelope") is None:
                notes["custom_prompt_envelope"] = protector.protect(
                    task_prompt_purpose(row.id), prompt
                ).model_dump(mode="json")
            snapshot["notes"] = notes
        row.options = options
        row.pipeline_snapshot_json = snapshot


def migrate_sensitive_text(
    engine: Engine,
    protector: SensitiveTextProtector | None = None,
) -> str:
    """Atomically protect all legacy prompt rows and publish only safe state."""

    selected = protector or WindowsDpapiSensitiveTextProtector()
    session = Session(engine)
    try:
        _protect_legacy_rows(session, selected)
        state = session.get(SensitiveTextMigrationRecord, 1)
        if state is None:
            state = SensitiveTextMigrationRecord(id=1, status=MIGRATION_COMPLETE)
            session.add(state)
        else:
            state.status = MIGRATION_COMPLETE
        session.commit()
        return MIGRATION_COMPLETE
    except Exception:
        session.rollback()
    finally:
        session.close()

    with Session(engine) as failure_session:
        state = failure_session.get(SensitiveTextMigrationRecord, 1)
        if state is None:
            failure_session.add(
                SensitiveTextMigrationRecord(id=1, status=MIGRATION_REQUIRED)
            )
        else:
            state.status = MIGRATION_REQUIRED
        failure_session.commit()
    return MIGRATION_REQUIRED
