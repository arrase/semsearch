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
  <p class="hero-subtitle">A fast, 100% local, keyboard-driven multimodal semantic search engine for the Linux desktop. Find documents, PDFs, screenshots, photos, SVGs, audio recordings, videos, and source code by meaning using unified Ollama embeddings—with strict Zero-OCR.</p>
  <div class="hero-badges">
    <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+"></a>
    <a href="#"><img src="https://img.shields.io/badge/platform-Linux%20Wayland%20%26%20X11-important.svg" alt="Platform"></a>
    <a href="https://doc.qt.io/qtforpython-6/"><img src="https://img.shields.io/badge/GUI-PySide6-green.svg" alt="PySide6"></a>
    <a href="https://ollama.com/"><img src="https://img.shields.io/badge/Embeddings-Ollama%20v0.40.4%2B-black.svg" alt="Ollama"></a>
    <a href="https://ollama.com/"><img src="https://img.shields.io/badge/Default%20Model-embeddinggemma--2-purple.svg" alt="Model"></a>
    <a href="https://www.sqlite.org/"><img src="https://img.shields.io/badge/Index-SQLite%20WAL%20v6%20%2B%20FTS5-003B57.svg" alt="SQLite FTS5"></a>
  </div>
</div>

!!! warning "Ollama v0.40.4+ Required"
    SemSearch relies on Ollama's native multimodal `/api/embed` endpoint introduced in `v0.40.4+` to generate unified embeddings across text, images, and audio. Make sure your local Ollama installation is updated before running SemSearch.

<div class="section-header">
  <h2>Key Features</h2>
  <span>Cross-modal retrieval, Wayland integration &amp; asynchronous desktop previews</span>
</div>

<div class="projects-grid">
  <div class="feature-card">
    <i class="fa-solid fa-wand-magic-sparkles feature-icon" aria-hidden="true"></i>
    <h3>True Cross-Modal Search (Zero-OCR)</h3>
    <p>Type <code>"black hole"</code> and immediately find screenshots, photos, or <code>.svg</code> diagrams of a black hole—even when named <code>Captura de pantalla_20260927.png</code> with zero matching text in the filename.</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-brain feature-icon" aria-hidden="true"></i>
    <h3>Multimodal Ollama Embeddings</h3>
    <p>Uses <code>embeddinggemma-2</code> by default via a persistent <code>httpx</code> client, strict <code>"embedding"</code> capability verification, VRAM-safe default <code>num_ctx = 0</code>, and automatic model/dimension fingerprinting in <code>meta</code>.</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-scale-balanced feature-icon" aria-hidden="true"></i>
    <h3>Hybrid BM25 + RAM Vector Ranking</h3>
    <p>Combines SQLite FTS5 BM25 rank, coverage, RAM-cached <code>VectorSnapshot</code> similarity, modality-aware Z-scores (<code>Z &gt;= 2.2</code>), <code>camelCase</code>/CJK support, and inline filters (<code>kind:</code>, <code>ext:</code>, <code>path:</code>, <code>after:</code>).</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-bolt feature-icon" aria-hidden="true"></i>
    <h3>Spotlight-Style Quick Panel (748px)</h3>
    <p>Summon anywhere with <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Space</kbd> (Wayland XDG <code>GlobalShortcuts</code> portal or X11) or <code>semsearch --toggle</code>, complete with Drag &amp; Drop and Quick Actions (<kbd>Tab</kbd> / <kbd>Ctrl</kbd>+<kbd>K</kbd>).</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-eye feature-icon" aria-hidden="true"></i>
    <h3>Async Previews &amp; In-Panel Playback</h3>
    <p>Non-blocking <code>gui/preview/</code> loaders with cached video frames, in-panel <code>QMediaPlayer</code> audio playback &amp; real RMS waveforms, HiDPI PDFs (<kbd>Alt</kbd>+<kbd>←</kbd>/<kbd>→</kbd>), animated GIFs/SVGs, and syntax-highlighted code.</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-terminal feature-icon" aria-hidden="true"></i>
    <h3>Composable CLI &amp; KRunner / GNOME</h3>
    <p>Script with <code>semsearch query --json</code>, <code>status</code>, and <code>reindex --force</code>, and integrate natively into <strong>KDE KRunner</strong> and <strong>GNOME Shell</strong> search via <code>semsearch install-desktop</code>.</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-code feature-icon" aria-hidden="true"></i>
    <h3>Developer Source Code Search</h3>
    <p>Prefix queries with <code>code:</code> for <code>.gitignore</code>-aware symbol retrieval across 60+ languages and jump straight to the definition line in VS Code, Cursor, Zed, Sublime, or JetBrains IDEs.</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-shield-halved feature-icon" aria-hidden="true"></i>
    <h3>100% Private, Hardened &amp; Resilient</h3>
    <p>Atomic <code>0600</code> config and IPC sockets, automatic exclusion of <code>.ssh</code>/<code>.aws</code>/<code>.kube</code>/secrets and zip bombs, Ollama outage backoff without marking files failed, and selective non-recursive <code>inotify</code> watches.</p>
  </div>

  <div class="feature-card">
    <i class="fa-solid fa-language feature-icon" aria-hidden="true"></i>
    <h3>23 Languages &amp; RTL Support</h3>
    <p>Lazy-loaded JSON localization catalogs for 23 languages with colon-separated <code>LANGUAGE</code> fallback detection and full Right-to-Left (RTL) layout adaptation for Arabic, Persian, and Hebrew.</p>
  </div>
