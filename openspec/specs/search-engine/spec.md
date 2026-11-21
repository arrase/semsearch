# search-engine Specification

## Purpose
Provides dual-index routing, inline filter parsing, instant lexical search, BM25 + coverage + modality-aware Z-score hybrid ranking fusion, symbol/camelCase/CJK text intelligence, quality gating, batch metadata retrieval, deferred snippet extraction, and weaker matches folding.

## Requirements

### Requirement: Inline Filter Parsing and Dual-Index Routing
`parse_query_filters()` MUST parse inline filters anywhere in a query into a `ParsedQuery`: `code:` (routes exclusively to `code.sqlite`), `kind:` (comma-separated aliases `pdf`, `doc`/`docs`, `image`/`img`/`photo`, `screenshot`/`shot`, `audio`, `video`, `code`), `ext:` (comma-separated extensions), `path:` (repeatable case-insensitive path substring supporting quoted values with spaces), and `after:` (relative `Nd`, `Nw`, `Nm` or ISO date `YYYY-MM-DD`). When a query consists solely of inline filters (`clean_query == ""`), `SearchService` MUST skip Ollama `_vector_search()` and return matching files sorted by `mtime DESC`.

#### Scenario: Combining inline filters with semantic and lexical search
- **WHEN** a user searches `kind:pdf ext:pdf path:"tax 2026" after:30d invoice`
- **THEN** `parse_query_filters()` extracts the structured filters, runs hybrid search on `clean_query = "invoice"`, and retains only PDF files under paths containing `tax 2026` modified within the last 30 days

#### Scenario: Pure filter query without Ollama calls
- **WHEN** a user enters `kind:pdf after:7d` with no residual search terms
- **THEN** `SearchService` skips `_vector_search()` and returns matching files ordered by modification time descending

### Requirement: Instant Lexical Search Delegation
`SearchService` MUST expose `lexical_search(query, limit=50, *, is_code=False) -> ResultSet` executing fast SQLite FTS5 search with full inline filter support (`code:`, `kind:`, `ext:`, `path:`, `after:`), batch record/segment fetching (`find_by_ids`, `get_segments_by_ids`), and zero Ollama calls, suitable for per-keystroke panel feedback.

#### Scenario: Instant keystroke lexical search with inline filters
- **WHEN** `SearchService.lexical_search("kind:doc budget", limit=50)` is invoked
- **THEN** deduplicated FTS5 matches filtered by `FileKind.DOC` are returned immediately without invoking `OllamaClient`

### Requirement: Hybrid Ranking Fusion Incorporating BM25 Rank
`SearchService._score_candidates()` MUST compute fused candidate scores as:
`Score = meaning_weight * max(z, 0) + words_weight * (cov**1.5) + bm25_bonus + kind_bonus + name_bonus + def_bonus`,
where `bm25_bonus = (words_weight * 0.5 * (cov**1.5)) / (1.0 + (keyword_rank - 1) / 10.0)` integrates SQLite FTS5 BM25 ranking directly into hybrid fusion, `word_weight = 1.0 if is_code else 2.0`, lookup queries weight `(meaning_weight=0.5, words_weight=word_weight * 2.0)` while standard semantic queries weight `(meaning_weight=1.0, words_weight=word_weight)`, `kind_bonus = 1.0`, `name_bonus = 3.0 * (matched_in_stem / len(folded_terms))**1.5` when `cov >= 0.5`, and `def_bonus` adds `+3.0` (strong) or `+1.5` (weak) for code definitions.

#### Scenario: BM25 density and filename ranking advantage in hybrid fusion
- **WHEN** two candidate files share identical term coverage (`cov = 1.0`) but one ranks higher in FTS5 BM25 (such as a concise document or direct filename match)
- **THEN** the higher-ranked BM25 candidate receives a larger `bm25_bonus` (plus `name_bonus` for filename stem matches) and ranks higher in the final results

### Requirement: Quality Gating at `SEMANTIC_Z_GATE = 2.2` and Weaker Matches Folding
The search engine MUST admit candidates only when `z >= 2.2` (`SEMANTIC_Z_GATE`), `coverage >= 0.5`, or the query is a pure kind-hint or pure inline-filter query. Candidates scoring below `50%` of the top candidate's score MUST be folded into `ResultSet.weaker`, while primary hits (`>= 50%`, up to `limit`) occupy `ResultSet.ranked`.

#### Scenario: Pruning candidates failing semantic and lexical gates
- **WHEN** a candidate has `z < 2.2` and `coverage < 0.5` on a standard query
- **THEN** it is discarded from both `ResultSet.ranked` and `ResultSet.weaker`

#### Scenario: Folding lower-scoring hits into weaker matches
- **WHEN** qualifying results include hits scoring below `0.5 * top_score`
- **THEN** they are partitioned into `ResultSet.weaker` while strong hits remain in `ResultSet.ranked`

### Requirement: Identifier Splitting (`camelCase` <-> `snake_case`), Qualified Symbols, CJK, and Safe FTS5 Escaping
Text normalization (`normalize_search_text`), term extraction (`extract_terms`), and code symbol lookup (`extract_identifier`, `find_definition`) MUST:
- Split `camelCase`/`PascalCase` boundaries so `OllamaClient` and `ollama_client` match interchangeably.
- Support qualified symbols (`Class.method`, `mod::fn`, `obj->prop`, `mod#sym`) by matching exact definitions first and falling back to the leaf symbol definition, as well as JS/TS `const`/`let`/`var`/`val` arrow and function assignments.
- Tokenize and expand CJK characters (Han ideographs, Hiragana, Katakana, Hangul) into unigrams/bigrams and support single-character CJK queries without requiring ASCII word boundaries.
- Fold extended Latin characters (`ł/ø/ß/đ/æ/œ`) and NFD combining marks, filter English/Spanish/Bengali/Arabic stopwords (including `code`/`codigo`/`código`) and kind hints, and escape double quotes (`"` -> `""`) in all FTS5 query patterns without silent `sqlite3.Error` fallbacks.

#### Scenario: Cross-matching camelCase and snake_case identifiers and qualified symbols
- **WHEN** a user queries `code: OllamaClient` against `ollama_client` or `code: SearchService.lexical_search` against `def lexical_search(...)`
- **THEN** `extract_identifier()` emits subword tokens `["ollama", "client"]` and `find_definition()` locates the leaf symbol definition line

#### Scenario: CJK search and double-quote FTS5 safety
- **WHEN** a user searches CJK text or enters a query containing literal double quotes `"`
- **THEN** CJK runs expand and match via unigrams/bigrams, and quotes are doubled cleanly in FTS5 patterns without raising SQLite syntax errors

### Requirement: Batch Retrieval and Deferred Snippet Extraction
`SearchService` MUST eliminate N+1 database queries by batch-fetching candidate `FileRecord`s via `find_by_ids()` and segment metadata via `get_segments_by_ids()`, and MUST defer Python `extract_snippet()` computation strictly to assembled primary hits rather than running it across all raw FTS rows.

#### Scenario: Batch loading and deferred snippet formatting on large candidate sets
- **WHEN** a query matches hundreds of FTS rows and candidate files
- **THEN** `SearchService` calls `fts_search(..., terms=None)`, fetches file and segment metadata in batched SQL queries, and runs `extract_snippet()` only for final primary `ranked` hits
