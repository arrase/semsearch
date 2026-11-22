"""Source code and document text preview with syntax highlighting, gutter, and diacritic-safe matches."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QVBoxLayout, QWidget

from semsearch.config import AppConfig
from semsearch.core.search_text import extract_terms, find_term_matches
from semsearch.core.types import SearchHit
from semsearch.gui.preview.loader import PreviewLoader, read_preview_text
from semsearch.gui.preview.metadata import MetadataCard
from semsearch.gui.theme import LIGHT_PALETTE, get_mono_font, get_palette

MAX_HIGHLIGHT_SELECTIONS = 200

_BMP_MAX = 0xFFFF
_DOC_SUFFIXES = frozenset({".docx", ".odt", ".rtf", ".html", ".htm", ".txt", ".md", ".markdown", ".rst"})

_KEYWORDS = (
    "def",
    "class",
    "fn",
    "func",
    "interface",
    "struct",
    "enum",
    "trait",
    "impl",
    "type",
    "return",
    "import",
    "from",
    "export",
    "default",
    "if",
    "elif",
    "else",
    "for",
    "while",
    "loop",
    "match",
    "switch",
    "case",
    "break",
    "continue",
    "try",
    "except",
    "finally",
    "raise",
    "catch",
    "throw",
    "with",
    "as",
    "pass",
    "yield",
    "async",
    "await",
    "const",
    "let",
    "var",
    "mut",
    "pub",
    "use",
    "mod",
    "package",
    "defer",
    "go",
    "chan",
    "select",
    "static",
    "extern",
    "inline",
    "typedef",
    "sizeof",
    "void",
    "int",
    "float",
    "double",
    "char",
    "bool",
    "true",
    "false",
    "True",
    "False",
    "None",
    "null",
    "undefined",
    "self",
    "this",
    "super",
    "new",
    "in",
    "not",
    "and",
    "or",
    "is",
    "lambda",
    "where",
)
_KEYWORDS_RE = re.compile(r"\b(?:" + "|".join(_KEYWORDS) + r")\b")
_DEFINITION_RE = re.compile(r"\b(?:def|class|fn|func|interface|struct)\s+([A-Za-z_]\w*)")
_TOKEN_START_RE = re.compile(r"""["'`#]|//""")


def _find_quoted_end(text: str, start: int, quote: str) -> int:
    """Return index immediately after the matching closing quote, or -1 if unclosed."""
    i = start + 1
    length = len(text)
    while i < length:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == quote:
            return i + 1
        i += 1
    return -1


def _iter_string_or_comment_spans(text: str) -> Iterator[tuple[int, int, bool]]:
    """Yield non-overlapping (start, end, is_comment) spans in left-to-right order."""
    pos = 0
    while (match := _TOKEN_START_RE.search(text, pos)) is not None:
        start = match.start()
        token = match.group(0)
        if token in {"#", "//"}:
            line_end = text.find("\n", start)
            end = len(text) if line_end == -1 else line_end
            yield start, end, True
            pos = end
            continue
        end = _find_quoted_end(text, start, token)
        if end == -1:
            pos = start + 1
            continue
        yield start, end, False
        pos = end


def _build_utf16_offset_map(text: str) -> list[int]:
    """Map Python character indices (including len(text)) to Qt UTF-16 code-unit positions."""
    mapping: list[int] = []
    qt_pos = 0
    for ch in text:
        mapping.append(qt_pos)
        qt_pos += 2 if ord(ch) > _BMP_MAX else 1
    mapping.append(qt_pos)
    return mapping


