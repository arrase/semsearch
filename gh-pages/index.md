# SemSearch

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Linux%20Desktop-important.svg)](#)
[![PySide6](https://img.shields.io/badge/GUI-PySide6-green.svg)](https://doc.qt.io/qtforpython-6/)
[![Ollama](https://img.shields.io/badge/Embeddings-Ollama%20v0.40.4%2B-black.svg)](https://ollama.com/)
[![Model](https://img.shields.io/badge/Default%20Model-embeddinggemma--2-purple.svg)](https://ollama.com/)
[![SQLite FTS5](https://img.shields.io/badge/Index-SQLite%20WAL%20%2B%20FTS5-003B57.svg)](https://www.sqlite.org/)

**Local Multimodal Semantic Search Engine for the Linux Desktop**

**SemSearch** is a fast, 100% local, keyboard-driven multimodal semantic search engine for Linux. Find documents, PDFs, screenshots, photos, audio recordings, videos, and source code by meaning—not just by filename—using unified multimodal embeddings powered by Ollama.

!!! warning "Ollama v0.40.4+ Required"
    SemSearch relies on Ollama's native multimodal `/api/embed` endpoint introduced in `v0.40.4+` to generate unified embeddings across text, images, and audio. Make sure your local Ollama installation is updated before running SemSearch.

---

## Key Features

<div class="projects-grid">
  <div class="feature-card">
    <i class="fa-solid fa-wand-magic-sparkles feature-icon"></i>
    <h3>True Cross-Modal Search</h3>
    <p>Type <code>"black hole"</code> and immediately find screenshots or downloaded pictures of a black hole—even when the file is named <code>Captura de pantalla_20260927.png</code> with zero matching text in the filename.</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-brain feature-icon"></i>
    <h3>Multimodal Ollama Embeddings</h3>
    <p>Uses <code>embeddinggemma-2</code> by default via Ollama and supports any verified multimodal embedding model with configurable context window validation (<code>num_ctx</code>).</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-scale-balanced feature-icon"></i>
    <h3>Hybrid Lexical + Semantic Ranking</h3>
    <p>Combines instant SQLite FTS5 BM25 keyword search with vector cosine similarity and modality-aware Z-score normalization so exact matches and conceptual hits rank naturally.</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-bolt feature-icon"></i>
    <h3>Spotlight-Style Quick Panel</h3>
    <p>Summon a floating search bar from anywhere on your desktop with <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Space</kbd> (or <code>semsearch --panel</code>) for instant keyboard-first navigation and live previews.</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-table-cells-large feature-icon"></i>
    <h3>Full Desktop Search Window</h3>
    <p>Browse visual results in a responsive thumbnail card grid, filter by category, inspect interactive PDF/media/code previews, and drag-and-drop cards into other desktop apps.</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-code feature-icon"></i>
    <h3>Developer Source Code Search</h3>
    <p>Prefix any query with <code>code:</code> (or switch to the <strong>Code</strong> tab) for symbol-aware retrieval across 60+ languages and jump straight to the matching line in VS Code, Cursor, Zed, Sublime, or JetBrains IDEs.</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-shield-halved feature-icon"></i>
    <h3>100% Private &amp; Offline</h3>
    <p>Every extraction, float16 vector, and SQLite index stays strictly on your machine (<code>~/.local/share/semsearch/</code>). Secrets (<code>.env</code>, SSH keys, <code>.pem</code>, <code>.kdbx</code>) are automatically excluded.</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-battery-half feature-icon"></i>
    <h3>Smart Background Indexing</h3>
    <p>Differential <code>inotify</code> filesystem watcher, 4-tier priority queue, AC/battery power policies via <code>/sys/class/power_supply</code>, and scheduled indexing windows.</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-language feature-icon"></i>
    <h3>23 Languages &amp; RTL Support</h3>
    <p>Built-in localization for 23 languages with automatic system locale detection and full Right-to-Left (RTL) layout adaptation for Arabic, Persian, and Hebrew.</p>
  </div>
</div>

---

## Interface Gallery

### Floating Quick Search Panel (`Ctrl+Shift+Space`)
Instant pop-up launcher with grouped results and side-by-side live preview:

![SemSearch Quick Search Panel](screenshots/quick-panel.png)

### Semantic Source Code Search (`code:`)
Search functions, classes, and implementation logic by concept with line-level preview and one-click IDE opening:

![SemSearch Code Search](screenshots/code-search.png)

### Multimodal Model & Storage Settings
Connect to any local or remote Ollama instance, switch between verified multimodal embedding models, configure the context window size (`num_ctx`) validated against your model's maximum capacity, and manage your local index:

![SemSearch Settings Dialog](screenshots/settings.png)

---

## Documentation Overview

<div class="projects-grid">
  <a href="architecture.md" class="feature-card">
    <i class="fa-solid fa-sitemap feature-icon"></i>
    <h3>Architecture &amp; Design</h3>
    <p>Explore the single-instance PySide6 daemon, <code>QLocalServer</code> IPC, dual SQLite WAL storage, 4-tier priority indexer, and differential <code>inotify</code> watcher.</p>
  </a>

  <a href="search-engine.md" class="feature-card">
    <i class="fa-solid fa-magnifying-glass-chart feature-icon"></i>
    <h3>Hybrid Search &amp; Ranking</h3>
    <p>Deep dive into dual-query routing, modality-aware Z-score normalization, FTS5 keyword coverage, symbol definition scoring, and quality gating.</p>
  </a>

  <a href="extractors.md" class="feature-card">
    <i class="fa-solid fa-layer-group feature-icon"></i>
    <h3>Multimodal Extractors</h3>
    <p>Learn how documents, two-stage PDFs, EXIF-normalized images, FFmpeg audio windows, perceptual-hash video frames, and source code are segmented without OCR.</p>
  </a>

  <a href="interfaces.md" class="feature-card">
    <i class="fa-solid fa-desktop feature-icon"></i>
    <h3>Desktop UI &amp; Shortcuts</h3>
    <p>Guide to the Quick Search Panel, Full Search Window, dynamic system tray icon, interactive previews, drag-and-drop, and deep-link desktop actions.</p>
  </a>

  <a href="configuration.md" class="feature-card">
    <i class="fa-solid fa-sliders feature-icon"></i>
    <h3>Configuration &amp; Models</h3>
    <p>Reference for the 4-tab Settings dialog, 5-step Onboarding Wizard, Ollama multimodal model verification, <code>config.json</code> schema, and 23-language i18n.</p>
  </a>

  <a href="installation.md" class="feature-card">
    <i class="fa-solid fa-download feature-icon"></i>
    <h3>Installation &amp; CLI Setup</h3>
    <p>Step-by-step instructions for installing Ollama, pulling <code>embeddinggemma-2</code>, installing SemSearch via <code>uv</code> or <code>pipx</code>, and CLI usage.</p>
  </a>
</div>
