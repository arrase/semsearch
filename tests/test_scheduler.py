"""Tests for PowerScheduler, Linux power supply detection, policy pause separation, and idle blocking."""

import time
from pathlib import Path

import pytest

from semsearch.config import AppConfig
from semsearch.core.database import Database
from semsearch.services.indexer import IndexService
from semsearch.services.scheduler import PowerScheduler


class RecordingIndexService(IndexService):
    def __init__(self) -> None:
        super().__init__(db=Database(":memory:"))
        self.paused = False
        self.pause_reason = ""
        self.last_by_policy: bool | None = None

    def pause(self, reason: str = "", *, by_policy: bool = False) -> None:
        self.paused = True
        self.pause_reason = reason
        self.last_by_policy = by_policy
        super().pause(reason=reason, by_policy=by_policy)

    def resume(self, *, by_policy: bool = False) -> None:
        self.last_by_policy = by_policy
        super().resume(by_policy=by_policy)
        self.paused = self.status.paused
        self.pause_reason = self.status.waiting_for


def test_battery_detection_sysfs(tmp_path: Path) -> None:
    sysfs = tmp_path / "power_supply"
    sysfs.mkdir()

    bat0 = sysfs / "BAT0"
    bat0.mkdir()
    (bat0 / "type").write_text("Battery\n", encoding="utf-8")
    (bat0 / "status").write_text("Discharging\n", encoding="utf-8")

    dummy_indexer = RecordingIndexService()
    config = AppConfig()
    scheduler = PowerScheduler(dummy_indexer, config, sysfs_path=sysfs)

    assert scheduler.is_on_battery() is True

    # When battery is charging
    (bat0 / "status").write_text("Charging\n", encoding="utf-8")
    assert scheduler.is_on_battery() is False

    # When battery is Full
    (bat0 / "status").write_text("Full\n", encoding="utf-8")
    assert scheduler.is_on_battery() is False