def _collect_match_spans(text: str, terms: list[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for term in terms:
        for span in find_term_matches(term, text):
            spans.append(span)
            if len(spans) >= MAX_HIGHLIGHT_SELECTIONS:
                return sorted(spans)
    spans.sort()
    return spans


class CodeSyntaxHighlighter(QSyntaxHighlighter):
    """Lightweight lexical syntax highlighter for common programming languages."""

    def __init__(self, parent: QTextDocument) -> None:
        super().__init__(parent)
        self._enabled = True

        self._keyword_fmt = QTextCharFormat()
        self._keyword_fmt.setFontWeight(QFont.Weight.DemiBold)

        self._def_fmt = QTextCharFormat()
        self._def_fmt.setFontWeight(QFont.Weight.DemiBold)

        self._string_fmt = QTextCharFormat()

        self._comment_fmt = QTextCharFormat()
        self._comment_fmt.setFontItalic(True)
        self._apply_colors()

    def _apply_colors(self) -> None:
        is_light = get_palette() == LIGHT_PALETTE
        self._keyword_fmt.setForeground(QColor("#7C3AED" if is_light else "#C084FC"))
        self._def_fmt.setForeground(QColor("#2563EB" if is_light else "#60A5FA"))
        self._string_fmt.setForeground(QColor("#059669" if is_light else "#34D399"))
        self._comment_fmt.setForeground(QColor("#64748B" if is_light else "#6B7280"))

    def refresh_theme(self) -> None:
        """Update syntax token colors for the active theme and rehighlight."""
        self._apply_colors()
        self.rehighlight()

    def set_enabled(self, *, enabled: bool) -> None:
        """Enable or disable syntax highlighting and rehighlight when toggled."""
        if self._enabled != enabled:
            self._enabled = enabled
            self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        """Apply keyword, definition, string, and comment formatting to a single text block."""
        if not self._enabled or not text:
            return

        utf16_map = _build_utf16_offset_map(text) if any(ord(ch) > _BMP_MAX for ch in text) else None

        def apply_span(start: int, end: int, fmt: QTextCharFormat) -> None:
            qt_start = utf16_map[start] if utf16_map is not None else start
            qt_end = utf16_map[end] if utf16_map is not None else end
            self.setFormat(qt_start, qt_end - qt_start, fmt)

        for match in _KEYWORDS_RE.finditer(text):
            apply_span(match.start(), match.end(), self._keyword_fmt)

        for match in _DEFINITION_RE.finditer(text):
            apply_span(match.start(1), match.end(1), self._def_fmt)

        for start, end, is_comment in _iter_string_or_comment_spans(text):
            apply_span(start, end, self._comment_fmt if is_comment else self._string_fmt)


class LineNumberGutter(QWidget):
    """Line number gutter for code preview widget."""

    def __init__(self, editor: CodeEditor) -> None:
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        """Return preferred gutter width."""
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event: QPaintEvent) -> None:
        """Delegate gutter rendering to parent CodeEditor."""
        self.editor.paint_gutter(event)


class CodeEditor(QPlainTextEdit):
    """Read-only code editor with line number gutter and lightweight syntax highlighting."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFont(get_mono_font(12))
        self.highlighter = CodeSyntaxHighlighter(self.document())
        self.gutter = LineNumberGutter(self)

        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self._update_gutter_width()

    def gutter_width(self) -> int:
        """Compute pixel width required to render line numbers."""
        digits = max(1, len(str(self.blockCount())))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Reposition gutter geometry when editor resizes."""
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, event: QPaintEvent) -> None:
        """Paint visible line numbers inside the editor gutter."""
        palette = get_palette()
        painter = QPainter(self.gutter)
        painter.fillRect(event.rect(), QColor(palette.bg_gutter))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())

        painter.setPen(QColor(palette.text_muted))
        painter.setFont(self.font())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number_str = str(block_number + 1)
                painter.drawText(
                    0,
                    top,
                    self.gutter.width() - 6,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight,
                    number_str,
                )
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1

        painter.end()


