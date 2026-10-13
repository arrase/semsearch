"""Linux power supply detection and operating hours scheduler."""

from datetime import datetime
from pathlib import Path
import threading

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
        self._thread: threading.Thread | None = None
        self._paused_by_scheduler = False

    def is_on_battery(self) -> bool:
        """Inspect /sys/class/power_supply/ to check if battery is discharging."""
        if not self.sysfs_path.is_dir():
            return False

        for dev_path in self.sysfs_path.iterdir():
            type_file = dev_path / "type"
            status_file = dev_path / "status"
            if type_file.is_file() and type_file.read_text(encoding="utf-8").strip() == "Battery":
                if status_file.is_file():
                    status = status_file.read_text(encoding="utf-8").strip()
                    if status == "Discharging":
                        return True
        return False

    def is_within_schedule(self, now_minutes: int | None = None) -> bool:
        """Check if current time is within configured active hours, supporting overnight ranges."""
        if not self.config.index_schedule_enabled:
            return True

        if now_minutes is None:
            now = datetime.now()
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
        """Evaluate policy once and update IndexService pause state."""
        allowed, reason = self.evaluate_policy()
        if not allowed and not self._paused_by_scheduler:
            self.index_service.pause(reason=reason)
            self._paused_by_scheduler = True
        elif allowed and self._paused_by_scheduler:
            self.index_service.resume()
            self._paused_by_scheduler = False

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self.check_and_update()
            self._stop_event.wait(self.poll_interval_sec)

    def start(self) -> None:
        """Start the background monitoring thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="PowerSchedulerThread", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the background monitoring thread."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
