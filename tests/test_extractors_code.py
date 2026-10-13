"""Unit tests for code and Jupyter notebook extractor."""

import json
from pathlib import Path
from semsearch.core.types import Modality, SegmentKind
from semsearch.extractors.code import (
    extract_code,
    extract_notebook,
)


def test_extract_code_definition_detection(tmp_path: Path) -> None:
    code_path = tmp_path / "sample.py"
    content = (
        "# Header comments\n"
        "# Another comment\n"
        "\n"
        "class DataProcessor:\n"
        "    def __init__(self):\n"
        "        self.val = 42\n"
        "\n"
        "    def run(self):\n"
        "        return self.val\n"
    )
    code_path.write_text(content, encoding="utf-8")

    segments = extract_code(str(code_path))
    assert len(segments) == 1
    seg = segments[0]
    assert seg.loc == 1.0
    # Line 4 is 'class DataProcessor:'
    assert seg.focus == 4.0


def test_extract_code_whole_line_chunking(tmp_path: Path) -> None:
    code_path = tmp_path / "long_code.py"
    # Create 100 lines of code (~3500 chars)
    lines = []
    for i in range(1, 101):
        if i % 25 == 0:
            lines.append(f"\ndef function_{i}():\n    pass\n")
        else:
            lines.append(f"    x_{i} = {i} * 2 + 1\n")

    code_path.write_text("".join(lines), encoding="utf-8")

    segments = extract_code(str(code_path), chunk_chars=800)
    assert len(segments) > 1

    for s in segments:
        # Whole lines check: must end with newline
        assert isinstance(s.payload, str)
        assert s.payload.endswith("\n")
        # Every line inside should be intact
        for l in s.payload.splitlines():
            assert "x_" in l or "function_" in l or "pass" in l or l == ""


def test_extract_notebook_strips_outputs(tmp_path: Path) -> None:
    nb_path = tmp_path / "analysis.ipynb"
    nb_data = {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": ["# Analysis Notebook\n", "This explains our workflow."],
            },
            {
                "cell_type": "code",
                "execution_count": 12,
                "metadata": {},
                "source": ["def compute_metrics(x):\n", "    return x ** 2\n"],
                "outputs": [
                    {
                        "name": "stdout",
                        "output_type": "stream",
                        "text": ["Output that should be stripped 12345\n"],
                    },
                    {
                        "data": {"text/plain": ["Result: 99999"]},
                        "execution_count": 12,
                        "output_type": "execute_result",
                    },
                ],
            },
        ],
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}
        },
        "nbformat": 4,
        "nbformat_minor": 2,
    }
    nb_path.write_text(json.dumps(nb_data), encoding="utf-8")

    segments = extract_notebook(str(nb_path))
    assert len(segments) >= 1
    assert segments[0].kind == SegmentKind.CELLS
    assert segments[0].loc == 1.0
    assert segments[0].loc_end == 2.0
    combined_text = "".join(str(s.payload) for s in segments)


    # Must contain code and markdown
    assert "Analysis Notebook" in combined_text
    assert "def compute_metrics" in combined_text

    # MUST NOT contain outputs or execution counts
    assert "Output that should be stripped" not in combined_text
    assert "Result: 99999" not in combined_text
    assert "12345" not in combined_text


def test_code_to_segment(tmp_path: Path) -> None:
    code_path = tmp_path / "test.rs"
    code_path.write_text("fn main() {\n    println!(\"Hello\");\n}\n", encoding="utf-8")

    segments = extract_code(str(code_path))
    assert len(segments) == 1
    seg = segments[0]
    assert seg.kind == SegmentKind.LINES
    assert seg.modality == Modality.TEXT
    assert seg.loc == 1.0
    assert seg.focus == 1.0
    assert "fn main" in seg.payload
