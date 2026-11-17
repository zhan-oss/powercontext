"""Capture each physical PDF page as independently citable evidence."""

from __future__ import annotations

import hashlib
import io
import math
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
    tokens = re.findall(r"[a-z]+|\d+", question.lower())
    stop = {"a", "an", "the", "of", "to", "for", "in", "on", "is", "are", "was", "were", "what", "which",
            "how", "many", "much", "did", "does", "do", "and", "or", "by", "with", "from", "as", "it",
            "this", "that", "these", "those", "has", "have", "had", "if", "then", "state", "give", "answer",
            "respond", "question", "using", "use", "shown", "details", "report", "reported", "fy"}
    content = [token for token in tokens if token not in stop]
    # Long natural-language questions often ask for evidence in several parts
    # of a report. Search each part independently, then reserve places for
    # their best pages instead of letting one generic term fill the top 20.
    variants: list[str] = []
    def add_variant(parts: list[str]) -> None:
        query = " ".join(dict.fromkeys(parts))
        if query and query not in variants:
            variants.append(query)

    add_variant(content)
    if len(content) >= 6:
        midpoint = len(content) // 2
        add_variant(content[:midpoint])
        add_variant(content[midpoint:])
    distinctive = [token for token in content if len(token) >= 6 and not token.isdigit()]
    if len(distinctive) >= 2:
        add_variant(distinctive)
    add_variant(tokens)
    # Short overlapping windows keep a focused financial phrase searchable
    # when the complete question combines several facts on different pages.
    # Sample across the entire question so later clauses are represented too.
    if len(content) > 4:
        stride = max(2, math.ceil(len(content) / 9))
        for start in range(0, len(content) - 1, stride):
            add_variant(content[start:start + 5])
    rankings: list[list[MemoryCitation]] = []
    scores: dict[tuple[str, str, str], float] = {}
    citations: dict[tuple[str, str, str], MemoryCitation] = {}
    documents: dict[tuple[str, str, str], str] = {}
    for query in variants:
        result = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=max(limit, 50)))
        ranking: list[MemoryCitation] = []
        for rank, hit in enumerate(result.hits):
            key = (hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id)
            citation = MemoryCitation(memory_ref=hit.memory_ref, entry_id=hit.entry_id,
                                      entry_version_id=hit.entry_version_id)
            citations[key] = citation
            documents[key] = hit.text.partition("\n")[0]
            scores[key] = scores.get(key, 0.0) + 1.0 / (20 + rank)
            ranking.append(citation)
        rankings.append(ranking)

    # Each evidence entry begins with its document ID. In a shared scope,
    # financially similar reports otherwise compete for the same 20 places.
    query_words = set(tokens)
    document_rank: dict[str, float] = {}
    document_affinity: dict[str, int] = {}
    for key, score in scores.items():
        document = documents[key]
        document_rank[document] = document_rank.get(document, 0.0) + score
        name_words = set(re.findall(r"[a-z]+|\d+", document.lower()))
        company_words = {word for word in name_words if len(word) >= 3 and
                         word not in {"inc", "corp", "company", "annual", "report", "form", "and"}}
        document_affinity[document] = max(
            document_affinity.get(document, 0),
            3 * len({word for word in company_words if not word.isdigit()} & query_words)
            + len({word for word in name_words if len(word) == 4 and word.isdigit()} & query_words),
        )
    best_document = None
    if document_affinity and max(document_affinity.values()) > 0:
        best_document = max(document_affinity, key=lambda name:
                            (document_affinity[name], document_rank[name], name))
        scores = {key: score for key, score in scores.items() if documents[key] == best_document}
        rankings = [[citation for citation in ranking if
                     documents[(citation.memory_ref.artifact_id, citation.entry_id,
                                citation.entry_version_id)] == best_document]
                    for ranking in rankings]

    selected: list[MemoryCitation] = []
    selected_keys: set[tuple[str, str, str]] = set()
    def append(citation: MemoryCitation) -> None:
        key = (citation.memory_ref.artifact_id, citation.entry_id, citation.entry_version_id)
        if key not in selected_keys and len(selected) < limit:
            selected_keys.add(key)
            selected.append(citation)

    for key in sorted(scores, key=lambda item: (-scores[item], item))[: max(1, limit // 2)]:
        append(citations[key])
    for rank in range(max((len(ranking) for ranking in rankings), default=0)):
        for ranking in rankings:
            if rank < len(ranking):
                append(ranking[rank])
            if len(selected) >= limit:
                return tuple(selected)
    return tuple(selected)
