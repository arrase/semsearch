# Proposal

## Why

When searching for terms that do not appear in indexed text documents (such as `'agujero'`), two issues degrade the user experience:
1. **False-positive semantic matches on short documents and outlier masking on multi-hit queries**: Because `z_score_normalize` uses a low standard-deviation floor (`sigma >= 0.02`) and `SearchService` gates semantic candidates at `z >= 2.0`, queries with no genuine match in the index (where background cosine spread is `~0.030–0.038`) inflate minor cosine variations on short notes (`Bienvenido.md` with `"bóveda"`, `2026-10-16.md`) or multi-segment videos into `z > 2.0` false positives. Conversely, when a query has **multiple genuine matches** (such as 2 black hole images for `'agujero negro'` or `'black hole'`) in a small collection (`~20` scale-eligible segments), including those top positive matches in the sample mean and standard deviation pulls the mean up and inflates `sigma` (`~0.062–0.069`), masking the second match below the semantic gate.
2. **Misleading yellow line highlight in document previews**: `PreviewPane.load_result` routes `.md` documents into the `CodePreviewWidget` branch using `focus = int(item.loc)` (where `loc` is a chunk index `0, 1, ...`, not a line number), and `CodePreviewWidget.load_code` forces `target_line = max(1, line)` and unconditionally paints a full-width yellow line highlight on Line 1 even when no target line exists (`line <= 0`) and the searched word does not appear on that line.

## What Changes

- **Outlier-Resistant Z-Score Calibration and Semantic Quality Gate**:
  - Update `z_score_normalize` (`src/semsearch/core/vectors.py`) to compute modality and global scale-eligible means using a symmetric 10% trimmed mean (when at least 10 scale-eligible items exist) and compute standard deviation (`sigma`) after excluding the top 15% positive centered deviations (when at least 10 scale-eligible items exist) with a minimum floor of `0.045`. This prevents low-variance noise queries from inflating Z-scores while preventing multiple genuine matches from inflating `sigma` and masking secondary hits.
  - Increase the semantic quality gate threshold (`z_gate`) in `SearchService.search` (`src/semsearch/services/searcher.py`) and `SearchHit.reason` (`src/semsearch/core/types.py`) from `2.0` to `2.2` (`SEMANTIC_Z_GATE = 2.2`).
- **Accurate Text & Code Preview Highlighting without False Yellow Line Selection**:
  - Update `CodePreviewWidget.load_code` (`src/semsearch/gui/preview.py`) so `line` defaults to `0` (no line highlight) and a full-width yellow line highlight (`ExtraSelection` with `FullWidthSelection`) is only applied when `line > 0` (such as an explicit code definition or line anchor).
  - Update `PreviewPane.load_result` (`src/semsearch/gui/preview.py`) so `FileKind.DOC` files (including `.md` notes) pass `line=0` and `wrap_lines=True` instead of treating chunk index `item.loc` as a line number, and scroll to the first matched query term (or the matching chunk's `excerpt` text when matched by meaning) without painting a false yellow line bar.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `vector-engine`: Use symmetric 10% trimmed mean and upper-tail 15% trimmed standard deviation with a minimum `sigma` floor of `0.045` in `z_score_normalize` so low-variance noise queries are clamped while multi-hit queries in small collections do not mask secondary matches.
- `search-engine`: Raise the semantic Z-score quality gate threshold (`z_gate`) from `2.0` to `2.2` so weak semantic matches are pruned while genuine semantic and lexical matches are retained.
- `search-panel`: Refine `PreviewPane` and `CodePreviewWidget` so full-width yellow line highlights are only rendered when an explicit target line (`line > 0`) is provided for code results, avoiding false Line 1 highlights on document and semantic matches while highlighting matched query terms and scrolling to matched excerpts.

## Impact

- `src/semsearch/core/vectors.py`: `z_score_normalize` `sigma` floor (`0.045`).
- `src/semsearch/core/types.py`: `SearchHit.reason` semantic threshold (`z >= 2.2`).
- `src/semsearch/services/searcher.py`: `SearchService.search` semantic quality gate (`z_gate = 2.2`).
- `src/semsearch/gui/preview.py`: `CodePreviewWidget.load_code` and `PreviewPane.load_result` line highlight and document routing behavior.
- `tests/test_vectors.py`, `tests/test_searcher.py`, `tests/test_gui_preview.py`: Unit and widget tests covering the stricter threshold and preview selection behavior.