def test_battery_detection_ignores_device_scope_and_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify peripheral Device-scoped batteries (e.g. wireless mice) and sysfs OSErrors do not trigger battery mode."""
    sysfs = tmp_path / "power_supply"
    sysfs.mkdir()

    mouse_bat = sysfs / "hidpp_battery_0"
    mouse_bat.mkdir()
    (mouse_bat / "scope").write_text("Device\n", encoding="utf-8")
    (mouse_bat / "type").write_text("Battery\n", encoding="utf-8")
    (mouse_bat / "status").write_text("Discharging\n", encoding="utf-8")

    scheduler = PowerScheduler(RecordingIndexService(), AppConfig(), sysfs_path=sysfs)
    assert scheduler.is_on_battery() is False

    # System-scoped battery still triggers battery detection
    sys_bat = sysfs / "BAT0"
    sys_bat.mkdir()
    (sys_bat / "scope").write_text("System\n", encoding="utf-8")
    (sys_bat / "type").write_text("Battery\n", encoding="utf-8")
    (sys_bat / "status").write_text("Discharging\n", encoding="utf-8")
    assert scheduler.is_on_battery() is True

    # Transient sysfs OSError returns False safely
    def failing_read_text(_self: Path, **_kwargs: object) -> str:
        raise OSError("device vanished")

    monkeypatch.setattr(Path, "read_text", failing_read_text)
    assert scheduler.is_on_battery() is False


def test_battery_detection_no_battery(tmp_path: Path) -> None:
    sysfs = tmp_path / "power_supply"
    sysfs.mkdir()

    ac0 = sysfs / "AC"
    ac0.mkdir()
    (ac0 / "type").write_text("Mains\n", encoding="utf-8")

    dummy_indexer = RecordingIndexService()
    config = AppConfig()
    scheduler = PowerScheduler(dummy_indexer, config, sysfs_path=sysfs)

    assert scheduler.is_on_battery() is False


def test_operating_hours_daytime_schedule() -> None:
    dummy_indexer = RecordingIndexService()
    config = AppConfig(
        index_schedule_enabled=True,
        index_schedule_start=540,  # 09:00
        index_schedule_end=1080,  # 18:00
    )
    scheduler = PowerScheduler(dummy_indexer, config)

    # 12:00 (720 min) -> inside
    assert scheduler.is_within_schedule(720) is True
    # 08:30 (510 min) -> outside
    assert scheduler.is_within_schedule(510) is False
    # 18:00 (1080 min) -> outside (half-open interval)
    assert scheduler.is_within_schedule(1080) is False
    # 18:30 (1110 min) -> outside
    assert scheduler.is_within_schedule(1110) is False


def test_operating_hours_overnight_schedule() -> None:
    dummy_indexer = RecordingIndexService()
    config = AppConfig(
        index_schedule_enabled=True,
        index_schedule_start=1320,  # 22:00
        index_schedule_end=420,  # 07:00
    )
    scheduler = PowerScheduler(dummy_indexer, config)

    # 23:30 (1410 min) -> inside
    assert scheduler.is_within_schedule(1410) is True
    # 03:00 (180 min) -> inside
    assert scheduler.is_within_schedule(180) is True
    # 22:00 (1320 min) -> inside
    assert scheduler.is_within_schedule(1320) is True
    # 07:00 (420 min) -> outside
    assert scheduler.is_within_schedule(420) is False
    # 14:00 (840 min) -> outside
    assert scheduler.is_within_schedule(840) is False


def test_evaluate_policy_and_update(tmp_path: Path) -> None:
    sysfs = tmp_path / "power_supply"
    sysfs.mkdir()
    bat0 = sysfs / "BAT0"
    bat0.mkdir()
    (bat0 / "type").write_text("Battery\n", encoding="utf-8")
    (bat0 / "status").write_text("Discharging\n", encoding="utf-8")

    dummy_indexer = RecordingIndexService()
    config = AppConfig(
        index_on_battery=False,
        index_schedule_enabled=False,
    )
    scheduler = PowerScheduler(dummy_indexer, config, sysfs_path=sysfs)

    allowed, reason = scheduler.evaluate_policy()
    assert allowed is False
    assert reason == "AC power"

    scheduler.check_and_update()
    assert dummy_indexer.get_status().paused is True
    assert dummy_indexer.pause_reason == "AC power"
    assert dummy_indexer.last_by_policy is True

    # Now plug in AC
    (bat0 / "status").write_text("Charging\n", encoding="utf-8")
    allowed, reason = scheduler.evaluate_policy()
    assert allowed is True
    assert reason == ""

    scheduler.check_and_update()
    assert dummy_indexer.get_status().paused is False
    assert dummy_indexer.last_by_policy is True


def test_manual_pause_and_policy_pause_are_independent(tmp_path: Path) -> None:
    """Verify PowerScheduler resume does not clear a manual user pause and vice versa."""
    sysfs = tmp_path / "power_supply"
    sysfs.mkdir()
    bat0 = sysfs / "BAT0"
    bat0.mkdir()
    (bat0 / "type").write_text("Battery\n", encoding="utf-8")
    (bat0 / "status").write_text("Discharging\n", encoding="utf-8")

    indexer = IndexService(db=Database(":memory:"))
    config = AppConfig(index_on_battery=False, index_schedule_enabled=False)
    scheduler = PowerScheduler(indexer, config, sysfs_path=sysfs)

    # 1. User manually pauses, then policy pauses, then AC power returns -> still paused for user
    indexer.pause(reason="User")
    scheduler.check_and_update()
    assert indexer.get_status().paused is True
    assert not indexer._pause_event.is_set()

    (bat0 / "status").write_text("Charging\n", encoding="utf-8")
    scheduler.check_and_update()
    assert indexer.get_status().paused is True
    assert indexer.get_status().waiting_for == "User"
    assert not indexer._pause_event.is_set()

    indexer.resume()
    assert indexer.get_status().paused is False
    assert indexer._pause_event.is_set()

    # 2. Policy pauses, then user pauses and resumes -> still paused for policy until AC returns
    (bat0 / "status").write_text("Discharging\n", encoding="utf-8")
    scheduler.check_and_update()
    indexer.pause(reason="User")
    indexer.resume()
    assert indexer.get_status().paused is True
    assert indexer.get_status().waiting_for == "AC power"
    assert not indexer._pause_event.is_set()

    (bat0 / "status").write_text("Charging\n", encoding="utf-8")
    scheduler.check_and_update()
    assert indexer.get_status().paused is False
    assert indexer._pause_event.is_set()


def test_scheduler_blocks_without_polling_when_policies_inactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify scheduler sleeps on _wake_event with zero polling when both policies are disabled."""
    sysfs = tmp_path / "power_supply"
    sysfs.mkdir()
    bat0 = sysfs / "BAT0"
    bat0.mkdir()
    (bat0 / "type").write_text("Battery\n", encoding="utf-8")
    (bat0 / "status").write_text("Discharging\n", encoding="utf-8")

    indexer = RecordingIndexService()
    config = AppConfig(index_on_battery=True, index_schedule_enabled=False)
    scheduler = PowerScheduler(indexer, config, poll_interval_sec=0.01, sysfs_path=sysfs)

    check_calls = 0
    orig_check = scheduler.check_and_update

    def counting_check() -> None:
        nonlocal check_calls
        check_calls += 1
        orig_check()

    monkeypatch.setattr(scheduler, "check_and_update", counting_check)

    scheduler.start()
    try:
        time.sleep(0.08)
        assert check_calls == 1
        assert indexer.get_status().paused is False

        # Enable battery restriction and wake scheduler -> immediate evaluation pauses indexing
        config.index_on_battery = False
        scheduler.wake()

        deadline = time.time() + 1.0
        while not indexer.get_status().paused and time.time() < deadline:
            time.sleep(0.01)

        assert indexer.get_status().paused is True
        assert check_calls >= 2
    finally:
        scheduler.stop()
