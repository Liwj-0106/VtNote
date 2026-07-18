"""SQLAlchemy 2 persistence models shared by the API and durable worker."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean, CheckConstraint, JSON, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator[datetime]):
    """Store UTC-naive in SQLite and always return aware UTC datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    pass


class TaskRecord(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    options: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False, default=dict
    )
    pipeline_snapshot_json: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    items: Mapped[list[ItemRecord]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by=lambda: ItemRecord.position,
    )


class ItemRecord(Base):
    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("task_id", "position", name="uq_items_task_position"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_locator: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    title: Mapped[str | None] = mapped_column(Text)
    artifact_relpath: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    task: Mapped[TaskRecord] = relationship(back_populates="items")
    stage_runs: Mapped[list[StageRunRecord]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by=lambda: (StageRunRecord.stage, StageRunRecord.attempt),
    )


class StageRunRecord(Base):
    __tablename__ = "stage_runs"
    __table_args__ = (
        UniqueConstraint("item_id", "stage", "attempt", name="uq_stage_runs_item_stage_attempt"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    warning: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    item: Mapped[ItemRecord] = relationship(back_populates="stage_runs")


class ProviderConnectionRecord(Base):
    __tablename__ = "provider_connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(
        String(128, collation="NOCASE"), nullable=False, unique=True
    )
    protocol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False, default=dict
    )
    credential_ref: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    test_ok: Mapped[bool | None] = mapped_column(Boolean)
    tested_revision: Mapped[int | None] = mapped_column(Integer)
    test_message: Mapped[str | None] = mapped_column(Text)
    tested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    profiles: Mapped[list[ProcessorProfileRecord]] = relationship(
        back_populates="connection", cascade="all, delete-orphan", passive_deletes=True
    )


class ProcessorProfileRecord(Base):
    __tablename__ = "processor_profiles"
    __table_args__ = (CheckConstraint("context_length > 0", name="ck_profile_context_length"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(
        String(128, collation="NOCASE"), nullable=False, unique=True
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("provider_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    context_length: Mapped[int] = mapped_column(Integer, nullable=False, default=8192)
    options: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False, default=dict
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    test_ok: Mapped[bool | None] = mapped_column(Boolean)
    tested_revision: Mapped[int | None] = mapped_column(Integer)
    tested_connection_revision: Mapped[int | None] = mapped_column(Integer)
    test_message: Mapped[str | None] = mapped_column(Text)
    tested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    upload_authorized_revision: Mapped[int | None] = mapped_column(Integer)
    upload_authorized_connection_revision: Mapped[int | None] = mapped_column(Integer)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    connection: Mapped[ProviderConnectionRecord] = relationship(back_populates="profiles")


class DefaultSettingsRecord(Base):
    __tablename__ = "default_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    asr_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="auto")
    cloud_asr_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("processor_profiles.id", ondelete="SET NULL")
    )
    translation_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    translation_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("processor_profiles.id", ondelete="SET NULL")
    )
    translation_target_language: Mapped[str] = mapped_column(
        String(64), nullable=False, default="zh-Hans"
    )
    notes_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("processor_profiles.id", ondelete="SET NULL")
    )
    notes_template: Mapped[str] = mapped_column(String(32), nullable=False, default="summary")
    notes_output_language: Mapped[str] = mapped_column(
        String(64), nullable=False, default="zh-Hans"
    )
    notes_custom_prompt: Mapped[str | None] = mapped_column(Text)
    notes_auto_enable_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    local_whisper_options: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON),
        nullable=False,
        default=lambda: {
            "model": "large-v3-turbo",
            "device": "auto",
            "compute_type": "int8_float16",
            "vad_filter": True,
            "model_root": r"D:\Workspace\Project\VtNote-data\models\faster-whisper",
            "cache_root": r"D:\Workspace\Codex\cache\VtNote-runtime\models\faster-whisper",
        },
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=_utcnow, onupdate=_utcnow, nullable=False
    )
