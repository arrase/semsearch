# vector-engine Specification

## Purpose
The vector-engine capability provides vector normalization, similarity scoring, float16 byte conversions, and modality-aware Z-score normalization for multimodal embeddings.

## Requirements

### Requirement: Vector Math and Similarity Computations
The vector engine MUST provide L2 normalization for 1D vectors and 2D matrices, compute cosine similarity between query vectors and document matrices, and serialize float16 bytes.

#### Scenario: Normalize and compute cosine similarity
- **WHEN** an unnormalized vector is L2 normalized and compared against a matrix of normalized vectors
- **THEN** valid cosine similarity scores between -1.0 and 1.0 are returned

#### Scenario: Float16 serialization
- **WHEN** a 768-dimensional float32 vector is converted to float16 bytes and deserialized
- **THEN** the reconstructed array matches the original values within float16 numerical precision

### Requirement: Modality Z-Score Normalization
The vector engine MUST normalize cosine similarity scores across modalities using modality-specific means (computed with symmetric 10% trimming when at least 10 items exist) for groups with at least 20 scale-eligible items, falling back to the global scale-eligible mean (computed with symmetric 10% trimming when at least 10 scale-eligible items exist) without artificial modality offsets, and dividing by a bounded global standard deviation (`sigma >= 0.045`) computed over the background scale-eligible segments (`sets_scale`) after excluding the top 15% largest centered deviations when at least 10 scale-eligible segments exist.

#### Scenario: Modality score calibration
- **WHEN** raw cosine similarities across different modalities are passed to Z-score normalization
- **THEN** scores are mean-centered per modality (when at least 20 scale-eligible vectors exist for that modality, or by the global scale-eligible mean otherwise) without subtracting a positive offset from image scores, and scaled by the bounded global standard deviation with a minimum floor of `0.045`

#### Scenario: Scale-eligible subset prevents single-file dominance
- **WHEN** `sets_scale` flags are provided to Z-score normalization
- **THEN** only segments marked with `sets_scale=True` contribute to computing the global standard deviation and modality means, while all segments receive normalized Z-scores

#### Scenario: Low-variance noise queries are clamped by minimum standard deviation floor
- **WHEN** a query has no genuine matches in the corpus and scale-eligible cosine similarities exhibit a narrow standard deviation below `0.045`
- **THEN** Z-score normalization divides centered scores by `0.045` so minor random cosine deviations do not inflate above the semantic quality gate

#### Scenario: Multiple genuine matches in a small corpus do not mask secondary hits
- **WHEN** a corpus of at least 10 scale-eligible segments contains two or more strong semantic matches (outliers) alongside unrelated background items
- **THEN** symmetric 10% mean trimming and upper-tail 15% deviation trimming prevent the top match from inflating `sigma`, allowing the secondary genuine match to retain a Z-score above the `2.2` semantic gate
