"""Search engine with dual-query routing, hybrid BM25 + Z-score ranking, and inline filters."""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from semsearch.core.code_lookup import (
    extract_identifier,
    find_definition,
    find_first_line_with_words,
    identifier_phrase,
)
from semsearch.core.database import Database, FtsMatchRow, SegmentMeta
from semsearch.core.ollama import (
    OllamaClient,
    OllamaError,
    format_code_query_prompt,
    format_query_prompt,
)
from semsearch.core.search_text import (
    _fold_latin_diacritics,
    extract_kind_hints,
    extract_snippet,
    extract_terms,
    is_lookup_query,
    make_fts_patterns,
    parse_query_filters,
)
from semsearch.core.types import (
    SEMANTIC_Z_GATE,
    FileKind,
    FileRecord,
    ParsedQuery,
    SearchHit,
    SegmentKind,
)
from semsearch.core.vectors import cosine_similarity_unit, z_score_normalize

_STRONG_DEFINITION = 2
_MIN_WORD_LEN = 2
_MIN_WILDCARD_TERM_LEN = 3
_MAX_FTS_WORD_FILES = 200
_COVERAGE_SEMANTIC_Z_FLOOR = 1.5
_MIN_COVERAGE_GATE = 0.5
_BM25_RANK_SCALE = 10.0
_BM25_WEIGHT_FACTOR = 0.5
_MIN_LEXICAL_LIMIT = 100
_LEXICAL_OVERFETCH_FACTOR = 4


def _resolve_hit_snippet(
    default_snippet: str,
    segment_id: int,
    keyword: tuple[int, FtsMatchRow] | None,
    terms: list[str] | None,
) -> str:
    if keyword is None or keyword[1]["segment_id"] != segment_id:
        return default_snippet
    row = keyword[1]
    source_text = row["original"] or row["body"]
    refined = extract_snippet(source_text, terms) if terms and source_text else None
    raw_snip = refined or row["snippet"]
    if raw_snip and "«" in raw_snip:
        return raw_snip
    return default_snippet