class CodePreviewWidget(QWidget):
    """Source code and document text preview with gutter, syntax colors, and match highlights."""

    def __init__(
        self,
        config: AppConfig | None = None,
        loader: PreviewLoader | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._loader = loader if loader is not None else PreviewLoader(self)
        self._owns_loader = loader is None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.editor = CodeEditor()
        self.meta_card = MetadataCard(config=config)

        layout.addWidget(self.editor, stretch=1)
        layout.addWidget(self.meta_card)
        self.refresh_theme()

    def refresh_theme(self) -> None:
        """Apply active theme palette to code editor gutter, syntax colors, and metadata card."""
        self.editor.highlighter.refresh_theme()
        self.editor.gutter.update()
        self.meta_card.refresh_theme()

    def retranslate_ui(self) -> None:
        """Refresh translated metadata card labels."""
        self.meta_card.retranslate_ui()

    @staticmethod
    def _highlight_query_terms(
        doc: QTextDocument,
        query: str,
    ) -> tuple[list[QTextEdit.ExtraSelection], QTextCursor | None]:
        if not query:
            return [], None
        text = doc.toPlainText()
        if not text:
            return [], None

        terms = extract_terms(query) or [t for t in query.lower().split() if len(t) > 1]
        if not terms:
            return [], None

        spans = _collect_match_spans(text, terms)
        if not spans:
            return [], None

        utf16_map = _build_utf16_offset_map(text) if any(ord(ch) > _BMP_MAX for ch in text) else None
        bg = QColor(250, 204, 21, 140)
        fg = QColor("#0F1115")

        selections: list[QTextEdit.ExtraSelection] = []
        first_match_cursor: QTextCursor | None = None
        for start, end in spans:
            qt_start = utf16_map[start] if utf16_map is not None else start
            qt_end = utf16_map[end] if utf16_map is not None else end
            match_cursor = QTextCursor(doc)
            match_cursor.setPosition(qt_start)
            match_cursor.setPosition(qt_end, QTextCursor.MoveMode.KeepAnchor)
            if first_match_cursor is None:
                first_match_cursor = QTextCursor(match_cursor)
            term_sel = QTextEdit.ExtraSelection()
            term_sel.format.setBackground(bg)
            term_sel.format.setForeground(fg)
            term_sel.cursor = match_cursor
            selections.append(term_sel)
        return selections, first_match_cursor

    @staticmethod
    def _find_excerpt_cursor(doc: QTextDocument, excerpt: str) -> QTextCursor | None:
        if not excerpt:
            return None
        clean_excerpt = excerpt.replace("«", "").replace("»", "").replace("…", "").strip()
        if not clean_excerpt:
            return None
        first_line_snip = clean_excerpt.splitlines()[0].strip()[:60]
        if not first_line_snip:
            return None
        ex_cursor = doc.find(first_line_snip)
        return None if ex_cursor.isNull() else ex_cursor

    @staticmethod
    def _build_line_selection(doc: QTextDocument, line: int) -> tuple[QTextEdit.ExtraSelection, QTextCursor]:
        cursor = QTextCursor(doc)
        block = doc.findBlockByNumber(line - 1)
        if block.isValid():
            cursor = QTextCursor(block)
        line_selection = QTextEdit.ExtraSelection()
        line_selection.format.setBackground(QColor(250, 204, 21, 90))
        line_selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)  # noqa: FBT003
        line_selection.cursor = cursor
        return line_selection, cursor

    @staticmethod
    def _build_excerpt_selection(excerpt_cursor: QTextCursor) -> QTextEdit.ExtraSelection:
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(QColor(99, 102, 241, 70))
        sel.cursor = excerpt_cursor
        return sel

    def _apply_content(self, content: str, line: int, query: str, excerpt: str) -> None:
        self.editor.setPlainText(content)

        doc = self.editor.document()
        cursor = QTextCursor(doc)
        selections: list[QTextEdit.ExtraSelection] = []

        if line > 0:
            line_sel, cursor = self._build_line_selection(doc, line)
            selections.append(line_sel)

        term_selections, first_match_cursor = self._highlight_query_terms(doc, query)
        selections.extend(term_selections)

        if line <= 0:
            if first_match_cursor is not None:
                cursor = first_match_cursor
            else:
                excerpt_cursor = self._find_excerpt_cursor(doc, excerpt)
                if excerpt_cursor is not None:
                    cursor = excerpt_cursor
                    selections.append(self._build_excerpt_selection(excerpt_cursor))

        self.editor.setExtraSelections(selections)
        self.editor.setTextCursor(cursor)
        self.editor.centerCursor()

    def load_code(
        self,
        path: str,
        name: str,
        line: int = 0,
        *,
        size: int = 0,
        mtime: float = 0.0,
        reason: str = "",
        query: str = "",
        excerpt: str = "",
        wrap_lines: bool = False,
        hit: SearchHit | None = None,
        async_load: bool = False,
    ) -> None:
        """Load code or document content and highlight target line, query matches, or semantic excerpt."""
        self._loader.cancel("code")
        target_path = Path(path)
        is_doc_file = wrap_lines or target_path.suffix.lower() in _DOC_SUFFIXES
        self.editor.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.WidgetWidth if is_doc_file else QPlainTextEdit.LineWrapMode.NoWrap
        )
        self.editor.highlighter.set_enabled(enabled=not is_doc_file)
        self.meta_card.update_metadata(name, path, size, mtime, reason, hit=hit)

        if async_load:
            self._apply_content(excerpt, line, query, excerpt)
            self._loader.submit(
                "code",
                lambda: read_preview_text(path, excerpt),
                lambda payload: self._apply_content(str(payload), line, query, excerpt),
            )
            return

        content = read_preview_text(path, excerpt)
        self._apply_content(content, line, query, excerpt)

    def clear(self) -> None:
        """Cancel pending loads and clear editor text, selections, and metadata."""
        self._loader.cancel("code")
        self.editor.clear()
        self.editor.setExtraSelections([])
        self.meta_card.clear()

    def shutdown(self) -> None:
        """Clear editor state and wait for owned loader pool when standalone."""
        self.clear()
        if self._owns_loader:
            self._loader.shutdown()
