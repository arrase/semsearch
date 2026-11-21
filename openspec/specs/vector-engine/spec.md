# vector-engine Specification

## Purpose
The vector-engine capability provides zero-copy L2 normalization, unit-matrix cosine similarity, float16 byte serialization with dynamic dimensionality, RAM `VectorSnapshot` caching semantics, and vectorized modality-aware Z-score normalization for multimodal embeddings.

## Requirements

### Requirement: Vector Math, Unit-Matrix Similarity, and Dynamic Float16 Serialization
The vector engine MUST provide L2 normalization for 1D vectors and 2D matrices (`l2_normalize`), general cosine similarity (`cosine_similarity`), and zero-copy pre-normalized cosine similarity (`cosine_similarity_unit(query_vec, unit_matrix)`) that L2-normalizes only the query vector and evaluates `unit_matrix @ query` without copying `unit_matrix` or recomputing document norms. `float16_to_bytes` and `bytes_to_float16` MUST serialize and deserialize arbitrary embedding dimensions (e.g., 512, 768, 1024).

#### Scenario: Cosine similarity against pre-normalized RAM matrix
- **WHEN** `cosine_similarity_unit(query_vec, unit_matrix)` is invoked with a cached L2-normalized `float32` matrix
- **THEN** only the 1D query vector is L2-normalized and dot products are computed directly via matrix-vector multiplication returning `float32` scores in `[-1.0, 1.0]` without redundant matrix copies

#### Scenario: Dynamic dimension float16 serialization
- **WHEN** float32 vectors of dimension 512, 768, or 1024 are converted via `float16_to_bytes` and restored via `bytes_to_float16`
- **THEN** the reconstructed arrays preserve their original dimensionality and match original values within float16 numerical precision

### Requirement: RAM VectorSnapshot Caching and Generation Invalidation
Vector matrices loaded for search MUST be represented as immutable, read-only `VectorSnapshot` arrays (`segment_ids`, `file_ids`, `modality_codes: uint8`, `matrix: float32`, `scale_mask: bool_`) cached in RAM, invalidated strictly by `_vec_generation` increments upon committed writes, with dimensionality inferred dynamically from `len(blob) // 2` and `scale_mask` marking at most one segment per `(file_id, modality_code)` pair.

#### Scenario: Dynamic dimension inference and scale masking in VectorSnapshot
- **WHEN** a snapshot is built from stored float16 blobs of any valid dimension across multi-segment files
- **THEN** the snapshot matrix shape is `(N, dim)` with `write=False`, `modality_codes` uses compact `uint8` encoding (`MODALITY_TO_CODE`), and only the first segment per `(file_id, modality_code)` has `scale_mask=True`

### Requirement: Vectorized Modality-Aware Z-Score Normalization
`z_score_normalize(cosines, modalities, sets_scale=None)` MUST operate vectorized over `Modality` lists or `uint8` `modality_codes` arrays. It MUST compute symmetric 10% trimmed means in $O(N)$ using `np.partition` when at least 10 items exist (`_trimmed_mean`), center each modality bucket with at least 20 scale-eligible segments using its modality mean (falling back to the global scale-eligible trimmed mean without artificial offsets), trim the top 15% largest centered scale-eligible deviations via `np.partition` when at least 10 scale-eligible items exist, and scale by `sigma = max(sqrt(np.var(bg_centered)), 0.045)` using true centered background variance.

#### Scenario: Vectorized modality score calibration
- **WHEN** raw cosine similarities and `uint8` modality codes across text, image, and audio segments are passed to `z_score_normalize()`
- **THEN** scores are mean-centered per modality (for buckets with `>= 20` scale-eligible items, else global trimmed mean) and divided by `max(sqrt(np.var(bg_centered)), 0.045)` returning `float32` Z-scores

#### Scenario: Scale-eligible subset prevents single-file dominance
- **WHEN** `sets_scale` flags are provided to `z_score_normalize()`
- **THEN** only segments with `sets_scale=True` contribute to trimmed means and background variance `np.var(bg_centered)`, while all segments receive normalized Z-scores

#### Scenario: Low-variance noise queries clamped by minimum standard deviation floor
- **WHEN** a query has no genuine matches and `sqrt(np.var(bg_centered))` is below `0.045`
- **THEN** Z-score normalization divides centered scores by `0.045` so random noise does not cross the `2.2` semantic gate

#### Scenario: Upper-tail trimming preserves secondary genuine matches
- **WHEN** a corpus of at least 10 scale-eligible segments contains multiple strong semantic matches alongside unrelated background items
- **THEN** symmetric 10% mean trimming and upper-tail 15% `np.partition` deviation trimming prevent top outliers from inflating `sigma`, allowing secondary genuine hits to remain above `SEMANTIC_Z_GATE = 2.2`
