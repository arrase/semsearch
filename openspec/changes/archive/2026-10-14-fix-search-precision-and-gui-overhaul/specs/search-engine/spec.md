# search-engine Specification Delta

## MODIFIED Requirements

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

### Requirement: Code Definition Navigation Scoring
The search engine MUST apply a relevance bonus to code files and segments that define matched identifiers and MUST associate the definition starting line number and matching segment ID as an anchor for editor jumps and code previews.

#### Scenario: Definition match bonus and line anchoring
- **WHEN** a code search hit matches a declared function, class, or type definition
- **THEN** a definition bonus is added to the file's final score so the defining file ranks above caller files, and the definition line number and containing segment ID are attached to the result

## ADDED Requirements

### Requirement: Multilingual Query Parsing and Diacritic-Insensitive Matching
The search engine MUST strip both English and Spanish stopwords and recognize both English and Spanish modality kind hints when extracting query terms, and MUST match and highlight query terms inside text snippets insensitively to Latin diacritics/accents.

#### Scenario: Spanish query term and kind extraction
- **WHEN** a user searches for a Spanish query such as `captura de pantalla` or `informacion fiscal de juan`
- **THEN** Spanish stopwords (`de`, `la`, `el`, `en`, `con`, `para`, `por`) are excluded from search terms and Spanish kind words (`captura`, `pantalla`, `imagen`, `documento`, `video`, `audio`) map to their corresponding `FileKind`

#### Scenario: Diacritic-insensitive snippet highlighting
- **WHEN** a user searches with unaccented terms such as `informacion` or `autorizacion` against text containing `INFORMACIÓN` or `Autorización`
- **THEN** `find_term_matches` and `extract_snippet` locate the accented occurrences and return the matching snippet ranges
