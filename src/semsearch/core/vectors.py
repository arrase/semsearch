"""Vector mathematics, normalization, and similarity engine."""

import numpy as np

from semsearch.core.types import Modality

DIM = 768


def l2_normalize(vec: np.ndarray) -> np.ndarray:
    """Compute L2 normalized representation for 1D vector or 2D matrix."""
    if vec.ndim == 1:
        norm = float(np.linalg.norm(vec))
        return vec if norm == 0.0 else vec / norm
    norms = np.linalg.norm(vec, axis=-1, keepdims=True)
    norms = np.where(norms == 0.0, 1.0, norms)
    return vec / norms


def cosine_similarity(query_vec: np.ndarray, doc_matrix: np.ndarray) -> np.ndarray:
    """Compute cosine similarity between a 1D query vector and a 2D document matrix."""
    if doc_matrix.size == 0 or len(doc_matrix) == 0:
        return np.empty((0,), dtype=np.float32)

    query = query_vec.astype(np.float32)
    docs = doc_matrix.astype(np.float32)

    q_norm = float(np.linalg.norm(query))
    d_norms = np.linalg.norm(docs, axis=1)

    dots = np.dot(docs, query)
    denom = q_norm * d_norms
    denom = np.where(denom == 0.0, 1.0, denom)
    return (dots / denom).astype(np.float32)


def float16_to_bytes(vec: np.ndarray) -> bytes:
    """Convert numpy array vector to float16 raw bytes."""
    return vec.astype(np.float16).tobytes()


def bytes_to_float16(data: bytes) -> np.ndarray:
    """Convert float16 raw bytes back to numpy array."""
    return np.frombuffer(data, dtype=np.float16)


def z_score_normalize(cosines: np.ndarray, modalities: list[Modality]) -> np.ndarray:
    """Compute modality-aware Z-score normalization."""
    if len(cosines) == 0:
        return np.empty((0,), dtype=np.float32)

    scores = np.asarray(cosines, dtype=np.float64)
    global_mean = float(np.mean(scores))

    sums: dict[Modality, float] = {}
    counts: dict[Modality, int] = {}
    for score, mod in zip(scores, modalities):
        sums[mod] = sums.get(mod, 0.0) + float(score)
        counts[mod] = counts.get(mod, 0) + 1

    means: dict[Modality, float] = {}
    for mod, count in counts.items():
        if count >= 20:
            means[mod] = sums[mod] / count
        else:
            means[mod] = global_mean

    c = np.array([score - means[mod] for score, mod in zip(scores, modalities)], dtype=np.float64)
    sigma = max(float(np.std(c)), 0.02)
    mean_c = float(np.mean(c))
    z = (c - mean_c) / sigma
    return z.astype(np.float32)
