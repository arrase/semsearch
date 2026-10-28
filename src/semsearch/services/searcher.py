"""Search engine with dual-query routing, hybrid DigUp ranking, and desktop gating."""

import re
from dataclasses import dataclass, field
from pathlib import Path

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
    SEMANTIC_Z_GATE,
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

    @staticmethod
    def _find_identifier_definition_in_db(
        target_db: Database,
        file_id: int,
        name: str,
        identifier: list[str],
    ) -> tuple[int, float, int] | None:
        phrase = identifier_phrase(identifier)
        best_def: tuple[int, int, int] | None = None
        for s_id, s_loc, s_text in target_db.find_code_segments(file_id, phrase):
            def_res = find_definition(name, s_text, first_line=s_loc)
            if not def_res:
                continue
            line_num, strength = def_res
            if best_def is None or strength > best_def[2]:
                best_def = (s_id, line_num, strength)
            if strength == 2:
                break
        return (best_def[0], float(best_def[1]), best_def[2]) if best_def is not None else None

    @staticmethod
    def _find_segment_definition_in_db(
        target_db: Database,
        file_id: int,
        name: str,
    ) -> tuple[int, float, int] | None:
        best_seg_def: tuple[int, int, int] | None = None
        for seg_id_cand, s_kind, seg_loc, _, _, s_body in target_db.get_file_segments(file_id):
            if s_kind != SegmentKind.LINES or not s_body:
                continue
            def_res = find_definition(name, s_body, first_line=int(seg_loc or 1))
            if not def_res:
                continue
            line_num, strength = def_res
            if best_seg_def is None or strength > best_seg_def[2]:
                best_seg_def = (seg_id_cand, line_num, strength)
            if strength == 2:
                break
        return (best_seg_def[0], float(best_seg_def[1]), best_seg_def[2]) if best_seg_def is not None else None

    @staticmethod
    def _find_file_definition_on_disk(
        target_db: Database,
        file_rec: FileRecord,
        segment_id: int,
        name: str,
    ) -> tuple[int, float, int] | None:
        file_path = Path(file_rec.path)
        if not file_path.is_file():
            return None
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if not content:
            return None
        def_res = find_definition(name, content, first_line=1)
        if not def_res:
            return None
        line_num, strength = def_res
        matched_seg_id = segment_id
        for seg_id_cand, s_kind, seg_loc, s_loc_end, _, _ in target_db.get_file_segments(file_rec.id):
            if s_kind == SegmentKind.LINES and seg_loc <= line_num <= max(s_loc_end, seg_loc):
                matched_seg_id = seg_id_cand
                break
        return matched_seg_id, float(line_num), strength

    @staticmethod
    def _find_word_line_in_segment(
        target_db: Database,
        segment_id: int,
        name: str,
        identifier: list[str] | None,
        terms: list[str],
    ) -> tuple[int, float, int] | None:
        seg_text = target_db.get_segment_text(segment_id)
        if seg_text is None:
            return None
        first_l, text_content = seg_text
        query_words = terms if not identifier else [name.lower()]
        line_hit = find_first_line_with_words(query_words, text_content, first_line=first_l)
        return (segment_id, float(line_hit), 0) if line_hit is not None else None

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
            id_def = self._find_identifier_definition_in_db(target_db, file_rec.id, name, identifier)
            if id_def is not None:
                return id_def

        if can_check_def:
            seg_def = self._find_segment_definition_in_db(target_db, file_rec.id, name)
            if seg_def is not None:
                return seg_def
            disk_def = self._find_file_definition_on_disk(target_db, file_rec, segment_id, name)
            if disk_def is not None:
                return disk_def

        if words_here or identifier is not None:
            word_hit = self._find_word_line_in_segment(target_db, segment_id, name, identifier, terms)
            if word_hit is not None:
                return word_hit

        return segment_id, 0.0, 0

    def _resolve_target_db_and_query(self, stripped: str, is_code: bool) -> tuple[Database | None, str, bool]:
        is_code_prefix = stripped.lower().startswith("code:")
        effective_code = is_code or is_code_prefix
        if effective_code:
            clean_query = stripped[5:].strip() if is_code_prefix else stripped
            if self.code_db is None or not clean_query:
                return None, "", True
            return self.code_db, clean_query, True
        return self.db, stripped, False

    @staticmethod
    def _parse_search_terms(
        clean_query: str,
        hinted_kinds: set[FileKind],
        is_code: bool,
    ) -> tuple[list[str], bool]:
        extracted_terms = extract_terms(clean_query)
        pure_kind_query = not extracted_terms and bool(hinted_kinds) and not is_code
        if extracted_terms:
            return extracted_terms, pure_kind_query
        if pure_kind_query:
            return [], True
        terms = [w.lower() for w in re.findall(r"\w+", clean_query) if len(w) >= 2]
        return (terms or [clean_query.lower()]), False

    def _vector_search(
        self,
        target_db: Database,
        clean_query: str,
        is_code: bool,
    ) -> dict[int, tuple[float, float, int]]:
        best: dict[int, tuple[float, float, int]] = {}
        if self.ollama is None:
            return best
        prompt = format_code_query_prompt(clean_query) if is_code else format_query_prompt(clean_query)
        try:
            embeddings = self.ollama.embed(self.model, [prompt])
        except OllamaError:
            return best
        if not embeddings:
            return best

        query_vec = np.array(embeddings[0], dtype=np.float32)
        seg_ids, file_ids, modalities, mat, sets_scale = target_db.get_all_vectors_with_scale()
        if len(seg_ids) == 0 or mat.shape[0] == 0:
            return best

        cosines = cosine_similarity(query_vec, mat)
        z_scores = z_score_normalize(cosines, modalities, sets_scale=sets_scale)
        for seg_id, f_id, cos, z in zip(seg_ids, file_ids, cosines, z_scores, strict=False):
            z_val = float(z)
            cos_val = float(cos)
            if f_id not in best or z_val > best[f_id][0]:
                best[f_id] = (z_val, cos_val, seg_id)
        return best

    @staticmethod
    def _build_fts_patterns(
        target_db: Database,
        terms: list[str],
        identifier: list[str] | None,
        prefix_last: bool,
    ) -> list[str]:
        if identifier is not None:
            return [identifier_phrase(identifier)]
        if not terms:
            return []
        patterns = make_fts_patterns(terms, prefix_last=prefix_last)
        for idx, p in enumerate(patterns):
            if len(terms[idx]) >= 3 and not p.endswith("*") and not target_db.is_indexed(p):
                patterns[idx] = p + "*"
        return patterns

    def _keyword_search(
        self,
        target_db: Database,
        terms: list[str],
        identifier: list[str] | None,
        prefix_last: bool,
        best: dict[int, tuple[float, float, int]],
    ) -> tuple[dict[int, tuple[int, dict]], dict[int, float]]:
        patterns = self._build_fts_patterns(target_db, terms, identifier, prefix_last)
        if not patterns:
            return {}, {}

        fts_rows = target_db.fts_search(" OR ".join(patterns), limit=1000, terms=terms)
        words: list[dict] = []
        seen_files: set[int] = set()
        for r in fts_rows:
            fid = r["file_id"]
            if fid is not None and fid not in seen_files:
                seen_files.add(fid)
                words.append(r)
                if len(words) >= 200:
                    break

        cov_fids = list(seen_files | {fid for fid, sem in best.items() if sem[0] >= 1.5})
        coverage = target_db.compute_coverage(patterns, cov_fids) if cov_fids else {}
        word_rank = {w["file_id"]: (idx + 1, w) for idx, w in enumerate(words)}
        return word_rank, coverage

    @staticmethod
    def _collect_candidates(
        target_db: Database,
        best: dict[int, tuple[float, float, int]],
        coverage: dict[int, float],
        hinted_kinds: set[FileKind],
        pure_kind_query: bool,
    ) -> set[int]:
        if pure_kind_query:
            return {f_rec.id for f_rec in target_db.find_all_files() if f_rec.kind in hinted_kinds}
        candidates = {fid for fid, sem in best.items() if sem[0] >= SEMANTIC_Z_GATE}
        candidates.update(fid for fid, cov in coverage.items() if cov >= 0.5)
        return candidates

    @staticmethod
    def _compute_name_bonus(path: str, folded_terms: list[str], cov_val: float) -> float:
        if not folded_terms or cov_val < 0.5:
            return 0.0
        stem_folded = _fold_latin_diacritics(Path(path).stem)
        matched_in_stem = sum(1 for ft in folded_terms if ft in stem_folded)
        if matched_in_stem <= 0:
            return 0.0
        name_ratio = matched_in_stem / len(folded_terms)
        return 3.0 * (name_ratio**1.5)

    @staticmethod
    def _select_segment_id(
        sem: tuple[float, float, int] | None,
        keyword: tuple[int, dict] | None,
    ) -> int | None:
        if sem is not None and (sem[0] >= SEMANTIC_Z_GATE or keyword is None):
            return sem[2]
        return keyword[1]["segment_id"] if keyword is not None else None

    def _compute_code_definition_bonus(
        self,
        target_db: Database,
        f_rec: FileRecord,
        sem: tuple[float, float, int] | None,
        keyword: tuple[int, dict] | None,
        identifier: list[str] | None,
        clean_query: str,
        terms: list[str],
        resolved_code_places: dict[int, tuple[int, float, int]],
    ) -> float:
        init_seg_id = self._select_segment_id(sem, keyword)
        if init_seg_id is None:
            return 0.0
        words_here = (keyword is not None and keyword[1]["segment_id"] == init_seg_id) or identifier is not None
        res_seg_id, res_focus, strength = self._resolve_code_place(
            target_db=target_db,
            file_rec=f_rec,
            segment_id=init_seg_id,
            identifier=identifier,
            clean_query=clean_query,
            terms=terms,
            words_here=words_here,
        )
        resolved_code_places[f_rec.id] = (res_seg_id, res_focus, strength)
        if strength == 2:
            return 3.0
        return 1.5 if strength == 1 else 0.0

    def _resolve_hit_segment(
        self,
        target_db: Database,
        fid: int,
        semantic: tuple[float, float, int] | None,
        keyword: tuple[int, dict] | None,
        is_code: bool,
        pure_kind_query: bool,
        resolved_code_places: dict[int, tuple[int, float, int]],
    ) -> tuple[int | None, float]:
        if is_code and fid in resolved_code_places:
            seg_id, focus_line, _ = resolved_code_places[fid]
            return seg_id, focus_line

        segment_id = self._select_segment_id(semantic, keyword)
        if segment_id is None and pure_kind_query:
            file_segs = target_db.get_file_segments(fid)
            if file_segs:
                segment_id = file_segs[0][0]
        return segment_id, 0.0

    def _assemble_hit(
        self,
        target_db: Database,
        fid: int,
        f_rec: FileRecord,
        score: float,
        semantic: tuple[float, float, int] | None,
        keyword: tuple[int, dict] | None,
        is_code: bool,
        pure_kind_query: bool,
        resolved_code_places: dict[int, tuple[int, float, int]],
    ) -> SearchHit | None:
        segment_id, focus_line = self._resolve_hit_segment(
            target_db, fid, semantic, keyword, is_code, pure_kind_query, resolved_code_places
        )
        if segment_id is None:
            return None

        seg_meta = target_db.get_segment(segment_id)
        s_kind, s_loc, s_loc_end, s_excerpt = seg_meta or (SegmentKind.NAME, 0.0, 0.0, "")

        snippet = s_excerpt
        if keyword and keyword[1]["segment_id"] == segment_id:
            raw_snip = keyword[1]["snippet"]
            if raw_snip and "«" in raw_snip:
                snippet = raw_snip

        return SearchHit(
            path=f_rec.path,
            kind=f_rec.kind,
            score=score,
            z=semantic[0] if semantic else 0.0,
            cosine=semantic[1] if semantic else 0.0,
            keyword_rank=keyword[0] if keyword else 0,
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

    def _score_candidates(
        self,
        target_db: Database,
        candidates: set[int],
        kinds: set[FileKind] | None,
        best: dict[int, tuple[float, float, int]],
        coverage: dict[int, float],
        word_rank: dict[int, tuple[int, dict]],
        hinted_kinds: set[FileKind],
        terms: list[str],
        identifier: list[str] | None,
        clean_query: str,
        is_code: bool,
        is_lookup: bool,
    ) -> tuple[dict[int, float], dict[int, FileRecord], dict[int, tuple[int, float, int]]]:
        word_weight = 1.0 if is_code else 2.0
        meaning_weight, words_weight = (0.5, word_weight * 2.0) if is_lookup else (1.0, word_weight)
        scored: dict[int, float] = {}
        file_records: dict[int, FileRecord] = {}
        resolved_code_places: dict[int, tuple[int, float, int]] = {}
        folded_terms = [_fold_latin_diacritics(t) for t in terms if _fold_latin_diacritics(t)]

        for fid in candidates:
            f_rec = target_db.find_by_id(fid)
            if f_rec is None or (kinds is not None and f_rec.kind not in kinds):
                continue
            file_records[fid] = f_rec

            sem = best.get(fid)
            z_val = sem[0] if sem else 0.0
            cov_val = coverage.get(fid, 0.0)
            kind_bonus = 1.0 if f_rec.kind in hinted_kinds else 0.0
            name_bonus = self._compute_name_bonus(f_rec.path, folded_terms, cov_val)
            def_bonus = (
                self._compute_code_definition_bonus(
                    target_db, f_rec, sem, word_rank.get(fid), identifier, clean_query, terms, resolved_code_places
                )
                if is_code
                else 0.0
            )
            scored[fid] = (
                meaning_weight * max(z_val, 0.0) + words_weight * (cov_val**1.5) + kind_bonus + name_bonus + def_bonus
            )
        return scored, file_records, resolved_code_places

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

        target_db, clean_query, is_code = self._resolve_target_db_and_query(stripped, is_code)
        if target_db is None:
            return ResultSet()

        hinted_kinds = extract_kind_hints(clean_query)
        terms, pure_kind_query = self._parse_search_terms(clean_query, hinted_kinds, is_code)
        identifier = extract_identifier(clean_query) if is_code else None
        is_lookup = (identifier is not None) or is_lookup_query(clean_query, terms=terms)

        # 1. Vector Search
        best = self._vector_search(target_db, clean_query, is_code)

        # 2. Keyword Search
        word_rank, coverage = self._keyword_search(target_db, terms, identifier, prefix_last, best)

        # 3. Quality Gating & Score Fusion
        candidates = self._collect_candidates(target_db, best, coverage, hinted_kinds, pure_kind_query)
        if not candidates:
            return ResultSet()

        scored, file_records, resolved_code_places = self._score_candidates(
            target_db=target_db,
            candidates=candidates,
            kinds=kinds,
            best=best,
            coverage=coverage,
            word_rank=word_rank,
            hinted_kinds=hinted_kinds,
            terms=terms,
            identifier=identifier,
            clean_query=clean_query,
            is_code=is_code,
            is_lookup=is_lookup,
        )
        if not scored:
            return ResultSet()

        # 4. Result Hit Assembly
        sorted_files = sorted(
            scored.keys(),
            key=lambda fid: (scored[fid], file_records[fid].mtime),
            reverse=True,
        )
        hits = [
            hit
            for fid in sorted_files
            if (
                hit := self._assemble_hit(
                    target_db=target_db,
                    fid=fid,
                    f_rec=file_records[fid],
                    score=scored[fid],
                    semantic=best.get(fid),
                    keyword=word_rank.get(fid),
                    is_code=is_code,
                    pure_kind_query=pure_kind_query,
                    resolved_code_places=resolved_code_places,
                )
            )
            is not None
        ]
        if not hits:
            return ResultSet()

        threshold = 0.5 * hits[0].score
        ranked = [h for h in hits if h.score >= threshold][:limit]
        weaker = [h for h in hits if h.score < threshold]
        return ResultSet(ranked=ranked, weaker=weaker)
