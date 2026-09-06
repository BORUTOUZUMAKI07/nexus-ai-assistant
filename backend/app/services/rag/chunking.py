"""
Document Chunking Service.
Implements Markdown Header-Aware Semantic Chunking with token boundaries
and recursive sliding window fallback.
"""
import re
from typing import Any

import litellm
import structlog
from backend.app.services.rag.base import IChunker

logger = structlog.get_logger(__name__)

# Defaults from settings (imported lazily inside methods to keep import-time light)
PARENT_TOKEN_TARGET = 512
CHILD_TOKEN_TARGET = 128
CHILD_OVERLAP_TOKENS = 32


class DocumentChunk:
    def __init__(
        self,
        content: str,
        chunk_index: int,
        token_count: int,
        metadata: dict[str, Any],
        is_parent: bool = True,
        parent_chunk_index: int | None = None,
        contextual_prefix: str | None = None,
    ):
        self.content = content
        self.chunk_index = chunk_index
        self.token_count = token_count
        self.metadata = metadata
        self.is_parent = is_parent
        self.parent_chunk_index = parent_chunk_index
        self.contextual_prefix = contextual_prefix

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "chunk_index": self.chunk_index,
            "token_count": self.token_count,
            "metadata": self.metadata,
            "is_parent": self.is_parent,
            "parent_chunk_index": self.parent_chunk_index,
            "contextual_prefix": self.contextual_prefix,
        }


