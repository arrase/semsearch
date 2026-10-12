"""Search engine with dual-query routing, hybrid DigUp ranking, and desktop gating."""

from dataclasses import dataclass, field
from pathlib import Path
import re
import numpy as np

from semsearch.core.database import Database
from semsearch.core.ollama import OllamaClient
from semsearch.core.types import (
    FileKind,
    ResultRow,
    SearchHit,
    SegmentKind,
)
from semsearch.core.vectors import cosine_similarity, z_score_normalize
from semsearch.extractors.code import DEFINITION_RE

STOP_WORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can't", "cannot", "could", "couldn't",
    "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during",
    "each", "few", "for", "from", "further", "had", "hadn't", "has", "hasn't",
    "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here",
    "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i",
    "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's",
    "its", "itself", "let's", "me", "more", "most", "mustn't", "my", "myself",
    "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other", "ought",
    "our", "ours", "ourselves", "out", "over", "own", "same", "shan't", "she",
    "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such",
    "than", "that", "that's", "the", "their", "theirs", "them", "themselves",
    "then", "there", "there's", "these", "they", "they'd", "they'll", "they're",
    "they've", "this", "those", "through", "to", "too", "under", "until", "up",
    "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were",
    "weren't", "what", "what's", "when", "when's", "where", "where's", "which",
    "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would",
    "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours",
    "yourself", "yourselves",
}

FILE_KIND_TERMS: dict[str, FileKind] = {
    "screenshot": FileKind.SCREENSHOT,
    "screenshots": FileKind.SCREENSHOT,
    "image": FileKind.IMAGE,
    "images": FileKind.IMAGE,
    "photo": FileKind.IMAGE,
    "photos": FileKind.IMAGE,
    "picture": FileKind.IMAGE,
    "pictures": FileKind.IMAGE,
    "pdf": FileKind.PDF,
    "pdfs": FileKind.PDF,
    "doc": FileKind.DOC,
    "docs": FileKind.DOC,
    "document": FileKind.DOC,
    "documents": FileKind.DOC,
    "audio": FileKind.AUDIO,
    "sound": FileKind.AUDIO,
    "music": FileKind.AUDIO,
    "video": FileKind.VIDEO,
    "videos": FileKind.VIDEO,
    "movie": FileKind.VIDEO,
    "code": FileKind.CODE,
}


class Prompts:
    """Ollama prompt formatting templates."""

    @staticmethod
    def query(text: str) -> str:
        """Format general document query prompt."""
        return f"task: search result | query: {text}"

    @staticmethod
    def code_query(text: str) -> str:
        """Format source code retrieval query prompt."""
        return f"task: code retrieval | query: {text}"


@dataclass
class ResultSet:
    """Search results partitioned into primary ranked hits and folded weaker hits."""

    ranked: list[SearchHit] = field(default_factory=list)
    weaker: list[SearchHit] = field(default_factory=list)

    def __iter__(self):
        return iter(self.ranked)

    def __len__(self) -> int:
        return len(self.ranked)

    def __getitem__(self, index: int) -> SearchHit:
        return self.ranked[index]