</div>

<div class="section-header">
  <h2>Interface Gallery</h2>
  <span>Quick search panel, code search &amp; model settings</span>
</div>

### Floating Quick Search Panel (`Ctrl+Shift+Space`)
Instant pop-up launcher (`748px`: `328px` grouped results + `416px` live asynchronous preview):

![SemSearch Quick Search Panel](screenshots/quick-panel.png)

### Semantic Source Code Search (`code:`)
Search functions, classes, and implementation logic by concept with syntax-highlighted line-level preview and one-click IDE opening:

![SemSearch Code Search](screenshots/code-search.png)

### Multimodal Model & Storage Settings
Connect to any local or remote Ollama instance, switch between verified multimodal embedding models, optionally configure `num_ctx`, and manage your local index:

![SemSearch Settings Dialog](screenshots/settings.png)

<div class="section-header">
  <h2>Documentation</h2>
  <span>Deep dive into architecture, ranking, extractors &amp; desktop integration</span>
</div>

<div class="projects-grid">
  <a href="architecture/" class="feature-card">
    <div class="card-top">
      <i class="fa-solid fa-sitemap feature-icon" aria-hidden="true"></i>
      <span class="card-badge">Read →</span>
    </div>
    <h3>Architecture &amp; Design</h3>
    <p>Explore the single-instance daemon with <code>flock</code> + <code>AF_UNIX</code> IPC, SQLite WAL v6 thread-local readers, RAM <code>VectorSnapshot</code> cache, Ollama backoff resilience, and selective <code>inotify</code> watcher.</p>
  </a>

  <a href="search-engine/" class="feature-card">
    <div class="card-top">
      <i class="fa-solid fa-magnifying-glass-chart feature-icon" aria-hidden="true"></i>
      <span class="card-badge">Read →</span>
    </div>
    <h3>Hybrid Search &amp; Ranking</h3>
    <p>Deep dive into inline filter parsing, BM25 + coverage + modality-aware Z-score fusion (<code>Z &gt;= 2.2</code>), <code>camelCase</code>/CJK tokenization, qualified symbol resolution, and batch retrieval.</p>
  </a>

  <a href="extractors/" class="feature-card">
    <div class="card-top">
      <i class="fa-solid fa-layer-group feature-icon" aria-hidden="true"></i>
      <span class="card-badge">Read →</span>
    </div>
    <h3>Multimodal Extractors</h3>
    <p>Learn how documents (with zip-bomb guards), two-stage PDFs, raster &amp; PyMuPDF-rasterized <code>.svg</code> images, bounded FFmpeg media, and code/notebooks are extracted with strict Zero-OCR.</p>
  </a>

  <a href="interfaces/" class="feature-card">
    <div class="card-top">
      <i class="fa-solid fa-desktop feature-icon" aria-hidden="true"></i>
      <span class="card-badge">Read →</span>
    </div>
    <h3>Desktop UI &amp; Shortcuts</h3>
    <p>Guide to the <code>748px</code> Floating Quick Search Panel, event-driven system tray, modular async <code>gui/preview/</code> renderers, Drag &amp; Drop, Quick Actions, and KRunner / GNOME D-Bus providers.</p>
  </a>

  <a href="configuration/" class="feature-card">
    <div class="card-top">
      <i class="fa-solid fa-sliders feature-icon" aria-hidden="true"></i>
      <span class="card-badge">Read →</span>
    </div>
    <h3>Configuration &amp; Models</h3>
    <p>Reference for the 4-tab Settings dialog, XDG Onboarding Wizard, strict multimodal model verification, VRAM-safe <code>num_ctx</code>, atomic <code>config.json</code>, and lazy JSON i18n.</p>
  </a>

  <a href="installation/" class="feature-card">
    <div class="card-top">
      <i class="fa-solid fa-download feature-icon" aria-hidden="true"></i>
      <span class="card-badge">Read →</span>
    </div>
    <h3>Installation &amp; CLI Setup</h3>
    <p>Step-by-step instructions for Ollama, installing SemSearch, headless CLI subcommands, Wayland XDG <code>GlobalShortcuts</code> &amp; <code>--toggle</code>, and KRunner / GNOME provider setup.</p>
  </a>
</div>
