"""Linux power supply detection and operating hours scheduler."""

import threading
from datetime import UTC, datetime
from pathlib import Path

from semsearch.config import AppConfig
from semsearch.services.indexer import IndexService

DEFAULT_SYSFS_POWER_PATH = Path("/sys/class/power_supply")


class PowerScheduler:
    """Monitors system power status and schedules indexing according to policy."""

    def __init__(
        self,
        index_service: IndexService,
        config: AppConfig,
        poll_interval_sec: float = 10.0,
        sysfs_path: Path = DEFAULT_SYSFS_POWER_PATH,
    ) -> None:
        self.index_service = index_service
        self.config = config
        self.poll_interval_sec = poll_interval_sec
        self.sysfs_path = sysfs_path

        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._paused_by_scheduler = False
        self._last_policy_reason = ""

    @staticmethod
    def _read_sysfs_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8").strip() if path.is_file() else ""
        except OSError:
            return ""

    def is_on_battery(self) -> bool:
        """Inspect /sys/class/power_supply/ to check if a system battery is discharging."""
        try:
            if not self.sysfs_path.is_dir():
                return False
            devices = list(self.sysfs_path.iterdir())
        except OSError:
            return False

        for dev_path in devices:
            if self._read_sysfs_text(dev_path / "scope") == "Device":
                continue
            if (
                self._read_sysfs_text(dev_path / "type") == "Battery"
                and self._read_sysfs_text(dev_path / "status") == "Discharging"
            ):
                return True
        return False

    def is_within_schedule(self, now_minutes: int | None = None) -> bool:
        """Check if current time is within configured active hours, supporting overnight ranges."""
        if not self.config.index_schedule_enabled:
            return True

        if now_minutes is None:
            now = datetime.now(tz=UTC).astimezone()
            now_minutes = now.hour * 60 + now.minute

        start = self.config.index_schedule_start
        end = self.config.index_schedule_end

        if start > end:
            return now_minutes >= start or now_minutes < end
        return start <= now_minutes < end

    def evaluate_policy(self, now_minutes: int | None = None) -> tuple[bool, str]:
        """Evaluate battery and schedule rules, returning (allowed, wait_reason)."""
        if not self.config.index_on_battery and self.is_on_battery():
            return False, "AC power"

        if not self.is_within_schedule(now_minutes):
            return False, "Scheduled hours"

        return True, ""

    def check_and_update(self) -> None:
        """Evaluate policy once and update IndexService policy pause state."""
        allowed, reason = self.evaluate_policy()
        if not allowed:
            if not self._paused_by_scheduler or reason != self._last_policy_reason:
                self.index_service.pause(reason=reason, by_policy=True)
                self._paused_by_scheduler = True
                self._last_policy_reason = reason
        elif self._paused_by_scheduler:
            self.index_service.resume(by_policy=True)
            self._paused_by_scheduler = False
            self._last_policy_reason = ""

    def wake(self) -> None:
        """Wake scheduler thread immediately to re-evaluate updated power or schedule policies."""
        self._wake_event.set()

    def _has_active_policies(self) -> bool:
        return (not self.config.index_on_battery) or self.config.index_schedule_enabled

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self._wake_event.clear()
            self.check_and_update()
            if self._stop_event.is_set():
                break
            if not self._has_active_policies():
                self._wake_event.wait()
            else:
                self._wake_event.wait(self.poll_interval_sec)

    def start(self) -> None:
        """Start the background monitoring thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="PowerSchedulerThread", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the background monitoring thread."""
        self._stop_event.set()
        self._wake_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
