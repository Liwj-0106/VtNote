from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from vtnote.database import initialize_database
from vtnote.maintenance import MaintenanceLease, MaintenanceService
from vtnote.models import ResourceLeaseRecord


NOW = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)


class _RuntimeAssets:
    def __init__(self) -> None:
        self.calls = 0

    def purge_due(self, *, now: datetime) -> tuple[str, ...]:
        assert now == NOW
        self.calls += 1
        return ("asset-1",)


class _Reconciler:
    def __init__(self) -> None:
        self.calls = 0

    def reconcile_one_due(self, now: datetime):
        assert now == NOW
        self.calls += 1
        return type("Result", (), {"action": "query_scheduled"})()


def test_maintenance_lease_excludes_a_second_owner_and_expires(tmp_path: Path) -> None:
    engine = initialize_database(tmp_path / "data" / "vtnote.db")
    first = MaintenanceLease(engine, owner="first", duration=timedelta(minutes=2))
    second = MaintenanceLease(engine, owner="second", duration=timedelta(minutes=2))

    assert first.acquire(NOW) is True
    assert second.acquire(NOW + timedelta(minutes=1)) is False
    assert second.acquire(NOW + timedelta(minutes=3)) is True
    second.release()
    with Session(engine) as session:
        assert session.get(ResourceLeaseRecord, "maintenance") is None
    engine.dispose()


def test_one_maintenance_pass_purges_local_trash_and_runs_one_external_action(
    tmp_path: Path,
) -> None:
    engine = initialize_database(tmp_path / "data" / "vtnote.db")
    assets = _RuntimeAssets()
    reconciler = _Reconciler()
    service = MaintenanceService(
        lease=MaintenanceLease(
            engine,
            owner="maintenance-worker",
            duration=timedelta(minutes=2),
        ),
        runtime_assets=assets,
        reconciler=reconciler,
    )

    result = service.run_once(NOW)

    assert result.acquired is True
    assert result.purged_asset_ids == ("asset-1",)
    assert result.external_action == "query_scheduled"
    assert assets.calls == 1
    assert reconciler.calls == 1
    engine.dispose()
