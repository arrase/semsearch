# Tasks

## 1. Ollama Client & Multimodal Model Discovery

- [x] 1.1 Implement `OllamaClient` and custom exceptions in `src/semsearch/core/ollama.py` with `/api/tags` and `/api/show` model inspection, verifying with unit tests.
- [x] 1.2 Implement multimodal capability filtering in `OllamaClient.list_multimodal_models` (supporting vision and audio models), verifying with tests against mocked model info.
- [x] 1.3 Implement `OllamaClient.embed` supporting text, image base64, and audio base64 inputs with batch splitting and timeouts, verifying with test assertions.
- [x] 1.4 Implement comprehensive test suite in `tests/test_ollama.py` and verify all pass with `.venv/bin/pytest tests/test_ollama.py`.

## 2. Image and Screenshot Extractor

- [x] 2.1 Implement `extract_image` in `src/semsearch/extractors/images.py` with EXIF orientation correction and Pillow downscaling (1024px images, 1536px screenshots), verifying with dimension assertions.
- [x] 2.2 Implement direct base64 encoding without OCR in `src/semsearch/extractors/images.py`, verifying zero OCR library imports or calls.
- [x] 2.3 Implement unit tests in `tests/test_extractors_images.py` and verify with `.venv/bin/pytest tests/test_extractors_images.py`.

## 3. PDF Extractor

- [x] 3.1 Implement `extract_pdf` in `src/semsearch/extractors/pdfs.py` with PyMuPDF two-stage page processing (pages 1–30 in pass 1, remainder deferred), verifying page range limits.
- [x] 3.2 Implement threshold logic: digital text extraction for pages >= 200 characters, and 1400px page image rendering for pages < 200 characters with zero OCR, verifying modality outputs.
- [x] 3.3 Implement unit tests in `tests/test_extractors_pdfs.py` and verify with `.venv/bin/pytest tests/test_extractors_pdfs.py`.

## 4. Document and Text Extractor

- [x] 4.1 Implement `read_doc_text` and `strip_html` in `src/semsearch/extractors/docs.py` supporting txt, md, html, docx, odt, and rtf, verifying stripped plain text output.
- [x] 4.2 Implement `chunk_text` with 1800 character chunk size, 200 character overlap snapped to word boundaries, and `has_words` filter (>= 20% alphabetic letters), verifying chunk boundary assertions.
- [x] 4.3 Implement `extract_doc` returning `ExtractedSegment` objects in `src/semsearch/extractors/docs.py` and verify with unit tests in `tests/test_extractors_docs.py`.

## 5. Media Extractor (Audio & Video)

- [x] 5.1 Implement `extract_audio` in `src/semsearch/extractors/media.py` with 16 kHz mono WAV conversion, 30s window / 25s hop, and RMS silence filter (> 0.003), verifying chunk generation and silence rejection.
- [x] 5.2 Implement video frame sampling (3s interval) and 8x8 average perceptual hash deduplication (Hamming distance <= 4 dropped) in `src/semsearch/extractors/media.py`, verifying shot deduplication and keyframe outputs.
- [x] 5.3 Implement unit tests in `tests/test_extractors_media.py` and verify with `.venv/bin/pytest tests/test_extractors_media.py`.

## 6. Code and Notebook Extractor

- [x] 6.1 Implement `extract_code` in `src/semsearch/extractors/code.py` with whole-line chunking (~1800 characters) and definition identifier line detection, verifying line numbers and non-broken syntax lines.
- [x] 6.2 Implement `extract_notebook` in `src/semsearch/extractors/code.py` parsing `.ipynb` files, stripping outputs and metadata, and chunking source cells, verifying clean extraction.
- [x] 6.3 Implement unit tests in `tests/test_extractors_code.py` and verify with `.venv/bin/pytest tests/test_extractors_code.py`.

## 7. Verification & OpenSpec Validation

- [x] 7.1 Validate OpenSpec change proposal with `openspec validate phase-2-ollama-extractors --strict`.
- [x] 7.2 Run the full test suite with `.venv/bin/pytest -v` ensuring zero regressions.
