---
hide:
  - toc
---

<div class="hero-section">
  <div class="hero-eyebrow">
    <i class="fa-solid fa-magnifying-glass-chart" aria-hidden="true"></i>
    <span>Local Multimodal Semantic Search</span>
  </div>
  <h1>SemSearch</h1>
  <p class="hero-subtitle">A fast, 100% local, keyboard-driven multimodal semantic search engine for the Linux desktop. Find documents, PDFs, screenshots, photos, audio recordings, videos, and source code by meaning using unified Ollama embeddings.</p>
  <div class="hero-badges">
    <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+"></a>
    <a href="#"><img src="https://img.shields.io/badge/platform-Linux%20Desktop-important.svg" alt="Platform"></a>
    <a href="https://doc.qt.io/qtforpython-6/"><img src="https://img.shields.io/badge/GUI-PySide6-green.svg" alt="PySide6"></a>
    <a href="https://ollama.com/"><img src="https://img.shields.io/badge/Embeddings-Ollama%20v0.40.4%2B-black.svg" alt="Ollama"></a>
    <a href="https://ollama.com/"><img src="https://img.shields.io/badge/Default%20Model-embeddinggemma--2-purple.svg" alt="Model"></a>
    <a href="https://www.sqlite.org/"><img src="https://img.shields.io/badge/Index-SQLite%20WAL%20%2B%20FTS5-003B57.svg" alt="SQLite FTS5"></a>
  </div>
</div>

!!! warning "Ollama v0.40.4+ Required"
    SemSearch relies on Ollama's native multimodal `/api/embed` endpoint introduced in `v0.40.4+` to generate unified embeddings across text, images, and audio. Make sure your local Ollama installation is updated before running SemSearch.

<div class="section-header">
  <h2>Key Features</h2>
  <span>Cross-modal retrieval &amp; instant desktop previews</span>
</div>

<div class="projects-grid">
  <div class="feature-card">
    <i class="fa-solid fa-wand-magic-sparkles feature-icon" aria-hidden="true"></i>
    <h3>True Cross-Modal Search</h3>
    <p>Type <code>"black hole"</code> and immediately find screenshots or downloaded pictures of a black hole—even when the file is named <code>Captura de pantalla_20260927.png</code> with zero matching text in the filename.</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-brain feature-icon" aria-hidden="true"></i>
    <h3>Multimodal Ollama Embeddings</h3>
    <p>Uses <code>embeddinggemma-2</code> by default via Ollama and supports any verified multimodal embedding model with configurable context window validation (<code>num_ctx</code>).</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-scale-balanced feature-icon" aria-hidden="true"></i>
    <h3>Hybrid Lexical + Semantic Ranking</h3>
    <p>Combines instant SQLite FTS5 BM25 keyword search with vector cosine similarity and modality-aware Z-score normalization so exact matches and conceptual hits rank naturally.</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-bolt feature-icon" aria-hidden="true"></i>
    <h3>Spotlight-Style Quick Panel</h3>
    <p>Summon a floating search bar from anywhere on your desktop with <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Space</kbd> (or <code>semsearch --panel</code>) for instant keyboard-first navigation and live previews.</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-eye feature-icon" aria-hidden="true"></i>
    <h3>Interactive Multimodal Previews</h3>
    <p>Inspect PDF pages with yellow query highlights, high-DPI images, FFmpeg video keyframes, audio waveform scrubbers, and line/keyword-highlighted source code &amp; documents right inside the panel.</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-code feature-icon" aria-hidden="true"></i>
    <h3>Developer Source Code Search</h3>
    <p>Prefix any query with <code>code:</code> for symbol-aware retrieval across 60+ languages and jump straight to the matching line in VS Code, Cursor, Zed, Sublime, or JetBrains IDEs.</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-shield-halved feature-icon" aria-hidden="true"></i>
    <h3>100% Private &amp; Offline</h3>
    <p>Every extraction, float16 vector, and SQLite index stays strictly on your machine (<code>~/.local/share/semsearch/</code>). Secrets (<code>.env</code>, SSH keys, <code>.pem</code>, <code>.kdbx</code>) are automatically excluded.</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-battery-half feature-icon" aria-hidden="true"></i>
    <h3>Smart Background Indexing</h3>
    <p>Differential <code>inotify</code> filesystem watcher, 4-tier priority queue, AC/battery power policies via <code>/sys/class/power_supply</code>, and scheduled indexing windows.</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-language feature-icon" aria-hidden="true"></i>
    <h3>23 Languages &amp; RTL Support</h3>
    <p>Built-in localization for 23 languages with automatic system locale detection and full Right-to-Left (RTL) layout adaptation for Arabic, Persian, and Hebrew.</p>
  </div>
