"""Capture each physical PDF page as independently citable evidence."""

from __future__ import annotations

import hashlib
import io
import re

from pydantic import BaseModel
from pypdf import PdfReader

from powercontext.builtin.artifacts.memory.models import MemoryCitation
from powercontext.builtin.runtime.models import SearchMemoryRequest
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class PDFDocumentInput(BaseModel):
    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    if not value.document_id.strip() or not value.content.startswith(b"%PDF-"):
        raise ValueError("Invalid PDF document")
    digest = hashlib.sha256(value.content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(value.content), strict=False)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise ValueError("PDF requires a password")
        pages = tuple(
            PDFPageInput(
                document_id=value.document_id,
                document_sha256=digest,
                page_number=number,
                text=page.extract_text() or "",
            )
            for number, page in enumerate(reader.pages, start=1)
        )
        if not pages:
            raise ValueError("PDF contains no pages")
        return pages
    except Exception as error:
        if isinstance(error, ValueError):
            raise
        raise ValueError("Unable to parse complete PDF") from error


def _page_id(value: PDFPageInput) -> str:
    identity = f"pdf:{value.document_id}:{value.document_sha256}:{value.page_number}"
    if len(identity) <= 256 and value.document_id == value.document_id.strip():
        return identity
    digest = hashlib.sha256(value.document_id.encode()).hexdigest()
    return f"pdf:{digest}:{value.document_sha256}:{value.page_number}"


class PDFPageAdapter:
    input_class = PDFPageInput
    name = "pdf-page"
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        identity = _page_id(value)
        return PDFPageSource(name=identity, source_id=identity, materialization=SourceMaterialization.CAPTURED,
                             **value.model_dump())

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(document_id=source.document_id, document_sha256=source.document_sha256,
                            page_number=source.page_number, text=source.text)


class PDFPageProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(source_type="pdf-page", source_id=source.source_id,
                            content=f"{source.document_id}\n{source.text}",
                            metadata={"document_id": source.document_id,
                                      "document_sha256": source.document_sha256,
                                      "page_number": source.page_number})


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(PDFPageAdapter(), projections=(PDFPageProjection(),))


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    if not isinstance(question, str) or not question.strip() or type(limit) is not int or limit < 1:
        raise ValueError("Invalid PDF search request")
    # The FTS engine ranks the complete question. A short content-word query
    # supplements it when conversational filler dominates the question.
    tokens = re.findall(r"[a-z]+|\d+", question.lower())
    stop = {"a", "an", "the", "of", "to", "for", "in", "on", "is", "are", "was", "were", "what", "which",
            "how", "many", "much", "did", "does", "do", "and", "or", "by", "with", "from", "as", "it"}
    focused = " ".join(token for token in tokens if token not in stop)
    searches = [focused, question] if focused and focused != question.lower() else [question]
    found: list[MemoryCitation] = []
    seen: set[tuple[str, str, str]] = set()
    for query in searches:
        result = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=min(max(limit, 20), 100)))
        for hit in result.hits:
            key = (hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id)
            if key in seen:
                continue
            seen.add(key)
            found.append(MemoryCitation(memory_ref=hit.memory_ref, entry_id=hit.entry_id,
                                        entry_version_id=hit.entry_version_id))
            if len(found) >= limit:
                return tuple(found)
    return tuple(found)
