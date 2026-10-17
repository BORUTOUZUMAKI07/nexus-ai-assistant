"""
Static Corpus Service ("CAG-lite" / static knowledge injection).

Loads a fixed shared knowledge base from a file or directory and returns a
stable, cache-friendly prefix string to inject into the system prompt.

Off by default (`STATIC_CORPUS_ENABLED=False`). Fail-open: missing path,
unreadable files, or I/O errors return empty strings and never raise at
runtime. The loaded content is cached in-process (by mtime + size) so repeated
compiles avoid disk I/O. It does **not** avoid retrieval; on free tiers it
only benefits if the provider's automatic prefix caching hits on the stable
prefix. Deliberately opt-in.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

_ALLOWED_EXTS = {".md", ".txt", ".rst"}


class _CorpusCache:
    __slots__ = ("mtime", "size", "content", "fingerprint")

    def __init__(self, mtime: float, size: int, content: str, fingerprint: str):
        self.mtime = mtime
        self.size = size
        self.content = content
        self.fingerprint = fingerprint


class StaticCorpusService:
    def __init__(self) -> None:
        self._cache: _CorpusCache | None = None

    def _collect_files(self, root: Path) -> list[Path]:
        if root.is_file():
            return [root] if root.suffix.lower() in _ALLOWED_EXTS else []
        if not root.is_dir():
            return []
        files: list[Path] = []
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix.lower() in _ALLOWED_EXTS:
                files.append(p)
        return files

    def _load_raw(self, root: Path) -> str:
        files = self._collect_files(root)
        if not files:
            return ""
        parts: list[str] = []
        for p in files:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                logger.warning(
                    "static_corpus_read_failed",
                    path=str(p),
                    error_type=type(exc).__name__,
                )
                continue
            parts.append(f"# {p.name}\n\n{text}")
        raw = "\n\n---\n\n".join(parts)
        maxc = settings.STATIC_CORPUS_MAX_CHARS
        if maxc > 0 and len(raw) > maxc:
            raw = raw[:maxc].rsplit(" ", 1)[0]
        return raw.strip()

    def _fingerprint(self, root: Path) -> tuple[str, float, int]:
        files = self._collect_files(root)
        if not files:
            return ("empty", 0.0, 0)
        mtime = max(p.stat().st_mtime for p in files)
        size = sum(p.stat().st_size for p in files)
        h = hashlib.sha256()
        h.update(str(mtime).encode("utf-8"))
        h.update(str(size).encode("utf-8"))
        for p in files:
            h.update(str(p.resolve()).encode("utf-8"))
            # include basename for stability
            h.update(p.name.encode("utf-8"))
        return (h.hexdigest()[:16], mtime, size)

    def load(self, *, force: bool = False) -> str:
        if not settings.STATIC_CORPUS_ENABLED:
            self._cache = None
            return ""
        path = settings.STATIC_CORPUS_PATH or ""
        if not path:
            self._cache = None
            return ""
        root = Path(path)
        if not root.exists():
            logger.warning("static_corpus_path_missing", path=str(root))
            self._cache = None
            return ""
        fp, mtime, size = self._fingerprint(root)
        if not force and self._cache is not None:
            c = self._cache
            if c.fingerprint == fp and abs(c.mtime - mtime) < 1e-9:
                return c.content
        content = self._load_raw(root)
        self._cache = _CorpusCache(mtime=mtime, size=size, content=content, fingerprint=fp)
        if content:
            logger.debug("static_corpus_loaded", chars=len(content), files=len(self._collect_files(root)))
        return content

    def build_prefix(self) -> str:
        corpus = self.load()
        if not corpus:
            return ""
        return f"# Static Knowledge Base\n\n{corpus}"


static_corpus_service = StaticCorpusService()
