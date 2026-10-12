"""Tests for PowerScheduler, Linux power supply detection, and operating hours scheduling."""

from pathlib import Path
import pytest

from semsearch.config import AppConfig
from semsearch.services.scheduler import PowerScheduler


class DummyIndexService:
    def __init__(self):
        self.paused = False
        self.pause_reason = ""

    def pause(self, reason: str = ""):
        self.paused = True
        self.pause_reason = reason

    def resume(self):
        self.paused = False
        self.pause_reason = ""


def test_battery_detection_sysfs(tmp_path: Path):
    sysfs = tmp_path / "power_supply"
    sysfs.mkdir()

    bat0 = sysfs / "BAT0"
    bat0.mkdir()
    (bat0 / "type").write_text("Battery\n", encoding="utf-8")
    (bat0 / "status").write_text("Discharging\n", encoding="utf-8")

    dummy_indexer = DummyIndexService()
    config = AppConfig()
    scheduler = PowerScheduler(dummy_indexer, config, sysfs_path=sysfs)

    assert scheduler.is_on_battery() is True

    # When battery is charging
    (bat0 / "status").write_text("Charging\n", encoding="utf-8")
    assert scheduler.is_on_battery() is False

    # When battery is Full
    (bat0 / "status").write_text("Full\n", encoding="utf-8")
    assert scheduler.is_on_battery() is False


def test_battery_detection_no_battery(tmp_path: Path):
    sysfs = tmp_path / "power_supply"
    sysfs.mkdir()

    ac0 = sysfs / "AC"
    ac0.mkdir()
    (ac0 / "type").write_text("Mains\n", encoding="utf-8")

    dummy_indexer = DummyIndexService()
    config = AppConfig()
    scheduler = PowerScheduler(dummy_indexer, config, sysfs_path=sysfs)

    assert scheduler.is_on_battery() is False


def test_operating_hours_daytime_schedule():
    dummy_indexer = DummyIndexService()
    config = AppConfig(
        index_schedule_enabled=True,
        index_schedule_start=540,  # 09:00
        index_schedule_end=1080,   # 18:00
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


def test_operating_hours_overnight_schedule():
    dummy_indexer = DummyIndexService()
    config = AppConfig(
        index_schedule_enabled=True,
        index_schedule_start=1320,  # 22:00
        index_schedule_end=420,     # 07:00
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


def test_evaluate_policy_and_update(tmp_path: Path):
    sysfs = tmp_path / "power_supply"
    sysfs.mkdir()
    bat0 = sysfs / "BAT0"
    bat0.mkdir()
    (bat0 / "type").write_text("Battery\n", encoding="utf-8")
    (bat0 / "status").write_text("Discharging\n", encoding="utf-8")

    dummy_indexer = DummyIndexService()
    config = AppConfig(
        index_on_battery=False,
        index_schedule_enabled=False,
    )
    scheduler = PowerScheduler(dummy_indexer, config, sysfs_path=sysfs)

    allowed, reason = scheduler.evaluate_policy()
    assert allowed is False
    assert reason == "AC power"

    scheduler.check_and_update()
    assert dummy_indexer.paused is True
    assert dummy_indexer.pause_reason == "AC power"

    # Now plug in AC
    (bat0 / "status").write_text("Charging\n", encoding="utf-8")
    allowed, reason = scheduler.evaluate_policy()
    assert allowed is True
    assert reason == ""

    scheduler.check_and_update()
    assert dummy_indexer.paused is False
