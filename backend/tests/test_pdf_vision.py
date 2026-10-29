"""
Visual PDF ingestion fallback (Gap 2).

A scanned / image-only PDF has no text layer, so ``extract_text`` returns
nothing and the document is rejected as "empty content" -- even though it is a
readable page that only a vision model can read. These tests pin both layers:
the renderer/reader in :mod:`backend.app.services.tools.pdf_vision`, and the
decision to use it (only for a thin text layer, only for PDFs, only when
enabled, and never worse than the text layer).
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pymupdf as fitz  # type: ignore[import-not-found]
from backend.app.services.rag import ingest as ingest_mod
from backend.app.services.tools import pdf_vision


def _make_pdf(path: Path, *, pages: int = 1, text: str = "sample") -> Path:
    doc = fitz.open()
    try:
        for _ in range(pages):
            page = doc.new_page()
            if text:
                # insert_textbox, not insert_text: the latter clips at the page
                # edge (~97 chars), so a "rich" text layer would silently be a
                # thin one and the test would assert the wrong thing.
                page.insert_textbox(fitz.Rect(72, 72, 520, 700), text)
        doc.save(str(path))
    finally:
        doc.close()
    return path


def _service() -> ingest_mod.IngestionService:
    """An IngestionService with no vector store / retriever (extraction only)."""
    return ingest_mod.IngestionService(vector_store=object(), retriever=object())


# ── renderer ─────────────────────────────────────────────────────────────────


def test_render_pdf_pages_returns_one_png_per_page(tmp_path):
    pdf = _make_pdf(tmp_path / "doc.pdf", pages=3)
    images = pdf_vision.render_pdf_pages(pdf, max_pages=10, dpi=72)
    assert len(images) == 3
    assert all(image[:4] == b"\x89PNG" for image in images)


def test_render_pdf_pages_respects_the_page_ceiling(tmp_path):
    pdf = _make_pdf(tmp_path / "doc.pdf", pages=5)
    assert len(pdf_vision.render_pdf_pages(pdf, max_pages=2, dpi=72)) == 2


async def test_rendering_happens_off_the_event_loop_thread(tmp_path, monkeypatch):
    """A synchronous PyMuPDF render must not run on the loop that serves requests."""
    pdf = _make_pdf(tmp_path / "doc.pdf", pages=1)
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}

    def fake_render(path, *, max_pages, dpi):
        seen["thread"] = threading.get_ident()
        return [b"\x89PNG"]

    async def reader(png: bytes, page_number: int) -> str:
        return "text"

    monkeypatch.setattr(pdf_vision, "render_pdf_pages", fake_render)

    await pdf_vision.extract_text_from_pdf(pdf, reader=reader)

    assert seen["thread"] != loop_thread, "rendering blocked the event loop"


# ── reader / transcription ───────────────────────────────────────────────────


async def test_extract_text_from_pdf_reads_every_rendered_page(tmp_path):
    pdf = _make_pdf(tmp_path / "doc.pdf", pages=2)
    seen: list[int] = []

    async def reader(png: bytes, page_number: int) -> str:
        seen.append(page_number)
        assert png[:4] == b"\x89PNG"
        return f"body {page_number}"

    result = await pdf_vision.extract_text_from_pdf(pdf, reader=reader)

    assert seen == [1, 2]
    assert "--- Page 1 ---\nbody 1" in result
    assert "--- Page 2 ---\nbody 2" in result


async def test_a_failed_page_does_not_cost_the_others(tmp_path):
    pdf = _make_pdf(tmp_path / "doc.pdf", pages=2)

    async def reader(png: bytes, page_number: int) -> str:
        if page_number == 1:
            raise RuntimeError("page 1 unreadable")
        return "survivor"

    result = await pdf_vision.extract_text_from_pdf(pdf, reader=reader)

    assert "survivor" in result
    assert "--- Page 1 ---" not in result


async def test_a_hung_page_read_is_bounded_by_the_timeout(tmp_path):
    pdf = _make_pdf(tmp_path / "doc.pdf", pages=1)

    async def slow_reader(png: bytes, page_number: int) -> str:
        await asyncio.sleep(5)
        return "too late"

    result = await pdf_vision.extract_text_from_pdf(
        pdf, reader=slow_reader, timeout_seconds=0.05
    )

    assert result == ""


async def test_blank_pages_yield_no_text(tmp_path):
    pdf = _make_pdf(tmp_path / "doc.pdf", pages=1)

    async def blank_reader(png: bytes, page_number: int) -> str:
        return ""

    assert await pdf_vision.extract_text_from_pdf(pdf, reader=blank_reader) == ""


# ── ingest decision ──────────────────────────────────────────────────────────


async def test_vision_fallback_fires_on_a_thin_text_layer(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path / "scan.pdf", text="hi")

    async def fake_vision(path, **kwargs):
        return "--- Page 1 ---\n" + ("recognized " * 40)

    monkeypatch.setattr(pdf_vision, "extract_text_from_pdf", fake_vision)
    monkeypatch.setattr(ingest_mod.settings, "PDF_VISION_ENABLED", True)

    result = await _service().extract_text_with_vision(pdf)

    assert "recognized" in result


async def test_vision_fallback_is_skipped_when_the_text_layer_is_rich(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path / "report.pdf", text="x" * 500)
    calls = {"n": 0}

    async def fake_vision(path, **kwargs):
        calls["n"] += 1
        return "should not have been called"

    monkeypatch.setattr(pdf_vision, "extract_text_from_pdf", fake_vision)
    monkeypatch.setattr(ingest_mod.settings, "PDF_VISION_ENABLED", True)

    result = await _service().extract_text_with_vision(pdf)

    assert calls["n"] == 0
    assert result.count("x") >= 500


async def test_vision_fallback_is_off_when_disabled(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path / "scan.pdf", text="hi")
    calls = {"n": 0}

    async def fake_vision(path, **kwargs):
        calls["n"] += 1
        return "recognized"

    monkeypatch.setattr(pdf_vision, "extract_text_from_pdf", fake_vision)
    monkeypatch.setattr(ingest_mod.settings, "PDF_VISION_ENABLED", False)

    await _service().extract_text_with_vision(pdf)

    assert calls["n"] == 0


async def test_vision_never_makes_an_extraction_worse(tmp_path, monkeypatch):
    """A vision pass that returns less than the text layer must be discarded."""
    pdf = _make_pdf(tmp_path / "scan.pdf", text="page header text")

    async def worse_vision(path, **kwargs):
        return "x"

    monkeypatch.setattr(pdf_vision, "extract_text_from_pdf", worse_vision)
    monkeypatch.setattr(ingest_mod.settings, "PDF_VISION_ENABLED", True)

    result = await _service().extract_text_with_vision(pdf)

    assert "page header text" in result


async def test_a_failing_vision_pass_returns_the_text_layer(tmp_path, monkeypatch):
    pdf = _make_pdf(tmp_path / "scan.pdf", text="page header text")

    async def broken_vision(path, **kwargs):
        raise RuntimeError("vision provider down")

    monkeypatch.setattr(pdf_vision, "extract_text_from_pdf", broken_vision)
    monkeypatch.setattr(ingest_mod.settings, "PDF_VISION_ENABLED", True)

    result = await _service().extract_text_with_vision(pdf)

    assert "page header text" in result


async def test_vision_fallback_never_runs_for_a_non_pdf(tmp_path, monkeypatch):
    txt = tmp_path / "note.txt"
    txt.write_text("just a short note", encoding="utf-8")
    calls = {"n": 0}

    async def fake_vision(path, **kwargs):
        calls["n"] += 1
        return "nope"

    monkeypatch.setattr(pdf_vision, "extract_text_from_pdf", fake_vision)
    monkeypatch.setattr(ingest_mod.settings, "PDF_VISION_ENABLED", True)

    result = await _service().extract_text_with_vision(txt)

    assert calls["n"] == 0
    assert "just a short note" in result
