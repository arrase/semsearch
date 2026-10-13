# search-engine Specification

## Purpose
Provides dual-stage semantic and keyword search, isolated code query routing, DigUp hybrid ranking fusion, quality gating, and weaker matches folding.

## Requirements

### Requirement: Dual Query Routing for Code and General Modes
The search engine MUST route queries prefixed with `code:` exclusively to the dedicated code index and route all other queries to the general file index. The engine MUST strip the `code:` prefix prior to processing code search terms.

#### Scenario: Isolated code search routing
- **WHEN** a user enters a query starting with the `code:` prefix
- **THEN** search evaluation executes against the code index database and omits general documents

#### Scenario: Default general query routing
- **WHEN** a user enters a standard query without the `code:` prefix
- **THEN** search evaluation executes against the general file index database and omits isolated code repositories

### Requirement: Hybrid DigUp Ranking Fusion
The search engine MUST rank candidate results using the DigUp formula: Score = W_meaning * max(z, 0) + W_words * (coverage)^1.5 + LexicalBonus + HintBonus. For standard queries, semantic and lexical weights SHALL default to equal weighting, while lookup queries featuring serials or code identifiers SHALL boost the lexical weight. Candidate files matching query terms in their filename or top BM25 lexical rank SHALL receive a lexical bonus so direct filename hits outrank incidental body mentions.

#### Scenario: Balanced hybrid scoring for natural language query
- **WHEN** a natural language query is executed
- **THEN** semantic Z-scores and keyword coverage are blended using equal primary weights with non-negative Z-score clamping

#### Scenario: Boosted exact lexical weight for code symbols and serial identifiers
- **WHEN** a query is identified as a lookup containing identifiers, symbols, or serial numbers
- **THEN** lexical keyword coverage weight is amplified over semantic embedding weight in the final score calculation

#### Scenario: Filename lexical match outranks incidental body mention
- **WHEN** a query matches a document's filename directly and also appears once inside the body of a longer document
- **THEN** the filename match receives a higher BM25 and filename lexical bonus and ranks above the incidental body mention

### Requirement: Search Result Quality Gating
The search engine MUST discard candidate segment matches that fail to satisfy at least one quality threshold: a Z-score of at least 2.0 (meaning match), a keyword coverage ratio of at least 0.5 (lexical match), or a direct kind-hint match when the query consists exclusively of modality/kind hint words.

#### Scenario: Pruning irrelevant candidates failing both gates
- **WHEN** a candidate segment yields both a Z-score below 2.0 and a keyword coverage ratio below 0.5 on a standard query
- **THEN** the candidate is discarded and excluded from the search result set

#### Scenario: Retaining semantic matches meeting Z-score threshold
- **WHEN** a candidate segment has a Z-score of at least 2.0 with low keyword coverage
- **THEN** the candidate is retained as a valid semantic match

#### Scenario: Retaining keyword matches meeting coverage threshold
- **WHEN** a candidate segment has a keyword coverage ratio of at least 0.5 with a Z-score below 2.0
- **THEN** the candidate is retained as a valid lexical match

#### Scenario: Retaining files matching pure kind-hint queries
- **WHEN** a user enters a query composed exclusively of kind hint terms (such as `screenshot`, `captura de pantalla`, or `pdf`)
- **THEN** indexed files matching the hinted `FileKind` are retained and ranked even when no residual lexical search terms remain

### Requirement: Weaker Matches Folding
The search engine MUST classify search results with a final score below 50% of the top-ranking result score as weaker matches, partitioning them for collapsible display in search results.

#### Scenario: Secondary results below half of top score folded
- **WHEN** search results are returned with scores less than 0.5 times the maximum score in the set
- **THEN** those lower-scoring items are flagged as weaker matches to be displayed collapsed

#### Scenario: Retaining all strong results in primary view
- **WHEN** all returned search results score at or above 50% of the maximum score
- **THEN** all items remain unfolded in the primary results list

### Requirement: Code Definition Navigation Scoring
The search engine MUST apply a relevance bonus to code files and segments that define matched identifiers and MUST associate the definition starting line number and matching segment ID as an anchor for editor jumps and code previews.

#### Scenario: Definition match bonus and line anchoring
- **WHEN** a code search hit matches a declared function, class, or type definition
- **THEN** a definition bonus is added to the file's final score so the defining file ranks above caller files, and the definition line number and containing segment ID are attached to the result

### Requirement: Multilingual Query Parsing and Diacritic-Insensitive Matching
The search engine MUST strip both English and Spanish stopwords and recognize both English and Spanish modality kind hints when extracting query terms, and MUST match and highlight query terms inside text snippets insensitively to Latin diacritics/accents.

#### Scenario: Spanish query term and kind extraction
- **WHEN** a user searches for a Spanish query such as `captura de pantalla` or `informacion fiscal de juan`
- **THEN** Spanish stopwords (`de`, `la`, `el`, `en`, `con`, `para`, `por`) are excluded from search terms and Spanish kind words (`captura`, `pantalla`, `imagen`, `documento`, `video`, `audio`) map to their corresponding `FileKind`

#### Scenario: Diacritic-insensitive snippet highlighting
- **WHEN** a user searches with unaccented terms such as `informacion` or `autorizacion` against text containing `INFORMACIÓN` or `Autorización`
- **THEN** `find_term_matches` and `extract_snippet` locate the accented occurrences and return the matching snippet ranges
