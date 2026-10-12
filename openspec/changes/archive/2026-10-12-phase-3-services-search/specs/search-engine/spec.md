# Spec Delta

## Purpose

Provides dual-stage semantic and keyword search, isolated code query routing, DigUp hybrid ranking fusion, quality gating, and weaker matches folding.

## ADDED Requirements

### Requirement: Dual Query Routing for Code and General Modes
The search engine MUST route queries prefixed with `code:` exclusively to the dedicated code index and route all other queries to the general file index. The engine MUST strip the `code:` prefix prior to processing code search terms.

#### Scenario: Isolated code search routing
- **WHEN** a user enters a query starting with the `code:` prefix
- **THEN** search evaluation executes against the code index database and omits general documents

#### Scenario: Default general query routing
- **WHEN** a user enters a standard query without the `code:` prefix
- **THEN** search evaluation executes against the general file index database and omits isolated code repositories

### Requirement: Hybrid DigUp Ranking Fusion
The search engine MUST rank candidate results using the DigUp formula: Score = W_meaning * max(z, 0) + W_words * (coverage)^1.5 + HintBonus. For standard queries, semantic and lexical weights SHALL default to equal weighting, while lookup queries featuring serials or code identifiers SHALL boost the lexical weight.

#### Scenario: Balanced hybrid scoring for natural language query
- **WHEN** a natural language query is executed
- **THEN** semantic Z-scores and keyword coverage are blended using equal primary weights with non-negative Z-score clamping

#### Scenario: Boosted exact lexical weight for code symbols and serial identifiers
- **WHEN** a query is identified as a lookup containing identifiers, symbols, or serial numbers
- **THEN** lexical keyword coverage weight is amplified over semantic embedding weight in the final score calculation

### Requirement: Search Result Quality Gating
The search engine MUST discard candidate segment matches that fail to satisfy at least one quality threshold: a Z-score of at least 2.0 (meaning match) or a keyword coverage ratio of at least 0.5 (lexical match).

#### Scenario: Pruning irrelevant candidates failing both gates
- **WHEN** a candidate segment yields both a Z-score below 2.0 and a keyword coverage ratio below 0.5
- **THEN** the candidate is discarded and excluded from the search result set

#### Scenario: Retaining semantic matches meeting Z-score threshold
- **WHEN** a candidate segment has a Z-score of at least 2.0 with low keyword coverage
- **THEN** the candidate is retained as a valid semantic match

#### Scenario: Retaining keyword matches meeting coverage threshold
- **WHEN** a candidate segment has a keyword coverage ratio of at least 0.5 with a Z-score below 2.0
- **THEN** the candidate is retained as a valid lexical match

### Requirement: Weaker Matches Folding
The search engine MUST classify search results with a final score below 50% of the top-ranking result score as weaker matches, partitioning them for collapsible display in search results.

#### Scenario: Secondary results below half of top score folded
- **WHEN** search results are returned with scores less than 0.5 times the maximum score in the set
- **THEN** those lower-scoring items are flagged as weaker matches to be displayed collapsed

#### Scenario: Retaining all strong results in primary view
- **WHEN** all returned search results score at or above 50% of the maximum score
- **THEN** all items remain unfolded in the primary results list

### Requirement: Code Definition Navigation Scoring
The search engine MUST apply a relevance bonus to code segments that define matched identifiers and MUST associate the definition starting line number as an anchor for editor jumps.

#### Scenario: Definition match bonus and line anchoring
- **WHEN** a code search hit matches a declared function, class, or type definition
- **THEN** an additional hint bonus is added to its score and the line number of the definition is attached to the result
