# Hybrid Search & Ranking Engine

SemSearch implements a hybrid retrieval and ranking pipeline in `SearchService` (`semsearch.services.searcher`) that combines **multimodal vector similarity** with **SQLite FTS5 lexical matching**, **modality-aware Z-score calibration**, and **symbol-aware code resolution**.

---

## 1. Dual-Query Routing & Task Prompts

Every query is first inspected for target index routing:

* **General Search (`index.sqlite`)**: Default mode for documents, PDFs, images, screenshots, audio, and video.
* **Source Code Search (`code.sqlite`)**: Activated automatically when the query begins with the `code:` prefix (e.g., `code: z_score_normalize`).

To align with asymmetric retrieval models like `embeddinggemma-2`, SemSearch formats text inputs with task-specific prefixes before calling Ollama's `/api/embed` endpoint:

| Operation | Prompt Template |
| :--- | :--- |
| **General Search Query** | `task: search result \| query: {query}` |
| **Source Code Query** | `task: code retrieval \| query: {query}` |
| **Document / Code Segment Indexing** | `title: {title} \| text: {segment_text}` |

---

## 2. Modality-Aware Z-Score Normalization

Raw cosine similarity distributions differ substantially across modalities—a text-to-image or text-to-audio cosine score is naturally lower and narrower than a text-to-text cosine score even for an exact conceptual match. Comparing raw cosines directly would cause text documents to drown out images, screenshots, and audio clips.

SemSearch solves this in `semsearch.core.vectors.z_score_normalize()` by calibrating scores per modality (`Modality.TEXT`, `Modality.IMAGE`, `Modality.AUDIO`):

1. **Cosine Similarity**: All stored `float16` segment vectors are loaded into a contiguous matrix and compared against the query vector in `float32`:
   $$\cos(\mathbf{q}, \mathbf{d}_i) = \frac{\mathbf{q} \cdot \mathbf{d}_i}{\|\mathbf{q}\|_2 \|\mathbf{d}_i\|_2}$$
2. **Modality Centering (10% Trimmed Mean)**: Scale-eligible segments (`sets_scale = True`, i.e., `SegmentKind != NAME`) are grouped by modality. If a modality bucket has at least 20 scale-eligible segments, its baseline mean $\mu_m$ is computed using a symmetric 10% trimmed mean (fallback: global trimmed mean across all scale-eligible segments):
   $$c_i = \cos(\mathbf{q}, \mathbf{d}_i) - \mu_{m(i)}$$
3. **Background Dispersion ($\sigma$)**: To prevent genuine top hits from inflating the standard deviation, the top 15% highest centered scale-eligible scores are excluded when computing the background variance (when $\ge 10$ scale-eligible segments exist), with a minimum floor of $\sigma \ge 0.045$:
   $$z_i = \frac{c_i}{\max\left(\sqrt{\frac{1}{|B|}\sum_{j \in B} c_j^2},\; 0.045\right)}$$
4. **File-Level Aggregation**: Each file retains the maximum $z$-score and corresponding best segment across all of its indexed segments.

---

## 3. Lexical FTS5 Search & Query Parsing

In parallel with vector search, `semsearch.core.search_text` parses the query for lexical retrieval:

* **Kind Hints (`extract_kind_hints`)**: Natural-language category words in the query (such as `pdf`, `pdfs`, `screenshot`, `screenshots`, `photo`, `image`, `audio`, `podcast`, `video`, `doc`, `document`) are recognized as `FileKind` hints. If a query consists solely of category words (e.g., `"screenshots"`), SemSearch executes a pure kind filter.
* **Stopword & Term Extraction (`extract_terms`)**: Extracts meaningful search tokens while stripping conversational filler words.
* **Identifier Detection (`extract_identifier`)**: Recognizes `camelCase`, `PascalCase`, `snake_case`, and `dotted.identifiers` in code queries to construct exact phrase matches.
* **Automatic Prefix Expansion**: For terms with $\ge 3$ characters that do not appear verbatim in the FTS vocabulary (`db.is_indexed(term)`), SemSearch automatically appends a prefix wildcard (`term*`) so partial words still match as you type.
* **Term Coverage (`compute_coverage`)**: Computes the fraction of query terms present in each candidate file ($\text{cov} \in [0.0, 1.0]$).

---

## 4. Quality Gating & Score Fusion

A file is admitted to the candidate set if and only if it passes at least one quality gate:

* **Semantic Gate**: Best segment Z-score $z \ge 2.2$ (`SEMANTIC_Z_GATE`), **or**
* **Lexical Gate**: Query term coverage $\text{cov} \ge 0.5$ (at least half of the query terms appear in the file), **or**
* **Pure Kind Query**: The user searched purely for a file category (e.g., `"pdfs"`).

### Hybrid Scoring Formula
For every candidate file $f$, the fused score is computed as:

$$\text{Score}(f) = w_{\text{meaning}} \cdot \max(z_f, 0) + w_{\text{words}} \cdot (\text{cov}_f)^{1.5} + B_{\text{kind}} + B_{\text{name}} + B_{\text{def}}$$

| Component | Value | Description |
| :--- | :--- | :--- |
| $w_{\text{meaning}}$ | `0.5` (lookup) / `1.0` (semantic) | Weight applied to the modality-normalized Z-score. |
| $w_{\text{words}}$ | `4.0` (general lookup) / `2.0` (general semantic or code lookup) / `1.0` (code semantic) | Weight applied to non-linear lexical coverage $(\text{cov}_f)^{1.5}$. |
| $B_{\text{kind}}$ | `+1.0` | Applied when the file's `FileKind` matches a category hint in the query. |
| $B_{\text{name}}$ | Up to `+3.0` | Applied when $\text{cov}_f \ge 0.5$ and query terms match the diacritic-folded filename stem: $3.0 \times (\text{match\_ratio})^{1.5}$. |
| $B_{\text{def}}$ | `+3.0` (strong) / `+1.5` (weak) | Applied in code search when `find_definition()` locates a symbol definition (`def`, `class`, `fn`, `struct`, `interface`, etc.) matching the query. |

---

## 5. Symbol Line Resolution & Result Folding

* **Code Line Jump (`_resolve_code_place`)**: For code hits, SemSearch inspects matching segments (and falls back to scanning the source file) using language-aware definition patterns (`semsearch.core.code_lookup.find_definition`). It sets `hit.focus` to the exact 1-based line number of the function, class, or variable definition so opening the result jumps your IDE cursor directly to that line.
* **Primary vs. Weaker Folding (`ResultSet`)**: Once all hits are sorted by `(score, mtime)` descending, hits with $\text{score} \ge 0.5 \times \text{top\_score}$ are placed in `result_set.ranked` (up to `limit = 50`), while lower-scoring hits are separated into `result_set.weaker` to keep the primary UI clean and high-signal.