class ChunkingService(IChunker):
    """
    Splits text, markdown, and code into semantically coherent chunks.
    Preserves heading hierarchies and context spans.
    """

    def __init__(self, max_tokens: int = 512, overlap_tokens: int = 64):
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens

    def count_tokens(self, text: str) -> int:
        try:
            return len(litellm.encode(model="llama-3.3-70b-versatile", text=text))
        except Exception:
            return len(text.split())

    def split_by_markdown_headers(self, text: str) -> list[dict[str, Any]]:
        """
        Splits markdown document by headers (h1, h2, h3).
        """
        header_pattern = r"(^#{1,4}\s+.*$)"
        lines = text.split("\n")
        sections: list[dict[str, Any]] = []
        current_header = "Introduction"
        current_lines: list[str] = []

        for line in lines:
            if re.match(header_pattern, line):
                if current_lines:
                    sections.append({
                        "header": current_header,
                        "content": "\n".join(current_lines).strip(),
                    })
                    current_lines = []
                current_header = line.strip("#").strip()
            current_lines.append(line)

        if current_lines:
            sections.append({
                "header": current_header,
                "content": "\n".join(current_lines).strip(),
            })

        return sections

    def chunk_document(self, text: str, source_metadata: dict[str, Any]) -> list[DocumentChunk]:
        """
        Main chunking pipeline. First splits by structure, then applies sliding window if chunk > max_tokens.
        """
        sections = self.split_by_markdown_headers(text)
        chunks: list[DocumentChunk] = []
        chunk_idx = 0

        for sec in sections:
            header = sec["header"]
            sec_content = sec["content"]
            sec_tokens = self.count_tokens(sec_content)

            if sec_tokens <= self.max_tokens:
                if sec_content.strip():
                    meta = {**source_metadata, "header": header}
                    chunks.append(DocumentChunk(
                        content=sec_content,
                        chunk_index=chunk_idx,
                        token_count=sec_tokens,
                        metadata=meta,
                    ))
                    chunk_idx += 1
            else:
                # Sub-chunk by sentences
                sentences = re.split(r"(?<=[.?!])\s+", sec_content)
                current_sub_chunk: list[str] = []
                current_tokens = 0

                for sent in sentences:
                    sent_tokens = self.count_tokens(sent)
                    if current_tokens + sent_tokens > self.max_tokens and current_sub_chunk:
                        chunk_text = " ".join(current_sub_chunk).strip()
                        meta = {**source_metadata, "header": header}
                        chunks.append(DocumentChunk(
                            content=chunk_text,
                            chunk_index=chunk_idx,
                            token_count=current_tokens,
                            metadata=meta,
                        ))
                        chunk_idx += 1
                        # Overlap: keep last sentence
                        current_sub_chunk = [current_sub_chunk[-1]] if current_sub_chunk else []
                        current_tokens = self.count_tokens(" ".join(current_sub_chunk))

                    current_sub_chunk.append(sent)
                    current_tokens += sent_tokens

                if current_sub_chunk:
                    chunk_text = " ".join(current_sub_chunk).strip()
                    meta = {**source_metadata, "header": header}
                    chunks.append(DocumentChunk(
                        content=chunk_text,
                        chunk_index=chunk_idx,
                        token_count=current_tokens,
                        metadata=meta,
                    ))
                    chunk_idx += 1

        logger.info("document_chunked", total_chunks=len(chunks), metadata=source_metadata)
        return chunks

    # ─── Parent-Child Chunking ─────────────────────────────────────────────────

    def generate_contextual_prefix(
        self,
        title: str | None = None,
        header_hierarchy: str | None = None,
    ) -> str:
        """
        Builds a concise context prefix (document title + heading hierarchy)
        that preserves global document context inside short child chunks.
        """
        parts = [p.strip() for p in (title, header_hierarchy) if p and p.strip()]
        return " | ".join(parts)

    def split_children(
        self,
        parent: DocumentChunk,
        child_tokens: int = CHILD_TOKEN_TARGET,
        overlap_tokens: int = CHILD_OVERLAP_TOKENS,
        start_index: int = 0,
    ) -> list[DocumentChunk]:
        """
        Subdivides a parent chunk (512t) into child chunks (128t) with a
        32-token sliding overlap. Each child retains the parent header/context
        metadata and carries parent_chunk_index for later relational linkage.
        """
        tokens = parent.content.split()
        if not tokens:
            return []

        child_token_count = max(1, child_tokens)
        step = max(1, child_token_count - max(0, overlap_tokens))
        children: list[DocumentChunk] = []
        idx = start_index

        for start in range(0, len(tokens), step):
            window = tokens[start : start + child_token_count]
            window_text = " ".join(window).strip()
            if not window_text:
                continue
            children.append(
                DocumentChunk(
                    content=window_text,
                    chunk_index=idx,
                    token_count=len(window),
                    metadata={**parent.metadata},
                    is_parent=False,
                    parent_chunk_index=parent.chunk_index,
                )
            )
            idx += 1

        return children

    def chunk_with_children(
        self,
        text: str,
        source_metadata: dict[str, Any],
        parent_tokens: int = PARENT_TOKEN_TARGET,
        child_tokens: int = CHILD_TOKEN_TARGET,
        overlap_tokens: int = CHILD_OVERLAP_TOKENS,
    ) -> list[dict[str, Any]]:
        """
        Full parent-child ingestion pipeline:
        1. Split document into 512-token parent chunks (header-aware).
        2. Subdivide each parent into 128-token child chunks.
        3. Attach a contextual prefix (title + heading) to every child.

        Returns a list of:
          {"parent": DocumentChunk, "children": [DocumentChunk, ...]}
        """
        parents = self.chunk_document(text, source_metadata=source_metadata)
        if not parents:
            return []

        title = source_metadata.get("title") or source_metadata.get("filename")
        groups: list[dict[str, Any]] = []
        next_child_index = 0

        for parent in parents:
            children = self.split_children(
                parent,
                child_tokens=child_tokens,
                overlap_tokens=overlap_tokens,
                start_index=next_child_index,
            )
            header_hierarchy = parent.metadata.get("header") or "Introduction"
            prefix = self.generate_contextual_prefix(title=title, header_hierarchy=header_hierarchy)
            for child in children:
                child.contextual_prefix = prefix
            next_child_index += len(children)
            groups.append({"parent": parent, "children": children})

        logger.info(
            "parent_child_chunked",
            parent_count=len(parents),
            child_count=next_child_index,
            metadata=source_metadata,
        )
        return groups


# Singleton instance for backwards-compatibility
chunking_service = ChunkingService()


def get_chunker() -> IChunker:
    """Dependency provider returning the active IChunker implementation."""
    return chunking_service
