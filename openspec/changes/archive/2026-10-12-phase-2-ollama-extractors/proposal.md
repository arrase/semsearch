# Proposal

## Why

SemSearch requires an inference engine integration and multimodal extractors to generate semantic embeddings from diverse desktop files. By connecting to Ollama's local API and implementing dedicated extractors for images, screenshots, PDFs, documents, audio, video, and source code, SemSearch enables fast, privacy-preserving semantic search across all local file types without relying on external cloud services or slow, error-prone optical character recognition (OCR).

## What Changes

- Implement `src/semsearch/core/ollama.py` containing `OllamaClient` to interact with Ollama's `/api/tags`, `/api/show`, and `/api/embed` endpoints.
- Add multimodal model capability inspection to filter and restrict model selection strictly to models supporting multimodal embeddings, defaulting to `embeddinggemma-2`.
- Implement batch embedding requests supporting text strings, image base64 payloads, and audio base64 payloads with timeout management and chunking.
- Implement `src/semsearch/extractors/images.py` with Pillow-based downscaling (1024px images, 1536px screenshots), EXIF orientation normalization, and direct base64 encoding with strictly zero OCR.
- Implement `src/semsearch/extractors/pdfs.py` using PyMuPDF with a two-stage page strategy (pages 1–30 in pass 1, remaining pages deferred). Digital text is extracted for pages with at least 200 characters; pages with fewer than 200 characters are rendered to 1400px images and embedded directly as visual inputs with strictly zero OCR.
- Implement `src/semsearch/extractors/docs.py` supporting plain text, Markdown, HTML (tag stripped), DOCX, ODT, and RTF, using an 1800-character window with 200-character overlap snapped to word boundaries and a quality filter requiring at least 20% alphabetic letters.
- Implement `src/semsearch/extractors/media.py` for audio (16 kHz mono WAV, 30s window, 25s hop, RMS silence filter > 0.003) and video (3s frame sampling, 8x8 average perceptual hash deduplication dropping frames with Hamming distance <= 4, keyframe images, and audio soundtrack processing).
- Implement `src/semsearch/extractors/code.py` using whole-line syntax stretches (~1800 characters), definition identifier detection for search navigation, and Jupyter notebook output stripping.
- Add comprehensive test suites for the Ollama client and all extractor modules.

## Capabilities

### New Capabilities
- `ollama-client`: Client interface for Ollama model capability inspection, multimodal model filtering, and batch embedding generation across text, image, and audio inputs.
- `multimodal-extractors`: Content extractors for images, screenshots, PDFs, office documents, audio, video, and source code with zero OCR.

### Modified Capabilities
None.

## Impact

- Introduces `src/semsearch/core/ollama.py` and extractor implementations in `src/semsearch/extractors/`.
- Uses dependencies `httpx`, `pillow`, `pymupdf`, `python-docx`, and standard library utilities (`wave`, `html.parser`).
- External OCR tools (Tesseract, EasyOCR) are strictly prohibited; visual inputs are embedded directly into multimodal models.
- Downstream indexing services and background workers in subsequent phases will consume these extractors and client.