class SearchService:
    """Hybrid search service combining FTS5 BM25 keyword search and vector similarity."""

    def __init__(
        self,
        db: Database,
        code_db: Database | None = None,
        ollama: OllamaClient | None = None,
        model: str = "embeddinggemma-2",
    ) -> None:
        self.db = db
        self.code_db = code_db
        self.ollama = ollama
        self.model = model

    def is_lookup_query(self, query: str) -> bool:
        """Determine whether the query is an identifier, symbol, or serial lookup."""
        q = query.strip()
        if not q:
            return False
        if re.search(r"[a-z][A-Z]", q):
            return True
        if re.search(r"[a-zA-Z0-9]+_[a-zA-Z0-9]+", q):
            return True
        if "/" in q or "\\" in q or "::" in q or re.search(r"\w+\.\w+", q):
            return True
        if re.search(r"\b[A-Za-z0-9]+-[A-Za-z0-9]+\b", q) or re.search(r"\b0x[0-9a-fA-F]+\b", q):
            return True
        words = re.findall(r"\w+", q)
        if any(re.search(r"[0-9]", w) and re.search(r"[a-zA-Z]", w) for w in words):
            return True
        return False

    def compute_coverage(self, terms: list[str], text: str) -> float:
        """Calculate query term coverage ratio in [0.0, 1.0]."""
        if not terms:
            return 0.0
        text_words = set(re.findall(r"\w+", text.lower()))
        matched = 0
        for idx, term in enumerate(terms):
            is_last = idx == len(terms) - 1
            if term in text_words:
                matched += 1
            elif is_last and any(tw.startswith(term) for tw in text_words):
                matched += 1
        return min(matched / len(terms), 1.0)

    def _normalize_query(self, query: str) -> tuple[list[str], FileKind | None, str]:
        raw_words = re.findall(r"\w+", query.lower())
        target_kind: FileKind | None = None
        for word in raw_words:
            if word in FILE_KIND_TERMS:
                target_kind = FILE_KIND_TERMS[word]
                break

        meaningful_terms = [w for w in raw_words if w not in STOP_WORDS and w not in FILE_KIND_TERMS]
        if not meaningful_terms:
            meaningful_terms = [w for w in raw_words if w not in STOP_WORDS]
        if not meaningful_terms:
            meaningful_terms = raw_words

        fts_query = " ".join(meaningful_terms) if meaningful_terms else query
        return meaningful_terms, target_kind, fts_query

    def _find_code_definition_line(self, path: str, terms: list[str]) -> int:
        target_path = Path(path)
        if not target_path.is_file():
            return 0
        lines = target_path.read_text(encoding="utf-8", errors="replace").splitlines()
        term_set = {t.lower() for t in terms}
        first_def_line = 0
        for idx, line in enumerate(lines, start=1):
            m = DEFINITION_RE.match(line)
            if m:
                if not first_def_line:
                    first_def_line = idx
                symbol_name = m.group(1).lower()
                if symbol_name in term_set:
                    return idx
        return first_def_line

    def search(self, query: str, limit: int = 50) -> ResultSet:
        """Execute hybrid search with dual query routing, Z-score fusion, gating, and folding."""
        stripped = query.strip()
        if not stripped:
            return ResultSet()

        is_code = stripped.startswith("code:")
        if is_code:
            target_db = self.code_db
            clean_query = stripped[5:].strip()
            if target_db is None or not clean_query:
                return ResultSet()
        else:
            target_db = self.db
            clean_query = stripped

        terms, target_kind, fts_query_str = self._normalize_query(clean_query)
        is_lookup = self.is_lookup_query(clean_query)

        # 1. Vector Search
        segment_z_scores: dict[int, tuple[float, float]] = {}
        if self.ollama is not None:
            prompt = Prompts.code_query(clean_query) if is_code else Prompts.query(clean_query)
            try:
                embeddings = self.ollama.embed(self.model, [prompt])
            except Exception:
                embeddings = []

            if embeddings:
                query_vec = np.array(embeddings[0], dtype=np.float32)
                seg_ids, file_ids, modalities, mat = target_db.get_all_vectors()
                if len(seg_ids) > 0 and mat.shape[0] > 0:
                    cosines = cosine_similarity(query_vec, mat)
                    z_scores = z_score_normalize(cosines, modalities)
                    pos_candidates = [
                        (seg_id, float(cos), float(z))
                        for seg_id, cos, z in zip(seg_ids, cosines, z_scores)
                        if z > 0.0
                    ]
                    pos_candidates.sort(key=lambda item: item[2], reverse=True)
                    for seg_id, cos, z in pos_candidates[:200]:
                        segment_z_scores[seg_id] = (cos, z)

        # 2. Keyword Search
        fts_rows = target_db.fts_search(fts_query_str, limit=200) if fts_query_str else []
        fts_by_seg: dict[int, dict] = {row["segment_id"]: row for row in fts_rows}

        candidate_segment_ids = set(segment_z_scores.keys()) | set(fts_by_seg.keys())
        if not candidate_segment_ids:
            return ResultSet()

        # Batch query segment and file records
        placeholders = ",".join("?" for _ in candidate_segment_ids)
        cursor = target_db._conn.cursor()
        cursor.execute(
            f"""
            SELECT s.id, s.file, s.kind, s.loc, s.loc_end, s.excerpt,
                   f.path, f.kind, f.mtime, f.size, f.info
            FROM segments s
            JOIN files f ON f.id = s.file
            WHERE s.id IN ({placeholders})
            """,
            list(candidate_segment_ids),
        )
        records = cursor.fetchall()

        # 3. Weights calculation
        if is_code:
            w_meaning = 0.5 if is_lookup else 1.0
            w_words = 2.0 if is_lookup else 1.0
        else:
            w_meaning = 0.5 if is_lookup else 1.0
            w_words = 4.0 if is_lookup else 2.0

        hits_by_file: dict[int, SearchHit] = {}

        for row in records:
            seg_id, file_id, s_kind, s_loc, s_loc_end, s_excerpt, f_path, f_kind_val, f_mtime, f_size, f_info_json = row
            file_kind = FileKind(f_kind_val)
            seg_kind = SegmentKind(s_kind)

            cos, z = segment_z_scores.get(seg_id, (0.0, 0.0))

            fts_item = fts_by_seg.get(seg_id)
            kw_rank = int(fts_item["rank"]) if fts_item else 0
            snippet = fts_item["snippet"] if fts_item and fts_item["snippet"] else s_excerpt

            file_path_obj = Path(f_path)
            content_text = " ".join([file_path_obj.name, snippet, s_excerpt])
            coverage = self.compute_coverage(terms, content_text)

            # Quality Gating
            if z < 2.0 and coverage < 0.5:
                continue

            # HintBonus
            hint_bonus = 0.0
            if target_kind is not None and file_kind == target_kind:
                hint_bonus += 1.0

            filename_cov = self.compute_coverage(terms, file_path_obj.name)
            hint_bonus += 0.5 * filename_cov

            focus_line = 0.0
            if is_code:
                focus_line = float(self._find_code_definition_line(f_path, terms))
                if focus_line > 0:
                    hint_bonus += 0.3

            score = w_meaning * max(z, 0.0) + w_words * (coverage ** 1.5) + hint_bonus

            hit = SearchHit(
                path=f_path,
                kind=file_kind,
                score=score,
                z=z,
                cosine=cos,
                keyword_rank=kw_rank,
                segment=seg_kind,
                segment_id=seg_id,
                loc=s_loc or 0.0,
                loc_end=s_loc_end or 0.0,
                excerpt=snippet or s_excerpt,
                modified=f_mtime,
                size=f_size,
                focus=focus_line,
            )

            if file_id not in hits_by_file or hit.score > hits_by_file[file_id].score:
                hits_by_file[file_id] = hit

        all_hits = sorted(hits_by_file.values(), key=lambda h: h.score, reverse=True)
        if not all_hits:
            return ResultSet()

        top_score = all_hits[0].score
        threshold = 0.5 * top_score

        ranked = [h for h in all_hits if h.score >= threshold][:limit]
        weaker = [h for h in all_hits if h.score < threshold]

        return ResultSet(ranked=ranked, weaker=weaker)

    def format_results(
        self,
        hits: list[SearchHit] | ResultSet,
        query: str = "",
    ) -> tuple[list[ResultRow], list[ResultRow]]:
        """Transform search hits into primary and weaker ResultRow view models."""
        terms = re.findall(r"\w+", query.lower())
        if isinstance(hits, ResultSet):
            primary = [ResultRow.from_hit(h, terms=terms) for h in hits.ranked]
            weaker = [ResultRow.from_hit(h, terms=terms) for h in hits.weaker]
            return primary, weaker

        if not hits:
            return [], []

        top_score = max(h.score for h in hits)
        threshold = 0.5 * top_score
        primary = [ResultRow.from_hit(h, terms=terms) for h in hits if h.score >= threshold]
        weaker = [ResultRow.from_hit(h, terms=terms) for h in hits if h.score < threshold]
        return primary, weaker
