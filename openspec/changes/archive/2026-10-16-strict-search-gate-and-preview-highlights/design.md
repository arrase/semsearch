# Design

## Context

See `proposal.md` for motivation and problem background. In `semsearch`, semantic vector search relies on `z_score_normalize` (`src/semsearch/core/vectors.py`) and a quality gate in `SearchService.search` (`src/semsearch/services/searcher.py`), while text and code previews are rendered through `PreviewPane` and `CodePreviewWidget` (`src/semsearch/gui/preview.py`).

## Goals / Non-Goals

**Goals:**
- Eliminate false-positive semantic hits when searching for terms with no genuine match in the corpus (such as `'agujero'`, `'elefante'`, `'guitarra'`, `'reloj'`) while preserving recall on true cross-modal and semantic matches (`'agujero negro'`, `'calavera'`, `'veterinario'`, `'impuestos'`, `'autorizacion'`).
- Eliminate misleading full-width yellow line highlights in `CodePreviewWidget` when no explicit target line (`line > 0`) exists or when previewing `FileKind.DOC` documents (including `.md` files).

**Non-Goals:**
- Modifying the SQLite database schema, re-indexing existing embeddings, or changing FTS5 lexical coverage weights.
- Introducing new UI widgets or altering non-text preview components (`ImagePreviewWidget`, `PdfPreviewWidget`, `AudioPreviewWidget`, `VideoPreviewWidget`).

## Decisions

### 1. Outlier-Resistant Trimmed Mean, Background Standard Deviation (`sigma >= 0.045`), and Semantic Gate (`z_gate = 2.2`)
- **Decision**:
  - In `src/semsearch/core/vectors.py` (`z_score_normalize`):
    - When computing the global scale-eligible mean (`global_mean`) or a modality mean (`cnt >= 20`), use a **symmetric 10% trimmed mean** (trimming `k = m // 10` from both ends of sorted values when `m >= 10`) so top positive hits do not pull the baseline mean upward while keeping symmetric noise distributions unbiased.
    - When computing `variance` from `scale_centered`, if `len(scale_centered) >= 10`, exclude the top 15% largest centered deviations (`k_top = max(1, int(len(scale_centered) * 0.15))`, using `np.sort(scale_centered)[:-k_top]`) before computing `variance = float(np.mean(bg_centered * bg_centered))`, and clamp `sigma = max(float(np.sqrt(variance)), 0.045)`.
  - Define `SEMANTIC_Z_GATE = 2.2` in `src/semsearch/core/types.py` (used by `SearchHit.reason`) and use `z_gate = SEMANTIC_Z_GATE` (`2.2`) in `src/semsearch/services/searcher.py`.
- **Rationale**:
  - Empirical evaluation against live `embeddinggemma-2` embeddings shows two complementary statistical regimes in small desktop collections (`~20` scale-eligible segments):
    1. **Noise queries (`'agujero'`, `'elefante'`, `'guitarra'`, `'reloj'`)**: When no true match exists in the corpus, raw cosine similarities cluster tightly with a standard deviation of `0.028–0.038`. Raising the `sigma` floor from `0.02` to `0.045` and `z_gate` to `2.2` keeps all noise queries below the gate (`z <= 1.99 < 2.2`).
    2. **Multi-hit queries (`'agujero negro'`, `'black hole'`)**: When 2 genuine matches exist in a 20-segment collection (10% of the sample), an untrimmed mean and standard deviation include those positive outliers (`cos = 0.743` and `0.689` in Spanish; `0.771` and `0.730` in English), pulling the mean up by `+0.02` and inflating `sigma` from `0.045` to `0.062–0.069`. That inflated `sigma` masks the second black hole image (`z = 1.54` for `'agujero negro'` and `z = 2.01` for `'black hole'`, both `< 2.2`). With symmetric 10% trimmed mean and upper-tail 15% trimmed `sigma`, the background spread remains anchored at `0.045` and both black hole images pass cleanly (`z = 3.45` and `2.25` for `'agujero negro'`; `z = 4.19` and `3.29` for `'black hole'`).
- **Alternatives Considered**:
  - *Untrimmed sample mean and standard deviation*: Fails on multi-hit queries in small collections due to outlier masking on the 2nd/3rd hits.
  - *One-sided upper-tail trimming on the mean*: Shifts the mean downward even on symmetric pure-noise queries, slightly inflating noise Z-scores; symmetric 10% trimming on the mean avoids any downward bias on noise.
  - *Hard absolute cosine threshold (e.g., `cos >= 0.70`)*: Rejected because baseline cosine values vary by query length, language, and modality (`image` vs `text` vs `audio`).

### 2. Conditional Line Highlighting and Document Routing in `PreviewPane` / `CodePreviewWidget`
- **Decision**:
  - In `CodePreviewWidget.load_code` (`src/semsearch/gui/preview.py`):
    - Change the default parameter `line: int = 0`.
    - Only create and append the full-width yellow `line_selection` (`QColor(250, 204, 21, 90)`) when `line > 0`.
    - Always highlight literal occurrences of `query` terms in the document (`term_sel`).
    - When `line <= 0`: if `first_match_cursor` is found, position and center the cursor on `first_match_cursor`; otherwise, if `excerpt` is non-empty, search for a clean snippet prefix of `excerpt` (`excerpt.replace("«", "").replace("»", "").replace("…", "").strip()[:60]`) to center the viewport on the matched chunk without adding a yellow line highlight; otherwise leave the cursor at the start of the document.
  - In `PreviewPane.load_result` (`src/semsearch/gui/preview.py`):
    - Route `kind == FileKind.CODE` (or code file extensions when `kind != FileKind.DOC`) with `line=focus` (`int(item.focus) if item.focus > 0 else (int(item.loc) if item.kind == FileKind.CODE else 0)`) and `wrap_lines=False`.
    - Route `kind == FileKind.DOC` (including `.md` files classified as `FileKind.DOC`) with `line=0` and `wrap_lines=True` so chunk indices (`item.loc`) are never misinterpreted as line numbers and markdown notes wrap cleanly in the preview pane.
- **Rationale**:
  - Directly resolves the bug where `.md` files in `FileKind.DOC` passed `focus = int(item.loc) = 0` into `load_code`, which clamped `target_line = max(1, 0) = 1` and painted Line 1 in yellow even when the match was purely semantic or located in another chunk.

## Risks / Trade-offs

- **[Risk] Existing unit tests in `tests/test_gui_preview.py` or `tests/test_searcher.py` asserting old selection counts or `z >= 2.0` boundary** → Mitigation: Inspect and update affected unit tests to verify both `line > 0` (code line highlight present) and `line == 0` (no full-width line highlight; only matched query term selections).
