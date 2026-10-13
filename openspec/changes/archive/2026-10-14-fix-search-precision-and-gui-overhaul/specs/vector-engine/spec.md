# vector-engine Specification Delta

## MODIFIED Requirements

### Requirement: Modality Z-Score Normalization
The vector engine MUST normalize cosine similarity scores across modalities using modality-specific means for groups with at least 20 scale-eligible items, falling back to the global scale-eligible mean without artificial modality offsets, and dividing by a bounded global standard deviation computed over scale-eligible segments (`sets_scale`).

#### Scenario: Modality score calibration
- **WHEN** raw cosine similarities across different modalities are passed to Z-score normalization
- **THEN** scores are mean-centered per modality (when at least 20 scale-eligible vectors exist for that modality, or by the global scale-eligible mean otherwise) without subtracting a positive offset from image scores, and scaled by the bounded global standard deviation

#### Scenario: Scale-eligible subset prevents single-file dominance
- **WHEN** `sets_scale` flags are provided to Z-score normalization
- **THEN** only segments marked with `sets_scale=True` contribute to computing the global standard deviation and modality means, while all segments receive normalized Z-scores
