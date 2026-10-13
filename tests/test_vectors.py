"""Unit tests for vector mathematics, similarity, and normalization."""

import numpy as np
import pytest

from semsearch.core.types import Modality
from semsearch.core.vectors import (
    DIM,
    bytes_to_float16,
    cosine_similarity,
    float16_to_bytes,
    l2_normalize,
    z_score_normalize,
)


def test_dim_constant():
    """Verify standard embedding vector dimension."""
    assert DIM == 768


def test_l2_normalize_1d():
    """Verify L2 normalization for 1D vectors."""
    vec = np.array([3.0, 4.0], dtype=np.float32)
    normed = l2_normalize(vec)
    assert np.isclose(np.linalg.norm(normed), 1.0)
    assert np.allclose(normed, [0.6, 0.8])

    # Zero vector should not divide by zero
    zero_vec = np.zeros(5, dtype=np.float32)
    assert np.array_equal(l2_normalize(zero_vec), zero_vec)


def test_l2_normalize_2d():
    """Verify L2 normalization for 2D matrices along row axis."""
    matrix = np.array(
        [
            [1.0, 1.0, 1.0],
            [0.0, 0.0, 0.0],
            [3.0, 4.0, 0.0],
        ],
        dtype=np.float32,
    )
    normed = l2_normalize(matrix)
    assert np.isclose(np.linalg.norm(normed[0]), 1.0)
    assert np.allclose(normed[1], [0.0, 0.0, 0.0])
    assert np.isclose(np.linalg.norm(normed[2]), 1.0)
    assert np.allclose(normed[2], [0.6, 0.8, 0.0])


def test_float16_bytes_roundtrip():
    """Verify serialization to and from float16 bytes."""
    rng = np.random.default_rng(42)
    vec = l2_normalize(rng.standard_normal(DIM).astype(np.float32))
    data = float16_to_bytes(vec)
    assert len(data) == DIM * 2  # 1536 bytes for 768 float16 values

    recovered = bytes_to_float16(data)
    assert recovered.shape == (DIM,)
    assert recovered.dtype == np.float16
    assert np.allclose(recovered.astype(np.float32), vec, atol=1e-3)


def test_cosine_similarity():
    """Verify cosine similarity calculation against doc matrices."""
    q = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    docs = np.array(
        [
            [1.0, 0.0, 0.0],   # identical -> 1.0
            [0.0, 1.0, 0.0],   # orthogonal -> 0.0
            [-1.0, 0.0, 0.0],  # opposite -> -1.0
            [2.0, 0.0, 0.0],   # unnormalized same direction -> 1.0
        ],
        dtype=np.float32,
    )
    sims = cosine_similarity(q, docs)
    assert np.allclose(sims, [1.0, 0.0, -1.0, 1.0])

    # Empty matrix check
    empty_docs = np.empty((0, 3), dtype=np.float32)
    assert len(cosine_similarity(q, empty_docs)) == 0


def test_z_score_normalize_empty():
    """Verify Z-score normalization handles empty inputs."""
    res = z_score_normalize(np.array([], dtype=np.float32), [])
    assert len(res) == 0


def test_z_score_normalize_small_modality_uses_global_mean():
    """Verify small modality groups (< 20 items) fall back to global mean."""
    cosines = np.array([0.5, 0.7, 0.9], dtype=np.float32)
    modalities = [Modality.TEXT, Modality.IMAGE, Modality.TEXT]

    z = z_score_normalize(cosines, modalities)
    assert len(z) == 3
    # Mean of z should be approximately 0.0
    assert np.isclose(np.mean(z), 0.0, atol=1e-5)


def test_z_score_normalize_large_modality_uses_modality_mean():
    """Verify modality groups with >= 20 items use their own modality mean."""
    # 25 text items around 0.8
    text_scores = np.full(25, 0.8, dtype=np.float32)
    # 25 image items around 0.4
    image_scores = np.full(25, 0.4, dtype=np.float32)

    cosines = np.concatenate([text_scores, image_scores])
    modalities = [Modality.TEXT] * 25 + [Modality.IMAGE] * 25

    z = z_score_normalize(cosines, modalities)
    assert len(z) == 50
    # Because each group was centered by its own mean (0.8 - 0.8 = 0, 0.4 - 0.4 = 0),
    # all centered values are 0.0, sigma will be clamped to minimum 0.02
    assert np.allclose(z, 0.0)


def test_z_score_normalize_small_image_uses_global_mean_without_penalty():
    """Verify small image group (< 20 items) uses global mean without subtracting an artificial offset."""
    text_scores = np.full(25, 0.30, dtype=np.float32)
    image_scores = np.array([0.42, 0.25], dtype=np.float32)

    cosines = np.concatenate([text_scores, image_scores])
    modalities = [Modality.TEXT] * 25 + [Modality.IMAGE] * 2

    z = z_score_normalize(cosines, modalities)
    assert z[25] > 2.0
    assert z[26] < 0.0


def test_z_score_normalize_sets_scale_excludes_deep_segments():
    """Verify sets_scale=False segments do not skew mean or standard deviation."""
    scale_scores = np.linspace(0.20, 0.40, 20, dtype=np.float32)
    deep_scores = np.full(50, 0.90, dtype=np.float32)
    cosines = np.concatenate([scale_scores, deep_scores])
    modalities = [Modality.TEXT] * 70
    sets_scale = [True] * 20 + [False] * 50

    z = z_score_normalize(cosines, modalities, sets_scale=sets_scale)
    assert np.isclose(np.mean(z[:20]), 0.0, atol=1e-4)
    assert np.all(z[20:] > 5.0)

