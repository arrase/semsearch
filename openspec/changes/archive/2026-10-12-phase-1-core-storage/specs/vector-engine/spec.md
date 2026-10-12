# Spec Delta

## Purpose

The vector-engine capability provides vector normalization, similarity scoring, float16 byte conversions, and modality-aware Z-score normalization for multimodal embeddings.

## ADDED Requirements

### Requirement: Vector Math and Similarity Computations
The vector engine MUST provide L2 normalization for 1D vectors and 2D matrices, compute cosine similarity between query vectors and document matrices, and serialize float16 bytes.

#### Scenario: Normalize and compute cosine similarity
- **WHEN** an unnormalized vector is L2 normalized and compared against a matrix of normalized vectors
- **THEN** valid cosine similarity scores between -1.0 and 1.0 are returned

#### Scenario: Float16 serialization
- **WHEN** a 768-dimensional float32 vector is converted to float16 bytes and deserialized
- **THEN** the reconstructed array matches the original values within float16 numerical precision

### Requirement: Modality Z-Score Normalization
The vector engine MUST normalize cosine similarity scores across modalities using modality-specific means for groups with at least 20 items and a global standard deviation.

#### Scenario: Modality score calibration
- **WHEN** raw cosine similarities across different modalities are passed to Z-score normalization
- **THEN** scores are mean-centered per modality and scaled by the bounded global standard deviation
