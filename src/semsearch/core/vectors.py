"""Vector mathematics, normalization, and similarity engine."""

from typing import Any

import numpy as np
import numpy.typing as npt

from semsearch.core.types import Modality

DIM = 768
_MIN_TRIM_SAMPLES = 10
_MIN_MODALITY_BUCKET_SIZE = 20

MODALITY_TO_CODE: dict[Modality, int] = {
    Modality.NONE: 0,
    Modality.TEXT: 1,
    Modality.IMAGE: 2,
    Modality.AUDIO: 3,
}
CODE_TO_MODALITY: tuple[Modality, ...] = (
    Modality.NONE,
    Modality.TEXT,
    Modality.IMAGE,
    Modality.AUDIO,
)

FloatArray = npt.NDArray[np.floating[Any]]


def l2_normalize(vec: FloatArray) -> FloatArray:
    """Compute L2 normalized representation for 1D vector or 2D matrix."""
    if vec.ndim == 1:
        norm = float(np.linalg.norm(vec))
        return vec if norm <= 0.0 else vec / norm
    norms = np.linalg.norm(vec, axis=-1, keepdims=True)
    norms = np.where(norms <= 0.0, 1.0, norms)
    return vec / norms


def cosine_similarity(query_vec: FloatArray, doc_matrix: FloatArray) -> npt.NDArray[np.float32]:
    """Compute cosine similarity between a 1D query vector and a 2D document matrix."""
    if doc_matrix.size == 0:
        return np.empty((0,), dtype=np.float32)

    query = np.asarray(query_vec, dtype=np.float32)
    docs = np.asarray(doc_matrix, dtype=np.float32)

    q_norm = float(np.linalg.norm(query))
    d_norms = np.linalg.norm(docs, axis=1)

    dots = docs @ query
    denom = q_norm * d_norms
    denom = np.where(denom <= 0.0, 1.0, denom)
    result: npt.NDArray[np.float32] = (dots / denom).astype(np.float32, copy=False)
    return result


def cosine_similarity_unit(query_vec: FloatArray, unit_matrix: FloatArray) -> npt.NDArray[np.float32]:
    """Compute cosine similarity between a 1D query vector and a pre-L2-normalized 2D document matrix."""
    if unit_matrix.size == 0:
        return np.empty((0,), dtype=np.float32)

    query = l2_normalize(np.asarray(query_vec, dtype=np.float32))
    docs = np.asarray(unit_matrix, dtype=np.float32)
    result: npt.NDArray[np.float32] = (docs @ query).astype(np.float32, copy=False)
    return result


def float16_to_bytes(vec: FloatArray) -> bytes:
    """Convert numpy array vector to float16 raw bytes."""
    return vec.astype(np.float16).tobytes()


def bytes_to_float16(data: bytes) -> npt.NDArray[np.float16]:
    """Convert float16 raw bytes back to numpy array."""
    return np.frombuffer(data, dtype=np.float16)


def _trimmed_mean(values: FloatArray) -> float:
    """Compute symmetric 10% trimmed mean in O(N) using np.partition when at least 10 items exist."""
    m = len(values)
    if m == 0:
        return 0.0
    if m >= _MIN_TRIM_SAMPLES:
        k = m // 10
        partitioned = np.partition(values, (k, m - k - 1))
        return float(np.mean(partitioned[k : m - k]))
    return float(np.mean(values))


def z_score_normalize(
    cosines: FloatArray,
    modalities: list[Modality] | npt.NDArray[np.uint8],
    sets_scale: list[bool] | npt.NDArray[np.bool_] | None = None,
) -> npt.NDArray[np.float32]:
    """Compute vectorized modality-aware Z-score normalization over scale-eligible segments."""
    n = len(cosines)
    if n == 0:
        return np.empty((0,), dtype=np.float32)

    scores = np.asarray(cosines, dtype=np.float64)
    if isinstance(modalities, np.ndarray):
        codes = np.asarray(modalities, dtype=np.uint8)
    else:
        codes = np.fromiter((MODALITY_TO_CODE[m] for m in modalities), dtype=np.uint8, count=n)

    if sets_scale is None:
        scale_mask = np.ones(n, dtype=bool)
    else:
        scale_mask = np.asarray(sets_scale, dtype=bool)
        if not np.any(scale_mask):
            scale_mask = np.ones(n, dtype=bool)

    scale_scores = scores[scale_mask]
    scale_codes = codes[scale_mask]
    global_mean = _trimmed_mean(scale_scores)

    means = np.full(len(MODALITY_TO_CODE), global_mean, dtype=np.float64)
    for code in range(len(MODALITY_TO_CODE)):
        bucket = scale_scores[scale_codes == code]
        if len(bucket) >= _MIN_MODALITY_BUCKET_SIZE:
            means[code] = _trimmed_mean(bucket)

    centered = scores - means[codes]
    scale_centered = centered[scale_mask]
    if len(scale_centered) >= _MIN_TRIM_SAMPLES:
        k_top = max(1, int(len(scale_centered) * 0.15))
        n_bg = len(scale_centered) - k_top
        bg_centered = np.partition(scale_centered, n_bg)[:n_bg]
    else:
        bg_centered = scale_centered

    variance = float(np.var(bg_centered)) if len(bg_centered) > 0 else 0.0
    sigma = max(float(np.sqrt(variance)), 0.045)
    return (centered / sigma).astype(np.float32)
