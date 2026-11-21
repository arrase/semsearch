"""Unit tests for document extractor and boundary-snapped chunking."""

import zipfile
from pathlib import Path

import pytest
from docx import Document

from semsearch.core.search_text import has_words
from semsearch.core.types import Modality, SegmentKind
from semsearch.extractors import docs as docs_mod
from semsearch.extractors.docs import (
    MAX_ARCHIVE_UNCOMPRESSED_BYTES,
    MAX_TEXT_FILE_BYTES,
    chunk_document,
    extract_doc,
    read_doc_text,
    strip_html,
)


def test_has_words() -> None:
    assert has_words("This is standard English text.") is True
    assert has_words("Python 3.13 programming") is True
    assert has_words("1234567890 9876543210") is False
    assert has_words("--- === +++ !!! ???") is False
    assert has_words("   \t\n  ") is False
    # Exactly 20% letters: 2 letters out of 10 non-whitespace chars
    assert has_words("ab12345678") is True
    # 1 letter out of 10 non-whitespace chars: 10%
    assert has_words("a123456789") is False


def test_strip_html() -> None:
    html = "<html><body><h1>Title</h1><p>Paragraph 1 with <b>bold</b> text.</p><p>Paragraph 2.</p></body></html>"
    stripped = strip_html(html)
    assert "Title" in stripped
    assert "Paragraph 1 with bold text." in stripped
    assert "Paragraph 2." in stripped
    assert "<h1>" not in stripped
    assert "<b>" not in stripped


