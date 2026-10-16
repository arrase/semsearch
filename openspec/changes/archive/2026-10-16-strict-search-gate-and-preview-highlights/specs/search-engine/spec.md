# Spec Delta

## MODIFIED Requirements

### Requirement: Search Result Quality Gating
The search engine MUST discard candidate segment matches that fail to satisfy at least one quality threshold: a Z-score of at least 2.2 (meaning match), a keyword coverage ratio of at least 0.5 (lexical match), or a direct kind-hint match when the query consists exclusively of modality/kind hint words.

#### Scenario: Pruning irrelevant candidates failing both gates
- **WHEN** a candidate segment yields both a Z-score below 2.2 and a keyword coverage ratio below 0.5 on a standard query
- **THEN** the candidate is discarded and excluded from the search result set

#### Scenario: Retaining semantic matches meeting Z-score threshold
- **WHEN** a candidate segment has a Z-score of at least 2.2 with low keyword coverage
- **THEN** the candidate is retained as a valid semantic match

#### Scenario: Retaining keyword matches meeting coverage threshold
- **WHEN** a candidate segment has a keyword coverage ratio of at least 0.5 with a Z-score below 2.2
- **THEN** the candidate is retained as a valid lexical match

#### Scenario: Retaining files matching pure kind-hint queries
- **WHEN** a user enters a query composed exclusively of kind hint terms (such as `screenshot`, `captura de pantalla`, or `pdf`)
- **THEN** indexed files matching the hinted `FileKind` are retained and ranked even when no residual lexical search terms remain
