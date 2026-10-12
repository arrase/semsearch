"""SemSearch background and backend services."""

from semsearch.services.actions import (
    copy_file_to_clipboard,
    copy_to_clipboard,
    execute_hit_action,
    open_code_at,
    open_code_editor,
    open_file,
    open_file_default,
    open_media_at,
    open_media_timestamp,
    open_pdf_at,
    open_pdf_page,
    reveal_in_file_manager,
)
from semsearch.services.indexer import (
    IndexQueueTier,
    IndexService,
    MeasuredCosts,
)
from semsearch.services.scheduler import PowerScheduler
from semsearch.services.searcher import (
    Prompts,
    ResultSet,
    SearchService,
)
from semsearch.services.watcher import (
    DebounceBucket,
    FileSystemWatcher,
)

__all__ = [
    "SearchService",
    "ResultSet",
    "Prompts",
    "IndexService",
    "IndexQueueTier",
    "MeasuredCosts",
    "PowerScheduler",
    "FileSystemWatcher",
    "DebounceBucket",
    "open_file",
    "open_file_default",
    "open_media_at",
    "open_media_timestamp",
    "open_pdf_at",
    "open_pdf_page",
    "open_code_at",
    "open_code_editor",
    "reveal_in_file_manager",
    "copy_to_clipboard",
    "copy_file_to_clipboard",
    "execute_hit_action",
]