def test_read_doc_text_formats(tmp_path: Path) -> None:
    # 1. Plain text & Markdown
    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("Plain text notes line 1\nLine 2", encoding="utf-8")
    assert "Plain text notes" in read_doc_text(str(txt_file))

    md_file = tmp_path / "readme.md"
    md_file.write_text("# Markdown Title\nContent goes here.", encoding="utf-8")
    assert "Markdown Title" in read_doc_text(str(md_file))

    # 2. HTML
    html_file = tmp_path / "doc.html"
    html_file.write_text("<p>Hello from HTML</p>", encoding="utf-8")
    assert "Hello from HTML" in read_doc_text(str(html_file))

    # 3. DOCX
    docx_file = tmp_path / "sample.docx"
    doc = Document()
    doc.add_heading("Docx Heading", level=1)
    doc.add_paragraph("Docx paragraph test content.")
    doc.save(str(docx_file))
    docx_text = read_doc_text(str(docx_file))
    assert "Docx Heading" in docx_text
    assert "Docx paragraph test content." in docx_text

    # 4. ODT
    odt_file = tmp_path / "sample.odt"
    content_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
                             xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
      <office:body>
        <office:text>
          <text:h>OpenDocument Title</text:h>
          <text:p>OpenDocument sample body text.</text:p>
        </office:text>
      </office:body>
    </office:document-content>"""
    with zipfile.ZipFile(odt_file, "w") as zf:
        zf.writestr("content.xml", content_xml)
    odt_text = read_doc_text(str(odt_file))
    assert "OpenDocument Title" in odt_text
    assert "OpenDocument sample body text." in odt_text

    # 5. RTF
    rtf_file = tmp_path / "sample.rtf"
    rtf_content = r"{\rtf1\ansi\deff0 {\fonttbl{\f0 Arial;}}\f0\fs24 Hello \b RTF \b0 World!\par Second line.}"
    rtf_file.write_text(rtf_content, encoding="utf-8")
    rtf_text = read_doc_text(str(rtf_file))
    assert "Hello RTF World!" in rtf_text
    assert "Second line." in rtf_text


def test_chunk_document_word_boundaries() -> None:
    # Build text that exceeds 1800 characters
    words = ["word" + str(i) for i in range(500)]
    full_text = " ".join(words)
    assert len(full_text) > 2000

    chunks = chunk_document(full_text, chunk_size=1800, overlap=200)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert isinstance(chunk.payload, str)
        assert len(chunk.payload) <= 1800
        # Assert word boundary snapping: no partial words at start or end
        assert not chunk.payload.startswith(" ")
        assert not chunk.payload.endswith(" ")
        # Every word should be a complete "wordX"
        for w in chunk.payload.split():
            assert w.startswith("word")


def test_extract_doc_segments(tmp_path: Path) -> None:
    doc_path = tmp_path / "test.txt"
    doc_path.write_text("Sentence one about semantic search.\nSentence two about embeddings.", encoding="utf-8")

    segments = extract_doc(str(doc_path))
    assert len(segments) == 1
    seg = segments[0]
    assert seg.kind == SegmentKind.CHUNK
    assert seg.modality == Modality.TEXT
    assert "semantic search" in seg.payload
    assert seg.loc == 0.0


def test_docx_table_extraction(tmp_path: Path) -> None:
    docx_file = tmp_path / "table_sample.docx"
    doc = Document()
    doc.add_heading("Report Summary", level=1)
    doc.add_paragraph("Overview before tables.")

    # Standard table
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Metric"
    table.cell(0, 1).text = "Score"
    table.cell(1, 0).text = "Accuracy"
    table.cell(1, 1).text = "98.5%"

    # Table with merged cells (to test clean deduplication)
    table_merged = doc.add_table(rows=2, cols=2)
    table_merged.cell(0, 0).merge(table_merged.cell(0, 1))
    table_merged.cell(0, 0).text = "Merged Header"
    table_merged.cell(1, 0).text = "Column A"
    table_merged.cell(1, 1).text = "Column B"

    doc.save(str(docx_file))

    extracted = read_doc_text(str(docx_file))
    assert "Report Summary" in extracted
    assert "Overview before tables." in extracted
    assert "Metric" in extracted
    assert "Score" in extracted
    assert "Accuracy" in extracted
    assert "98.5%" in extracted
    assert "Merged Header" in extracted
    # Merged header should only appear once despite spanning two columns
    assert extracted.count("Merged Header") == 1
    assert "Column A" in extracted
    assert "Column B" in extracted

    # Also test extract_doc
    segments = extract_doc(str(docx_file))
    assert len(segments) >= 1
    assert any("Merged Header" in str(s.payload) for s in segments)


def test_reject_legacy_binary_doc(tmp_path: Path) -> None:
    doc_file = tmp_path / "legacy.doc"
    doc_file.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1binary ole2 payload")

    with pytest.raises(ValueError, match=r"Legacy binary \.doc format is not supported"):
        read_doc_text(str(doc_file))

    with pytest.raises(ValueError, match=r"Legacy binary \.doc format is not supported"):
        extract_doc(str(doc_file))


def test_zip_bomb_blocked_for_odt_and_docx(tmp_path: Path) -> None:
    assert MAX_ARCHIVE_UNCOMPRESSED_BYTES == 32 * 1024 * 1024
    oversized_payload = b"0" * (33 * 1024 * 1024)

    bomb_odt = tmp_path / "bomb.odt"
    with zipfile.ZipFile(bomb_odt, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content.xml", oversized_payload)

    with pytest.raises(ValueError, match="Archive exceeds maximum safe uncompressed size"):
        read_doc_text(str(bomb_odt))

    bomb_docx = tmp_path / "bomb.docx"
    with zipfile.ZipFile(bomb_docx, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", oversized_payload)

    with pytest.raises(ValueError, match="Archive exceeds maximum safe uncompressed size"):
        read_doc_text(str(bomb_docx))


def test_text_file_size_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert MAX_TEXT_FILE_BYTES == 10 * 1024 * 1024
    monkeypatch.setattr(docs_mod, "MAX_TEXT_FILE_BYTES", 64)

    for ext in (".txt", ".md", ".markdown", ".rst", ".html", ".htm", ".rtf"):
        large_file = tmp_path / f"large{ext}"
        large_file.write_text("a" * 128, encoding="utf-8")
        with pytest.raises(ValueError, match="maximum safe size"):
            read_doc_text(str(large_file))

    small_rst = tmp_path / "small.rst"
    small_rst.write_text("Short valid reStructuredText.", encoding="utf-8")
    assert read_doc_text(str(small_rst)) == "Short valid reStructuredText."


def test_strip_html_ignores_script_style_noscript_svg_and_head() -> None:
    html = (
        "<!DOCTYPE html><html>"
        "<head><title>Hidden Head Title</title><style>.x { color: red; }</style></head>"
        "<body>"
        "<script>const leaked = 'DO_NOT_INDEX';</script>"
        "<style>body { font-size: 14px; }</style>"
        "<noscript><p>Enable JavaScript notice</p></noscript>"
        "<svg><g><svg><text>Nested SVG Text</text></svg><text>Outer SVG Text</text></g></svg>"
        "<h1>Visible Heading</h1>"
        "<p>Actual document paragraph.</p>"
        "</body></html>"
    )
    stripped = strip_html(html)
    assert "Visible Heading" in stripped
    assert "Actual document paragraph." in stripped
    for forbidden in (
        "Hidden Head Title",
        "color: red",
        "DO_NOT_INDEX",
        "font-size",
        "Enable JavaScript",
        "Nested SVG Text",
        "Outer SVG Text",
    ):
        assert forbidden not in stripped


def test_strip_rtf_ignorable_groups_unicode_and_cp1252(tmp_path: Path) -> None:
    rtf_file = tmp_path / "rich.rtf"
    rtf_content = (
        r"{\rtf1\ansi\deff0 "
        r"{\fonttbl{\f0\fnil Arial;}{\f1\fswiss Helvetica;}}"
        r"{\colortbl ;\red255\green0\blue0;}"
        r"{\*\themedata 504b0304abcdef}"
        r"{\*\datastore {\nested datastore_secret}}"
        r"{\*\latentstyles{\lsdlockedexcept Normal;}}"
        r"\f0\fs24 Caf\'e9 10\'80 espa\u241?ol \u241\'f1 \u-1279?\par "
        r"Second line.}"
    )
    rtf_file.write_text(rtf_content, encoding="utf-8")

    extracted = read_doc_text(str(rtf_file))
    assert "Café" in extracted
    assert "10€" in extracted
    assert "español" in extracted
    assert "ñ" in extracted
    assert "ﬁ" in extracted
    assert "Second line." in extracted
    for ignored in ("Arial", "Helvetica", "504b0304", "datastore_secret", "Normal"):
        assert ignored not in extracted
