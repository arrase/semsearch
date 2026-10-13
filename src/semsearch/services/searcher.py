"""Search engine with dual-query routing, hybrid DigUp ranking, and desktop gating."""

from dataclasses import dataclass, field
from pathlib import Path
import re
import numpy as np

from semsearch.core.code_lookup import (
    extract_identifier,
    find_definition,
    find_first_line_with_words,
    identifier_phrase,
)
from semsearch.core.database import Database
from semsearch.core.ollama import (
    OllamaClient,
    OllamaError,
    format_code_query_prompt,
    format_query_prompt,
)
from semsearch.core.search_text import (
    _fold_latin_diacritics,
    extract_kind_hints,
    extract_terms,
    is_lookup_query,
    make_fts_patterns,
)
from semsearch.core.types import (
    FileKind,
    FileRecord,
    SearchHit,
    SegmentKind,
)
from semsearch.core.vectors import cosine_similarity, z_score_normalize


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

    def _resolve_code_place(
        self,
        target_db: Database,
        file_rec: FileRecord,
        segment_id: int,
        identifier: list[str] | None,
        clean_query: str,
        terms: list[str],
        words_here: bool,
    ) -> tuple[int, float, int]:
        """Resolve code stretch segment, precise line number jump, and definition strength."""
        name = clean_query.strip().removesuffix("()")
        can_check_def = identifier is not None or len(terms) == 1

        if identifier:
            phrase = identifier_phrase(identifier)
            best_def: tuple[int, int, int] | None = None
            for s_id, s_loc, s_text in target_db.find_code_segments(file_rec.id, phrase):
                def_res = find_definition(name, s_text, first_line=s_loc)
                if def_res:
                    line_num, strength = def_res
                    if best_def is None or strength > best_def[2]:
                        best_def = (s_id, line_num, strength)
                    if strength == 2:
                        break

            if best_def is not None:
                return best_def[0], float(best_def[1]), best_def[2]

        if can_check_def:
            best_seg_def: tuple[int, int, int] | None = None
            for s_id, s_kind, s_loc, s_loc_end, _, s_body in target_db.get_file_segments(file_rec.id):
                if s_kind == SegmentKind.LINES and s_body:
                    def_res = find_definition(name, s_body, first_line=int(s_loc or 1))
                    if def_res:
                        line_num, strength = def_res
                        if best_seg_def is None or strength > best_seg_def[2]:
                            best_seg_def = (s_id, line_num, strength)
                        if strength == 2:
                            break
            if best_seg_def is not None:
                return best_seg_def[0], float(best_seg_def[1]), best_seg_def[2]

            file_path = Path(file_rec.path)
            if file_path.is_file():
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    content = ""
                if content:
                    def_res = find_definition(name, content, first_line=1)
                    if def_res:
                        line_num, strength = def_res
                        matched_seg_id = segment_id
                        for s_id, s_kind, s_loc, s_loc_end, _, _ in target_db.get_file_segments(file_rec.id):
                            if s_kind == SegmentKind.LINES and s_loc <= line_num <= max(s_loc_end, s_loc):
                                matched_seg_id = s_id
                                break
                        return matched_seg_id, float(line_num), strength

        if words_here or identifier is not None:
            seg_text = target_db.get_segment_text(segment_id)
            if seg_text is not None:
                first_l, text_content = seg_text
                query_words = terms if not identifier else [name.lower()]
                line_hit = find_first_line_with_words(query_words, text_content, first_line=first_l)
                if line_hit is not None:
                    return segment_id, float(line_hit), 0

        return segment_id, 0.0, 0

    def search(
        self,
        query: str,
        limit: int = 50,
        is_code: bool = False,
        prefix_last: bool = False,
        kinds: set[FileKind] | None = None,
    ) -> ResultSet:
        """Execute hybrid search with dual query routing, Z-score fusion, gating, and folding."""
        stripped = query.strip()
        if not stripped:
            return ResultSet()

        is_code_prefix = stripped.lower().startswith("code:")
        if not is_code:
            is_code = is_code_prefix
        if is_code:
            target_db = self.code_db
            clean_query = stripped[5:].strip() if is_code_prefix else stripped
            if target_db is None or not clean_query:
                return ResultSet()
        else:
            target_db = self.db
            clean_query = stripped

        hinted_kinds = extract_kind_hints(clean_query)
        extracted_terms = extract_terms(clean_query)
        pure_kind_query = not extracted_terms and bool(hinted_kinds) and not is_code

        if extracted_terms:
            terms = extracted_terms
        elif pure_kind_query:
            terms = []
        else:
            terms = [w.lower() for w in re.findall(r"\w+", clean_query) if len(w) >= 2]
            if not terms:
                terms = [clean_query.lower()]

        identifier = extract_identifier(clean_query) if is_code else None
        is_lookup = (identifier is not None) or is_lookup_query(clean_query, terms=terms)

        # 1. Vector Search
        best: dict[int, tuple[float, float, int]] = {}
        if self.ollama is not None:
            prompt = format_code_query_prompt(clean_query) if is_code else format_query_prompt(clean_query)
            try:
                embeddings = self.ollama.embed(self.model, [prompt])
            except OllamaError:
                embeddings = []

            if embeddings:
                query_vec = np.array(embeddings[0], dtype=np.float32)
                seg_ids, file_ids, modalities, mat, sets_scale = target_db.get_all_vectors_with_scale()
                if len(seg_ids) > 0 and mat.shape[0] > 0:
                    cosines = cosine_similarity(query_vec, mat)
                    z_scores = z_score_normalize(cosines, modalities, sets_scale=sets_scale)
                    for seg_id, f_id, cos, z in zip(seg_ids, file_ids, cosines, z_scores):
                        z_val = float(z)
                        cos_val = float(cos)
                        if f_id not in best or z_val > best[f_id][0]:
                            best[f_id] = (z_val, cos_val, seg_id)

        # 2. Keyword Search
        if identifier is not None:
            patterns = [identifier_phrase(identifier)]
        elif terms:
            patterns = make_fts_patterns(terms, prefix_last=prefix_last)
            for idx, p in enumerate(patterns):
                if len(terms[idx]) >= 3 and not p.endswith("*"):
                    if not target_db.is_indexed(p):
                        patterns[idx] = p + "*"
        else:
            patterns = []

        words: list[dict] = []
        coverage: dict[int, float] = {}
        if patterns:
            fts_query_str = " OR ".join(patterns)
            fts_rows = target_db.fts_search(fts_query_str, limit=1000, terms=terms)
            seen_files: set[int] = set()
            for r in fts_rows:
                fid = r["file_id"]
                if fid is not None and fid not in seen_files:
                    seen_files.add(fid)
                    words.append(r)
                    if len(words) >= 200:
                        break

            cov_fids = list(seen_files | {fid for fid, sem in best.items() if sem[0] >= 1.5})
            if cov_fids:
                coverage = target_db.compute_coverage(patterns, cov_fids)

        word_rank: dict[int, tuple[int, dict]] = {
            w["file_id"]: (idx + 1, w) for idx, w in enumerate(words)
        }

        # 3. Quality Gating & Score Fusion
        z_gate = 2.0
        word_weight = 1.0 if is_code else 2.0
        meaning_weight, words_weight = (0.5, word_weight * 2.0) if is_lookup else (1.0, word_weight)

        if pure_kind_query:
            candidates = {
                f_rec.id
                for f_rec in target_db.find_all_files()
                if f_rec.kind in hinted_kinds
            }
        else:
            candidates = {fid for fid, sem in best.items() if sem[0] >= z_gate}
            for fid, cov in coverage.items():
                if cov >= 0.5:
                    candidates.add(fid)

        if not candidates:
            return ResultSet()

        scored: dict[int, float] = {}
        file_records: dict[int, FileRecord] = {}
        resolved_code_places: dict[int, tuple[int, float, int]] = {}
        folded_terms = [_fold_latin_diacritics(t) for t in terms if _fold_latin_diacritics(t)]

        for fid in candidates:
            f_rec = target_db.find_by_id(fid)
            if f_rec is None:
                continue
            if kinds is not None and f_rec.kind not in kinds:
                continue
            file_records[fid] = f_rec

            sem = best.get(fid)
            z_val = sem[0] if sem else 0.0
            cov_val = coverage.get(fid, 0.0)

            kind_bonus = 1.0 if f_rec.kind in hinted_kinds else 0.0

            name_bonus = 0.0
            if folded_terms and cov_val >= 0.5:
                stem_folded = _fold_latin_diacritics(Path(f_rec.path).stem)
                matched_in_stem = sum(1 for ft in folded_terms if ft in stem_folded)
                if matched_in_stem > 0:
                    name_ratio = matched_in_stem / len(folded_terms)
                    name_bonus = 3.0 * (name_ratio ** 1.5)

            def_bonus = 0.0
            if is_code:
                keyword = word_rank.get(fid)
                use_meaning = sem is not None and (sem[0] >= z_gate or keyword is None)
                init_seg_id = sem[2] if use_meaning and sem is not None else (keyword[1]["segment_id"] if keyword else None)
                if init_seg_id is not None:
                    res_seg_id, res_focus, strength = self._resolve_code_place(
                        target_db=target_db,
                        file_rec=f_rec,
                        segment_id=init_seg_id,
                        identifier=identifier,
                        clean_query=clean_query,
                        terms=terms,
                        words_here=(keyword is not None and keyword[1]["segment_id"] == init_seg_id) or identifier is not None,
                    )
                    resolved_code_places[fid] = (res_seg_id, res_focus, strength)
                    if strength == 2:
                        def_bonus = 3.0
                    elif strength == 1:
                        def_bonus = 1.5

            score = (
                meaning_weight * max(z_val, 0.0)
                + words_weight * (cov_val ** 1.5)
                + kind_bonus
                + name_bonus
                + def_bonus
            )
            scored[fid] = score

        if not scored:
            return ResultSet()

        # 4. Result Hit Assembly
        sorted_files = sorted(
            scored.keys(),
            key=lambda fid: (scored[fid], file_records[fid].mtime),
            reverse=True,
        )
        hits: list[SearchHit] = []

        for fid in sorted_files:
            f_rec = file_records[fid]
            semantic = best.get(fid)
            keyword = word_rank.get(fid)

            use_meaning = semantic is not None and (semantic[0] >= z_gate or keyword is None)
            segment_id = (
                semantic[2]
                if use_meaning and semantic is not None
                else (keyword[1]["segment_id"] if keyword else None)
            )
            if segment_id is None and pure_kind_query:
                file_segs = target_db.get_file_segments(fid)
                if file_segs:
                    segment_id = file_segs[0][0]
            if segment_id is None:
                continue

            focus_line: float = 0.0
            if is_code and fid in resolved_code_places:
                segment_id, focus_line, _ = resolved_code_places[fid]

            seg_meta = target_db.get_segment(segment_id)
            if seg_meta is not None:
                s_kind, s_loc, s_loc_end, s_excerpt = seg_meta
            else:
                s_kind, s_loc, s_loc_end, s_excerpt = SegmentKind.NAME, 0.0, 0.0, ""

            snippet = ""
            if keyword and keyword[1]["segment_id"] == segment_id:
                raw_snip = keyword[1]["snippet"]
                if raw_snip and "«" in raw_snip:
                    snippet = raw_snip

            if not snippet:
                snippet = s_excerpt

            kw_rank = keyword[0] if keyword else 0

            hits.append(
                SearchHit(
                    path=f_rec.path,
                    kind=f_rec.kind,
                    score=scored[fid],
                    z=semantic[0] if semantic else 0.0,
                    cosine=semantic[1] if semantic else 0.0,
                    keyword_rank=kw_rank,
                    segment=s_kind,
                    segment_id=segment_id,
                    loc=s_loc,
                    loc_end=s_loc_end,
                    excerpt=snippet,
                    modified=f_rec.mtime,
                    size=f_rec.size,
                    info=f_rec.info,
                    focus=focus_line,
                )
            )

        if not hits:
            return ResultSet()

        top_score = hits[0].score
        threshold = 0.5 * top_score

        ranked = [h for h in hits if h.score >= threshold][:limit]
        weaker = [h for h in hits if h.score < threshold]

        return ResultSet(ranked=ranked, weaker=weaker)