</div>

<div class="section-header">
  <h2>Interface Gallery</h2>
  <span>Quick search panel, code search &amp; model settings</span>
</div>

### Floating Quick Search Panel (`Ctrl+Shift+Space`)
Instant pop-up launcher with grouped results and side-by-side live preview:

![SemSearch Quick Search Panel](screenshots/quick-panel.png)

### Semantic Source Code Search (`code:`)
Search functions, classes, and implementation logic by concept with line-level preview and one-click IDE opening:

![SemSearch Code Search](screenshots/code-search.png)

### Multimodal Model & Storage Settings
Connect to any local or remote Ollama instance, switch between verified multimodal embedding models, configure the context window size (`num_ctx`) validated against your model's maximum capacity, and manage your local index:

![SemSearch Settings Dialog](screenshots/settings.png)

<div class="section-header">
  <h2>Documentation</h2>
  <span>Deep dive into architecture, ranking &amp; extractors</span>
</div>

<div class="projects-grid">
  <a href="architecture/" class="feature-card">
    <div class="card-top">
      <i class="fa-solid fa-sitemap feature-icon" aria-hidden="true"></i>
      <span class="card-badge">Read →</span>
    </div>
    <h3>Architecture &amp; Design</h3>
    <p>Explore the single-instance PySide6 daemon, <code>QLocalServer</code> IPC, dual SQLite WAL storage, 4-tier priority indexer, and differential <code>inotify</code> watcher.</p>
  </a>

  <a href="search-engine/" class="feature-card">
    <div class="card-top">
      <i class="fa-solid fa-magnifying-glass-chart feature-icon" aria-hidden="true"></i>
      <span class="card-badge">Read →</span>
    </div>
    <h3>Hybrid Search &amp; Ranking</h3>
    <p>Deep dive into dual-query routing, modality-aware Z-score normalization, FTS5 keyword coverage, symbol definition scoring, and quality gating.</p>
  </a>

  <a href="extractors/" class="feature-card">
    <div class="card-top">
      <i class="fa-solid fa-layer-group feature-icon" aria-hidden="true"></i>
      <span class="card-badge">Read →</span>
    </div>
    <h3>Multimodal Extractors</h3>
    <p>Learn how documents, two-stage PDFs, EXIF-normalized images, FFmpeg audio windows, perceptual-hash video frames, and source code are segmented without OCR.</p>
  </a>

  <a href="interfaces/" class="feature-card">
    <div class="card-top">
      <i class="fa-solid fa-desktop feature-icon" aria-hidden="true"></i>
      <span class="card-badge">Read →</span>
    </div>
    <h3>Desktop UI &amp; Shortcuts</h3>
    <p>Guide to the Floating Quick Search Panel, dynamic system tray icon, interactive multimodal previews, and deep-link desktop actions.</p>
  </a>

  <a href="configuration/" class="feature-card">
    <div class="card-top">
      <i class="fa-solid fa-sliders feature-icon" aria-hidden="true"></i>
      <span class="card-badge">Read →</span>
    </div>
    <h3>Configuration &amp; Models</h3>
    <p>Reference for the 4-tab Settings dialog, 5-step Onboarding Wizard, Ollama multimodal model verification, <code>config.json</code> schema, and 23-language i18n.</p>
  </a>

  <a href="installation/" class="feature-card">
    <div class="card-top">
      <i class="fa-solid fa-download feature-icon" aria-hidden="true"></i>
      <span class="card-badge">Read →</span>
    </div>
    <h3>Installation &amp; CLI Setup</h3>
    <p>Step-by-step instructions for installing Ollama, pulling <code>embeddinggemma-2</code>, installing SemSearch via <code>uv</code> or <code>pipx</code>, and CLI usage.</p>
  </a>
</div>
