# core-storage Specification

## ADDED Requirements

### Requirement: Idle SQLite Memory Release
When the application transitions to an idle state in the system tray or closes active search views, the storage engine MUST release non-essential SQLite heap and page cache allocations via `shrink_memory()` (`PRAGMA shrink_memory;`) under the database reentrant lock so that idle connections do not retain unnecessary memory.

#### Scenario: Shrinking database memory on transition to idle
- **WHEN** `shrink_memory()` is invoked on an open `Database` connection after indexing or search queries complete
- **THEN** the database executes `PRAGMA shrink_memory;` synchronized by `_lock` to free unused SQLite cache and heap pages without closing the connection or invalidating stored records
