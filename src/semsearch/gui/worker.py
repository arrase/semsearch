"""Background worker thread for asynchronous search queries."""

import threading

from PySide6.QtCore import QThread, Signal

from semsearch.services.searcher import SearchService


class SearchWorker(QThread):
    """Worker thread executing search service query off the main Qt UI thread."""

    results_ready = Signal(str, object)  # query, ResultSet
    _search_lock = threading.Lock()

    def __init__(
        self,
        search_service: SearchService,
        query: str,
        limit: int = 100,
        is_code: bool = False,
    ) -> None:
        super().__init__()
        self.search_service = search_service
        self.query = query
        self.limit = limit
        self.is_code = is_code
        self._stopped = False

    def stop(self) -> None:
        """Signal the worker thread to stop execution."""
        self._stopped = True
        self.requestInterruption()

    @property
    def is_stopped(self) -> bool:
        """Check if worker was requested to stop."""
        return self._stopped or self.isInterruptionRequested()

    def run(self) -> None:
        if self.is_stopped:
            return
        with self._search_lock:
            if self.is_stopped:
                return
            results = self.search_service.search(self.query, limit=self.limit, is_code=self.is_code)
        if not self.is_stopped:
            self.results_ready.emit(self.query, results)
