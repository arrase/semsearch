"""PDF extractor with digital text extraction and visual page rendering."""

import base64
import pymupdf as fitz
from semsearch.core.search_text import has_words
from semsearch.core.types import ExtractedSegment, Modality, SegmentKind


def extract_pdf(
    path: str,
    start_page: int = 0,
    max_pages: int = 30,
) -> tuple[list[ExtractedSegment], bool]:
    """Extract pages from a PDF document in two stages without OCR."""
    with fitz.open(path) as doc:
        total_pages = len(doc)
        end_page = min(start_page + max_pages, total_pages)
        is_complete = end_page >= total_pages

        segments: list[ExtractedSegment] = []

        for page_num in range(start_page, end_page):
            page = doc[page_num]
            text = page.get_text().strip()

            if len(text) >= 200 and has_words(text):
                snippet = text[:300].replace("\n", " ").strip()
                segments.append(
                    ExtractedSegment(
                        kind=SegmentKind.PAGE,
                        modality=Modality.TEXT,
                        loc=float(page_num + 1),
                        excerpt=snippet,
                        payload=text,
                    )
                )
            else:
                rect = page.rect
                max_side = max(rect.width, rect.height)
                target_scale = 150.0 / 72.0
                if max_side * target_scale > 1400.0 and max_side > 0:
                    scale = 1400.0 / max_side
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
                else:
                    pix = page.get_pixmap(dpi=150)

                img_bytes = pix.tobytes("jpeg")
                b64_data = base64.b64encode(img_bytes).decode("ascii")
                clean_text = " ".join(text[:300].split())
                excerpt = clean_text if clean_text and has_words(clean_text) else f"Page {page_num + 1} (visual)"
                segments.append(
                    ExtractedSegment(
                        kind=SegmentKind.PAGE_IMAGE,
                        modality=Modality.IMAGE,
                        loc=float(page_num + 1),
                        excerpt=excerpt,
                        payload={"image": b64_data},
                    )
                )

        return segments, is_complete
