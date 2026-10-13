# indexing-service Specification Delta

## ADDED Requirements

### Requirement: Code Repository Root and Relative Title Resolution
When indexing code files, the indexing service MUST resolve the repository root by walking upward from the file to the nearest `.git` directory or code project root marker before falling back to the configured code root folder, and MUST format segment titles with the repository directory name followed by the relative file path (e.g., `semsearch/src/semsearch/services/searcher.py`).

#### Scenario: Nested git repository inside configured parent directory
- **WHEN** a code folder such as `~/Develop` contains multiple repositories (e.g., `~/Develop/semsearch/.git`)
- **THEN** the indexed code segment sets `repo` to `~/Develop/semsearch` and `title` to `semsearch/src/...`
