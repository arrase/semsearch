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


def _trimmed_mean(values: np.ndarray) -> float:
    """Compute symmetric 10% trimmed mean when at least 10 items exist."""
    m = len(values)
    if m == 0:
        return 0.0
    if m >= 10:
        k = m // 10
        return float(np.mean(np.sort(values)[k : m - k]))
    return float(np.mean(values))


def z_score_normalize(
    cosines: np.ndarray,
    modalities: list[Modality],
    sets_scale: list[bool] | np.ndarray | None = None,
) -> np.ndarray:
    """Compute modality-aware Z-score normalization over scale-eligible segments."""
    n = len(cosines)
    if n == 0:
        return np.empty((0,), dtype=np.float32)

    scores = np.asarray(cosines, dtype=np.float64)
    if sets_scale is None:
        scale_mask = np.ones(n, dtype=bool)
    else:
        scale_mask = np.asarray(sets_scale, dtype=bool)
        if not np.any(scale_mask):
            scale_mask = np.ones(n, dtype=bool)

    global_mean = _trimmed_mean(scores[scale_mask])

    mod_buckets: dict[Modality, list[float]] = {}
    for idx in range(n):
        if scale_mask[idx]:
            mod_buckets.setdefault(modalities[idx], []).append(float(scores[idx]))

    means: dict[Modality, float] = {}
    for mod in set(modalities):
        bucket = mod_buckets.get(mod, [])
        means[mod] = (
            _trimmed_mean(np.asarray(bucket, dtype=np.float64))
            if len(bucket) >= 20
            else global_mean
        )

    centered = np.array(
        [scores[idx] - means[modalities[idx]] for idx in range(n)],
        dtype=np.float64,
    )
    scale_centered = centered[scale_mask]
    if len(scale_centered) >= 10:
        k_top = max(1, int(len(scale_centered) * 0.15))
        bg_centered = np.sort(scale_centered)[:-k_top]
    else:
        bg_centered = scale_centered
    variance = float(np.mean(bg_centered * bg_centered)) if len(bg_centered) > 0 else 0.0
    sigma = max(float(np.sqrt(variance)), 0.045)
    return (centered / sigma).astype(np.float32)