@dataclass
class ResultSet:
    """Search results partitioned into primary ranked hits and folded weaker hits."""

    ranked: list[SearchHit] = field(default_factory=list)
    weaker: list[SearchHit] = field(default_factory=list)

    def __iter__(self) -> Iterator[SearchHit]:
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
            if strength == _STRONG_DEFINITION:
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
            if strength == _STRONG_DEFINITION:
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
        *,
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

    def _resolve_target_db(self, parsed: ParsedQuery, *, is_code: bool) -> tuple[Database | None, bool]:
        effective_code = is_code or parsed.is_code
        if effective_code:
            if self.code_db is None:
                return None, True
            return self.code_db, True
        return self.db, False

    @staticmethod
    def _has_inline_filters(parsed: ParsedQuery) -> bool:
        return bool(parsed.kinds or parsed.extensions or parsed.path_fragments or parsed.after_mtime > 0.0)

    @staticmethod
    def _combine_kinds(kinds: set[FileKind] | None, parsed_kinds: frozenset[FileKind]) -> set[FileKind] | None:
        if kinds is not None and parsed_kinds:
            return set(kinds) & set(parsed_kinds)
        if parsed_kinds:
            return set(parsed_kinds)
        return set(kinds) if kinds is not None else None

    @staticmethod
    def _matches_filters(
        f_rec: FileRecord,
        *,
        parsed: ParsedQuery,
        kinds: set[FileKind] | None,
    ) -> bool:
        if kinds is not None and f_rec.kind not in kinds:
            return False
        if parsed.extensions and Path(f_rec.path).suffix.lstrip(".").lower() not in parsed.extensions:
            return False
        if parsed.path_fragments:
            path_lower = f_rec.path.lower()
            if not all(frag.lower() in path_lower for frag in parsed.path_fragments):
                return False
        return not (parsed.after_mtime > 0.0 and f_rec.mtime < parsed.after_mtime)

    def _filter_records(
        self,
        records: dict[int, FileRecord],
        *,
        parsed: ParsedQuery,
        kinds: set[FileKind] | None,
    ) -> dict[int, FileRecord]:
        return {
            fid: f_rec for fid, f_rec in records.items() if self._matches_filters(f_rec, parsed=parsed, kinds=kinds)
        }

    @staticmethod
    def _parse_search_terms(
        clean_query: str,
        hinted_kinds: set[FileKind],
        *,
        is_code: bool,
    ) -> tuple[list[str], bool]:
        if not clean_query:
            return [], False
        extracted_terms = extract_terms(clean_query)
        pure_kind_query = not extracted_terms and bool(hinted_kinds) and not is_code
        if extracted_terms:
            return extracted_terms, pure_kind_query
        if pure_kind_query:
            return [], True
        terms = [w.lower() for w in re.findall(r"\w+", clean_query) if len(w) >= _MIN_WORD_LEN]
        return (terms or [clean_query.lower()]), False

    def _vector_search(
        self,
        target_db: Database,
        clean_query: str,
        *,
        is_code: bool,
    ) -> dict[int, tuple[float, float, int]]:
        best: dict[int, tuple[float, float, int]] = {}
        if self.ollama is None or not clean_query:
            return best
        prompt = format_code_query_prompt(clean_query) if is_code else format_query_prompt(clean_query)
        try:
            embeddings = self.ollama.embed(self.model, [prompt])
        except OllamaError:
            return best
        if not embeddings:
            return best

        snap = target_db.get_vector_snapshot()
        if snap.segment_ids.size == 0 or snap.matrix.shape[0] == 0:
            return best

        query_vec = np.array(embeddings[0], dtype=np.float32)
        if query_vec.shape[0] != snap.matrix.shape[1]:
            return best

        cosines = cosine_similarity_unit(query_vec, snap.matrix)
        z_scores = z_score_normalize(cosines, snap.modality_codes, sets_scale=snap.scale_mask)
        for seg_id, f_id, cos, z in zip(
            snap.segment_ids.tolist(),
            snap.file_ids.tolist(),
            cosines.tolist(),
            z_scores.tolist(),
            strict=False,
        ):
            if f_id not in best or z > best[f_id][0]:
                best[f_id] = (z, cos, seg_id)
        return best

    @staticmethod
    def _build_fts_patterns(
        target_db: Database,
        terms: list[str],
        identifier: list[str] | None,
        *,
        prefix_last: bool,
    ) -> list[str]:
        if identifier is not None:
            return [identifier_phrase(identifier)]
        if not terms:
            return []
        patterns = make_fts_patterns(terms, prefix_last=prefix_last)
        for idx, p in enumerate(patterns):
            if len(terms[idx]) >= _MIN_WILDCARD_TERM_LEN and not p.endswith("*") and not target_db.is_indexed(p):
                patterns[idx] = p + "*"
        return patterns

    def _keyword_search(
        self,
        target_db: Database,
        terms: list[str],
        identifier: list[str] | None,
        *,
        prefix_last: bool,
        best: dict[int, tuple[float, float, int]],
    ) -> tuple[dict[int, tuple[int, FtsMatchRow]], dict[int, float]]:
        patterns = self._build_fts_patterns(target_db, terms, identifier, prefix_last=prefix_last)
        if not patterns:
            return {}, {}

        fts_rows = target_db.fts_search(" OR ".join(patterns), limit=1000, terms=None)
        words: list[FtsMatchRow] = []
        seen_files: set[int] = set()
        for r in fts_rows:
            fid = r["file_id"]
            if fid is not None and fid not in seen_files:
                seen_files.add(fid)
                words.append(r)
                if len(words) >= _MAX_FTS_WORD_FILES:
                    break

        cov_fids = list(seen_files | {fid for fid, sem in best.items() if sem[0] >= _COVERAGE_SEMANTIC_Z_FLOOR})
        coverage = target_db.compute_coverage(patterns, cov_fids) if cov_fids else {}
        word_rank = {w["file_id"]: (idx + 1, w) for idx, w in enumerate(words) if w["file_id"] is not None}
        return word_rank, coverage

    @staticmethod
    def _collect_candidates(
        target_db: Database,
        best: dict[int, tuple[float, float, int]],
        coverage: dict[int, float],
        hinted_kinds: set[FileKind],
        *,
        pure_kind_query: bool,
    ) -> set[int]:
        if pure_kind_query:
            return {f_rec.id for f_rec in target_db.find_all_files() if f_rec.kind in hinted_kinds}
        candidates = {fid for fid, sem in best.items() if sem[0] >= SEMANTIC_Z_GATE}
        candidates.update(fid for fid, cov in coverage.items() if cov >= _MIN_COVERAGE_GATE)
        return candidates

    @staticmethod
    def _compute_name_bonus(path: str, folded_terms: list[str], cov_val: float) -> float:
        if not folded_terms or cov_val < _MIN_COVERAGE_GATE:
            return 0.0
        stem_folded = _fold_latin_diacritics(Path(path).stem)
        matched_in_stem = sum(1 for ft in folded_terms if ft in stem_folded)
        if matched_in_stem <= 0:
            return 0.0
        name_ratio = matched_in_stem / len(folded_terms)
        return float(3.0 * (name_ratio**1.5))

    @staticmethod
    def _select_segment_id(
        sem: tuple[float, float, int] | None,
        keyword: tuple[int, FtsMatchRow] | None,
    ) -> int | None:
        if sem is not None and (sem[0] >= SEMANTIC_Z_GATE or keyword is None):
            return sem[2]
        return keyword[1]["segment_id"] if keyword is not None else None

    def _compute_code_definition_bonus(
        self,
        target_db: Database,
        f_rec: FileRecord,
        sem: tuple[float, float, int] | None,
        keyword: tuple[int, FtsMatchRow] | None,
        *,
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
        if strength == _STRONG_DEFINITION:
            return 3.0
        return 1.5 if strength == 1 else 0.0

    def _resolve_hit_segment(
        self,
        target_db: Database,
        fid: int,
        semantic: tuple[float, float, int] | None,
        keyword: tuple[int, FtsMatchRow] | None,
        *,
        is_code: bool,
        pure_listing: bool,
        resolved_code_places: dict[int, tuple[int, float, int]],
    ) -> tuple[int | None, float]:
        if is_code and fid in resolved_code_places:
            seg_id, focus_line, _ = resolved_code_places[fid]
            return seg_id, focus_line

        segment_id = self._select_segment_id(semantic, keyword)
        if segment_id is None and pure_listing:
            file_segs = target_db.get_file_segments(fid)
            if file_segs:
                segment_id = file_segs[0][0]
        return segment_id, 0.0

    @staticmethod
    def _build_hit(
        f_rec: FileRecord,
        score: float,
        seg_meta: SegmentMeta | None,
        *,
        semantic: tuple[float, float, int] | None,
        keyword: tuple[int, FtsMatchRow] | None,
        focus_line: float,
        terms: list[str] | None,
    ) -> SearchHit:
        if seg_meta is not None:
            s_kind = seg_meta.kind
            s_id = seg_meta.id
            s_loc = seg_meta.loc
            s_loc_end = seg_meta.loc_end
            base_snippet = seg_meta.excerpt
            seg_focus = seg_meta.focus
        else:
            s_kind = SegmentKind.NAME
            s_id = 0
            s_loc = 0.0
            s_loc_end = 0.0
            base_snippet = ""
            seg_focus = 0.0

        snippet = _resolve_hit_snippet(base_snippet, s_id, keyword, terms)
        effective_focus = seg_focus if focus_line <= 0.0 and seg_focus > 0.0 else focus_line
        z, cosine = (semantic[0], semantic[1]) if semantic is not None else (0.0, 0.0)
        keyword_rank = keyword[0] if keyword is not None else 0

        return SearchHit(
            path=f_rec.path,
            kind=f_rec.kind,
            score=score,
            z=z,
            cosine=cosine,
            keyword_rank=keyword_rank,
            segment=s_kind,
            segment_id=s_id,
            loc=s_loc,
            loc_end=s_loc_end,
            excerpt=snippet,
            modified=f_rec.mtime,
            size=f_rec.size,
            info=f_rec.info,
            focus=effective_focus,
        )

    def _assemble_hit_list(
        self,
        fids: list[int],
        file_records: dict[int, FileRecord],
        scored: dict[int, float],
        resolved_segs: dict[int, tuple[int | None, float]],
        seg_metas: dict[int, SegmentMeta],
        *,
        best: dict[int, tuple[float, float, int]],
        word_rank: dict[int, tuple[int, FtsMatchRow]],
        terms: list[str] | None,
        pure_listing: bool,
    ) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for fid in fids:
            seg_id, focus_line = resolved_segs[fid]
            if seg_id is None and not pure_listing:
                continue
            seg_meta = seg_metas.get(seg_id) if seg_id is not None else None
            if seg_id is not None and seg_meta is None:
                continue
            hits.append(
                self._build_hit(
                    file_records[fid],
                    scored[fid],
                    seg_meta,
                    semantic=best.get(fid),
                    keyword=word_rank.get(fid),
                    focus_line=focus_line,
                    terms=terms,
                )
            )
        return hits

    def _score_candidates(
        self,
        target_db: Database,
        file_records: dict[int, FileRecord],
        *,
        best: dict[int, tuple[float, float, int]],
        coverage: dict[int, float],
        word_rank: dict[int, tuple[int, FtsMatchRow]],
        hinted_kinds: set[FileKind],
        terms: list[str],
        identifier: list[str] | None,
        clean_query: str,
        is_code: bool,
    ) -> tuple[dict[int, float], dict[int, tuple[int, float, int]]]:
        is_lookup = (identifier is not None) or is_lookup_query(clean_query, terms=terms)
        word_weight = 1.0 if is_code else 2.0
        meaning_weight, words_weight = (0.5, word_weight * 2.0) if is_lookup else (1.0, word_weight)
        scored: dict[int, float] = {}
        resolved_code_places: dict[int, tuple[int, float, int]] = {}
        folded_terms = [_fold_latin_diacritics(t) for t in terms if _fold_latin_diacritics(t)]

        for fid, f_rec in file_records.items():
            sem = best.get(fid)
            keyword = word_rank.get(fid)
            z_val = sem[0] if sem else 0.0
            cov_val = coverage.get(fid, 0.0)
            cov_factor = cov_val**1.5
            bm25_bonus = (
                (words_weight * _BM25_WEIGHT_FACTOR * cov_factor) / (1.0 + (keyword[0] - 1) / _BM25_RANK_SCALE)
                if keyword is not None
                else 0.0
            )
            kind_bonus = 1.0 if f_rec.kind in hinted_kinds else 0.0
            name_bonus = self._compute_name_bonus(f_rec.path, folded_terms, cov_val)
            def_bonus = (
                self._compute_code_definition_bonus(
                    target_db,
                    f_rec,
                    sem,
                    keyword,
                    identifier=identifier,
                    clean_query=clean_query,
                    terms=terms,
                    resolved_code_places=resolved_code_places,
                )
                if is_code
                else 0.0
            )
            scored[fid] = (
                meaning_weight * max(z_val, 0.0)
                + words_weight * cov_factor
                + bm25_bonus
                + kind_bonus
                + name_bonus
                + def_bonus
            )
        return scored, resolved_code_places

    def _run_pure_filter_search(
        self,
        target_db: Database,
        parsed: ParsedQuery,
        effective_kinds: set[FileKind] | None,
        limit: int,
    ) -> ResultSet:
        all_records = {f_rec.id: f_rec for f_rec in target_db.find_all_files()}
        file_records = self._filter_records(all_records, parsed=parsed, kinds=effective_kinds)
        if not file_records:
            return ResultSet()

        sorted_fids = sorted(file_records.keys(), key=lambda fid: file_records[fid].mtime, reverse=True)[:limit]
        scored = dict.fromkeys(sorted_fids, 1.0)
        resolved_segs = {
            fid: self._resolve_hit_segment(
                target_db,
                fid,
                None,
                None,
                is_code=False,
                pure_listing=True,
                resolved_code_places={},
            )
            for fid in sorted_fids
        }
        seg_metas = target_db.get_segments_by_ids(seg_id for seg_id, _ in resolved_segs.values() if seg_id is not None)
        ranked = self._assemble_hit_list(
            sorted_fids,
            file_records,
            scored,
            resolved_segs,
            seg_metas,
            best={},
            word_rank={},
            terms=None,
            pure_listing=True,
        )
        return ResultSet(ranked=ranked)

    def _prepare_query(
        self,
        query: str,
        *,
        is_code: bool,
        kinds: set[FileKind] | None = None,
    ) -> tuple[Database, ParsedQuery, bool, set[FileKind] | None] | None:
        stripped = query.strip()
        if not stripped:
            return None
        parsed = parse_query_filters(stripped)
        target_db, effective_code = self._resolve_target_db(parsed, is_code=is_code)
        if target_db is None or (not parsed.clean_query and not self._has_inline_filters(parsed)):
            return None
        effective_kinds = self._combine_kinds(kinds, parsed.kinds)
        if effective_kinds is not None and not effective_kinds:
            return None
        return target_db, parsed, effective_code, effective_kinds

    def lexical_search(self, query: str, limit: int = 50, *, is_code: bool = False) -> ResultSet:
        """Execute fast SQLite FTS5 lexical search supporting inline filters without Ollama calls."""
        prepared = self._prepare_query(query, is_code=is_code)
        if prepared is None:
            return ResultSet()

        target_db, parsed, _, effective_kinds = prepared
        if not parsed.clean_query:
            return self._run_pure_filter_search(target_db, parsed, effective_kinds, limit)

        fts_limit = max(limit * _LEXICAL_OVERFETCH_FACTOR, _MIN_LEXICAL_LIMIT)
        fts_rows = target_db.fts_search(parsed.clean_query, limit=fts_limit, terms=None)
        best_rows: dict[int, FtsMatchRow] = {}
        for row in fts_rows:
            fid = row["file_id"]
            if fid is not None and fid not in best_rows:
                best_rows[fid] = row

        records = self._filter_records(target_db.find_by_ids(best_rows.keys()), parsed=parsed, kinds=effective_kinds)
        matched_fids = [fid for fid in best_rows if fid in records][:limit]
        if not matched_fids:
            return ResultSet()

        seg_metas = target_db.get_segments_by_ids(best_rows[fid]["segment_id"] for fid in matched_fids)
        terms = extract_terms(parsed.clean_query)
        hits = [
            self._build_hit(
                records[fid],
                max(float(-best_rows[fid]["rank"]), 0.001),
                seg_metas.get(best_rows[fid]["segment_id"]),
                semantic=None,
                keyword=(rank_idx, best_rows[fid]),
                focus_line=0.0,
                terms=terms or None,
            )
            for rank_idx, fid in enumerate(matched_fids, start=1)
        ]
        return ResultSet(ranked=hits)

    def _partition_and_assemble(
        self,
        target_db: Database,
        file_records: dict[int, FileRecord],
        scored: dict[int, float],
        *,
        best: dict[int, tuple[float, float, int]],
        word_rank: dict[int, tuple[int, FtsMatchRow]],
        terms: list[str],
        is_code: bool,
        pure_kind_query: bool,
        resolved_code_places: dict[int, tuple[int, float, int]],
        limit: int,
    ) -> ResultSet:
        sorted_files = sorted(
            scored.keys(),
            key=lambda fid: (scored[fid], file_records[fid].mtime),
            reverse=True,
        )
        threshold = 0.5 * scored[sorted_files[0]]
        ranked_fids = [fid for fid in sorted_files if scored[fid] >= threshold][:limit]
        weaker_fids = [fid for fid in sorted_files if scored[fid] < threshold]

        resolved_segs = {
            fid: self._resolve_hit_segment(
                target_db,
                fid,
                best.get(fid),
                word_rank.get(fid),
                is_code=is_code,
                pure_listing=pure_kind_query,
                resolved_code_places=resolved_code_places,
            )
            for fid in ranked_fids + weaker_fids
        }
        seg_metas = target_db.get_segments_by_ids(seg_id for seg_id, _ in resolved_segs.values() if seg_id is not None)
        ranked = self._assemble_hit_list(
            ranked_fids,
            file_records,
            scored,
            resolved_segs,
            seg_metas,
            best=best,
            word_rank=word_rank,
            terms=terms or None,
            pure_listing=pure_kind_query,
        )
        weaker = self._assemble_hit_list(
            weaker_fids,
            file_records,
            scored,
            resolved_segs,
            seg_metas,
            best=best,
            word_rank=word_rank,
            terms=None,
            pure_listing=pure_kind_query,
        )
        return ResultSet(ranked=ranked, weaker=weaker)

    def search(
        self,
        query: str,
        limit: int = 50,
        *,
        is_code: bool = False,
        prefix_last: bool = False,
        kinds: set[FileKind] | None = None,
    ) -> ResultSet:
        """Execute hybrid search with inline filters, BM25 + Z-score fusion, gating, and folding."""
        prepared = self._prepare_query(query, is_code=is_code, kinds=kinds)
        if prepared is None:
            return ResultSet()

        target_db, parsed, effective_code, effective_kinds = prepared
        if not parsed.clean_query:
            return self._run_pure_filter_search(target_db, parsed, effective_kinds, limit)

        clean_query = parsed.clean_query
        hinted_kinds = extract_kind_hints(clean_query)
        terms, pure_kind_query = self._parse_search_terms(clean_query, hinted_kinds, is_code=effective_code)
        identifier = extract_identifier(clean_query) if effective_code else None

        best = self._vector_search(target_db, clean_query, is_code=effective_code)
        word_rank, coverage = self._keyword_search(
            target_db,
            terms,
            identifier,
            prefix_last=prefix_last,
            best=best,
        )

        candidates = self._collect_candidates(
            target_db,
            best,
            coverage,
            hinted_kinds,
            pure_kind_query=pure_kind_query,
        )
        file_records = (
            self._filter_records(target_db.find_by_ids(candidates), parsed=parsed, kinds=effective_kinds)
            if candidates
            else {}
        )
        if not file_records:
            return ResultSet()

        scored, resolved_code_places = self._score_candidates(
            target_db,
            file_records,
            best=best,
            coverage=coverage,
            word_rank=word_rank,
            hinted_kinds=hinted_kinds,
            terms=terms,
            identifier=identifier,
            clean_query=clean_query,
            is_code=effective_code,
        )
        if not scored:
            return ResultSet()

        return self._partition_and_assemble(
            target_db,
            file_records,
            scored,
            best=best,
            word_rank=word_rank,
            terms=terms,
            is_code=effective_code,
            pure_kind_query=pure_kind_query,
            resolved_code_places=resolved_code_places,
            limit=limit,
        )
