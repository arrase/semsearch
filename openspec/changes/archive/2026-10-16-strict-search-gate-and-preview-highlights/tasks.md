# Tasks

## 1. Outlier-Resistant Z-Score Calibration and Semantic Quality Gate

- [x] 1.1 Update `z_score_normalize` in `src/semsearch/core/vectors.py` to use a symmetric 10% trimmed mean (when `>= 10` scale-eligible items exist), exclude the top 15% largest centered deviations from `variance` (when `>= 10` scale-eligible items exist), and clamp `sigma >= 0.045`; add/update unit tests in `tests/test_vectors.py` and `tests/test_searcher.py` verifying low-variance noise clamping and multi-hit outlier resistance (`pytest tests/test_vectors.py tests/test_searcher.py`).
- [x] 1.2 Define `SEMANTIC_Z_GATE = 2.2` in `src/semsearch/core/types.py` (used by `SearchHit.reason`) and update `SearchService.search` in `src/semsearch/services/searcher.py` to use `z_gate = SEMANTIC_Z_GATE`, verifying with unit tests in `tests/test_searcher.py` that weak semantic candidates (`z < 2.2`, `coverage < 0.5`) are pruned while candidates with `z >= 2.2` pass (`pytest tests/test_searcher.py`).

## 2. Preview Highlighting and Document Routing

- [x] 2.1 Update `CodePreviewWidget.load_code` and `PreviewPane.load_result` in `src/semsearch/gui/preview.py` so `line` defaults to `0`, full-width yellow line selection (`FullWidthSelection`) is only applied when `line > 0`, `FileKind.DOC` files (including `.md`) route with `line=0` and `wrap_lines=True`, and the editor scrolls to the first matched query term or chunk `excerpt` without painting a yellow line bar when `line <= 0`.
- [x] 2.2 Update and add widget tests in `tests/test_gui_preview.py` verifying that `FileKind.DOC` and `line=0` previews omit full-width line selection while highlighting matched query terms, and `FileKind.CODE` with `line > 0` retains the yellow anchor line highlight (`pytest tests/test_gui_preview.py`).

## 3. End-to-End Verification

- [x] 3.1 Run the full test suite (`pytest`) to verify all unit, GUI, and E2E search tests pass with zero regressions.
