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
The vector engine MUST normalize cosine similarity scores across modalities using modality-specific means for groups with at least 20 scale-eligible items, falling back to the global scale-eligible mean without artificial modality offsets, and dividing by a bounded global standard deviation computed over scale-eligible segments (`sets_scale`).

#### Scenario: Modality score calibration
- **WHEN** raw cosine similarities across different modalities are passed to Z-score normalization
- **THEN** scores are mean-centered per modality (when at least 20 scale-eligible vectors exist for that modality, or by the global scale-eligible mean otherwise) without subtracting a positive offset from image scores, and scaled by the bounded global standard deviation

#### Scenario: Scale-eligible subset prevents single-file dominance
- **WHEN** `sets_scale` flags are provided to Z-score normalization
- **THEN** only segments marked with `sets_scale=True` contribute to computing the global standard deviation and modality means, while all segments receive normalized Z-scores
