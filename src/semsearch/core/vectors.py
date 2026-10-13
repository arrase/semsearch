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

    sums: dict[Modality, float] = {}
    counts: dict[Modality, int] = {}
    global_sum = 0.0
    global_n = 0

    for idx in range(n):
        if scale_mask[idx]:
            mod = modalities[idx]
            val = float(scores[idx])
            sums[mod] = sums.get(mod, 0.0) + val
            counts[mod] = counts.get(mod, 0) + 1
            global_sum += val
            global_n += 1

    global_mean = global_sum / global_n if global_n > 0 else 0.0
    means: dict[Modality, float] = {}
    for mod in set(modalities):
        cnt = counts.get(mod, 0)
        means[mod] = sums[mod] / cnt if cnt >= 20 else global_mean

    centered = np.array(
        [scores[idx] - means[modalities[idx]] for idx in range(n)],
        dtype=np.float64,
    )
    scale_centered = centered[scale_mask]
    variance = float(np.mean(scale_centered * scale_centered)) if len(scale_centered) > 0 else 0.0
    sigma = max(float(np.sqrt(variance)), 0.02)
    return (centered / sigma).astype(np.float32)

