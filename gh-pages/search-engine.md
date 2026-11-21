# Hybrid Search & Ranking Engine

SemSearch implements a hybrid retrieval and ranking pipeline in `SearchService` (`semsearch.services.searcher`) combining **inline filter parsing**, **RAM-cached multimodal vector similarity**, **SQLite FTS5 BM25 keyword ranking**, **modality-aware Z-score calibration**, and **symbol-aware code resolution**.

---

## 1. Inline Filters, Dual-Index Routing & Task Prompts

Before executing lexical or semantic search, `parse_query_filters()` (`core/search_text.py`) parses the raw query string into an immutable `ParsedQuery`:

| Filter | Syntax Examples | Behavior |
| :--- | :--- | :--- |
| **`code:`** | `code: OllamaClient`, `parse_query code:` | Routes exclusively to `code.sqlite` (general queries target `index.sqlite`). |
| **`kind:`** | `kind:pdf`, `kind:image,screenshot` | Restricts results by `FileKind` aliases: `pdf`, `doc`/`docs`, `image`/`img`/`photo`, `screenshot`/`shot`, `audio`, `video`, `code`. |
| **`ext:`** | `ext:rs`, `ext:.md,.txt` | Filters by comma-separated lowercase file extensions. |
| **`path:`** | `path:invoices`, `path:"tax 2026"` | Repeatable case-insensitive path substring filter (supports single/double quotes for spaces). |
| **`after:`** | `after:7d`, `after:2w`, `after:6m`, `after:2026-01-01` | Filters files with `mtime` on or after a relative duration (`d` days, `w` weeks, `m` 30-day months) or UTC ISO date (`YYYY-MM-DD`). |

* **Pure-Filter Fast Path**: When a query contains only inline filters (`clean_query == ""`, e.g., `kind:pdf after:7d`), `SearchService` skips `_vector_search()` (zero Ollama requests) and returns matching files sorted by modification time descending.
* **Instant Lexical API**: `SearchService.lexical_search(query, limit=50)` runs SQLite FTS5 matching with full inline filter support and zero Ollama calls—used by the Quick Search Panel for instant per-keystroke feedback.

### Asymmetric Embedding Task Prompts
For semantic queries and segment indexing, `core/ollama.py` formats prompts for asymmetric retrieval models like `embeddinggemma-2`:

| Operation | Prompt Template |
| :--- | :--- |
| **General Search Query** | `task: search result \| query: {query}` |
| **Source Code Query** | `task: code retrieval \| query: {query}` |
| **Document / Code Segment Indexing** | `title: {title} \| text: {segment_text}` |

---

## 2. RAM `VectorSnapshot` & Modality-Aware Z-Score Normalization

Raw cosine similarity distributions differ substantially across modalities—a text-to-image or text-to-audio cosine score is naturally lower and narrower than a text-to-text cosine score even for a strong conceptual match. SemSearch calibrates scores per modality in `semsearch.core.vectors`:

1. **Zero-Copy Unit Cosine Similarity (`cosine_similarity_unit`)**: Stored `float16` vectors are already L2-normalized at index time and cached in RAM as a read-only `float32` `VectorSnapshot.matrix` (with dynamic dimension inference). Search L2-normalizes only the 1D query vector and computes dot products directly without copying the matrix or recomputing document norms:
   $$\cos(\mathbf{q}, \mathbf{d}_i) = \mathbf{d}_i \cdot \frac{\mathbf{q}}{\|\mathbf{q}\|_2}$$
2. **Vectorized Modality Centering ($O(N)$ Trimmed Mean)**: `z_score_normalize()` operates over compact `uint8` `modality_codes` (`Modality.TEXT`, `Modality.IMAGE`, `Modality.AUDIO`) and `scale_mask` (marking only the first segment per `(file_id, modality)` so huge multi-segment files do not dominate calibration). For each modality bucket with $\ge 20$ scale-eligible segments, the baseline mean $\mu_m$ is computed via a symmetric 10% trimmed mean using `np.partition` in $O(N)$ (fallback: global scale-eligible trimmed mean):
   $$c_i = \cos(\mathbf{q}, \mathbf{d}_i) - \mu_{m(i)}$$
3. **Background Dispersion ($\sigma$)**: To prevent genuine top hits from inflating variance, the top 15% highest centered scale-eligible scores are trimmed via `np.partition` (when $\ge 10$ scale-eligible segments exist) to form the background set $B$, and true centered variance $\operatorname{Var}(B)$ is floored at $\sigma \ge 0.045$:
   $$z_i = \frac{c_i}{\max\left(\sqrt{\operatorname{Var}(B)},\; 0.045\right)}$$
4. **File-Level Aggregation**: Each file retains its maximum $z$-score and corresponding best segment across all indexed segments.

---

## 3. Multilingual Lexical FTS5, `camelCase` & CJK Support

In parallel with vector search, `core/search_text.py` and `core/code_lookup.py` power lexical matching and code intelligence:

* **`camelCase` ↔ `snake_case` Cross-Matching**: `split_camel_case()` splits `camelCase` and `PascalCase` boundaries during both indexing (`normalize_search_text`) and querying (`extract_terms`, `extract_identifier`), so searching `OllamaClient` matches `ollama_client` and vice versa (`["ollama", "client"]`).
* **Qualified Symbols & Modern JS/TS Definitions**: `find_definition()` supports qualified lookups (`Class.method`, `mod::fn`, `obj->prop`, `mod#sym`) by checking exact definitions first and falling back to the leaf symbol definition, and recognizes `const` / `let` / `var` / `val` arrow functions and function expressions alongside `def`, `class`, `fn`, `func`, `struct`, `interface`, `trait`, `enum`, etc.
* **CJK Tokenization**: Characters in CJK Unified Ideographs, Extension A, Compatibility Ideographs, Hiragana, Katakana, and Hangul ranges are expanded into unigrams and bigrams at index time and tokenized into bigrams/single characters at query time without requiring whitespace word boundaries.
* **Extended Latin Folding & Multilingual Hints**: Folds extended Latin characters (`ł → l`, `ø → o`, `ß → ss`, `đ → d`, `æ → ae`, `œ → oe`) plus NFD combining marks, strips stopwords across English, Spanish (including `code`/`codigo`/`código`), Bengali, and Arabic, and recognizes soft category kind hints (`extract_kind_hints`).
* **Safe FTS5 Quoting & Prefix Expansion**: All double quotes inside FTS5 terms are escaped (`"` → `""`), eliminating syntax errors without silent fallbacks. Unindexed terms with $\ge 3$ characters (or the trailing term when `prefix_last=True`) receive a trailing prefix wildcard (`"term"*`).
* **Term Coverage (`compute_coverage`)**: Computes the fraction of query terms present across each candidate file's segments ($\text{cov}_f \in [0.0, 1.0]$).

---

## 4. Quality Gating, Hybrid BM25 Fusion & Deferred Snippets

A candidate file is admitted if and only if it passes at least one quality gate:

* **Semantic Gate**: Best segment Z-score $z_f \ge 2.2$ (`SEMANTIC_Z_GATE`), **or**
* **Lexical Gate**: Query term coverage $\text{cov}_f \ge 0.5$, **or**
* **Pure Kind / Filter Query**: Pure category hint query (e.g., `"screenshots"`) or pure inline-filter query (e.g., `kind:pdf after:7d`).

### Hybrid Scoring Formula
For every admitted candidate file $f$, `SearchService._score_candidates()` computes:

$$\text{Score}(f) = w_{\text{meaning}} \cdot \max(z_f, 0) + w_{\text{words}} \cdot (\text{cov}_f)^{1.5} + B_{\text{bm25}} + B_{\text{kind}} + B_{\text{name}} + B_{\text{def}}$$

| Component | Formula / Value | Description |
| :--- | :--- | :--- |
| $w_{\text{meaning}}$ | `0.5` (lookup) / `1.0` (semantic) | Weight applied to the modality-normalized Z-score. |
| $w_{\text{words}}$ | `4.0` (general lookup) / `2.0` (general semantic or code lookup) / `1.0` (code semantic) | Weight applied to non-linear lexical coverage $(\text{cov}_f)^{1.5}$. |
| $B_{\text{bm25}}$ | $\dfrac{0.5 \cdot w_{\text{words}} \cdot (\text{cov}_f)^{1.5}}{1 + (\text{rank}_{\text{bm25}} - 1) / 10}$ | Integrates SQLite FTS5 `bm25(fts, 10.0, 1.0)` rank directly into hybrid scoring so dense, concise, and filename-matched documents outrank incidental mentions in huge files. |
| $B_{\text{kind}}$ | `+1.0` | Applied when the file's `FileKind` matches a category hint in the query. |
| $B_{\text{name}}$ | $3.0 \times (\text{match\_ratio})^{1.5}$ (up to `+3.0`) | Applied when $\text{cov}_f \ge 0.5$ and folded query terms match the filename stem. |
| $B_{\text{def}}$ | `+3.0` (strong) / `+1.5` (weak) | Applied in code search when `find_definition()` locates a symbol definition matching the query. |

---

## 5. Batch Retrieval, Line Anchoring & Result Folding

* **Zero N+1 Database Queries**: Candidate `FileRecord` rows and `SegmentMeta` rows (including persisted `focus`) are fetched in chunked batch queries (`find_by_ids()`, `get_segments_by_ids()`), and Python `extract_snippet()` highlighting is deferred exclusively to final primary `ranked` hits rather than all raw FTS matches.
* **Code Line Jump (`_resolve_code_place`)**: Locates the exact 1-based line number of a matched function, class, or symbol definition and sets `hit.focus` so opening the result jumps your IDE directly to that line.
* **Primary vs. Weaker Folding (`ResultSet`)**: Hits are sorted by `(score, mtime)` descending. Hits with $\text{score} \ge 0.5 \times \text{top\_score}$ populate `result_set.ranked` (up to `limit`), while lower-scoring hits are folded into `result_set.weaker`.
* **Pure Domain Match Reasons**: `SearchHit.reason` returns a pure `MatchReason` enum (`MEANING` if $z \ge 2.2$, `WORDS` if `keyword_rank > 0`, else `NAME`) translated strictly at the UI/CLI boundary.
