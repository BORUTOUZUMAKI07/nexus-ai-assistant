"""RAG ingestion text-extraction tests (PDF / binary-safety)."""
from pathlib import Path

from backend.app.services.rag.ingest import IngestionService


def test_extract_text_pdf_uses_pymupdf_and_extracts_text(tmp_path: Path) -> None:
    """A real PDF pulls its text layer via fitz (no raw-binary fallback)."""
    import pymupdf as fitz  # pymupdf is a declared project dependency

    pdf_path = tmp_path / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Dynamic Programming recursion")
    doc.save(str(pdf_path))
    doc.close()

    text = IngestionService().extract_text(pdf_path)

    assert "Dynamic Programming recursion" in text
    assert "--- Page 1 ---" in text
    # No binary garbage may slip through.
    assert "\x00" not in text


def test_extract_text_binary_bytes_with_nul_stripped(tmp_path: Path) -> None:
    """Binary garbage must be sanitized of NUL bytes before DB writes."""
    raw = tmp_path / "doc.bin"
    raw.write_bytes(b"hello\x00world\x00")
    text = IngestionService().extract_text(raw)
    assert text == "helloworld"
    assert "\x00" not in text
