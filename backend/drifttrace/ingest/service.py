"""Paper resolution and full-text acquisition (pipeline steps 1 and 2)."""

import hashlib
import logging
import re
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from drifttrace.config import get_settings
from drifttrace.db.models import Chunk, Paper, Reference
from drifttrace.external import scholarly as api
from drifttrace.external.http import Polite
from drifttrace.ingest.embeddings import embed
from drifttrace.ingest.grobid import process_pdf
from drifttrace.ingest.parsers import parse_jats, parse_plain_pdf, parse_tei
from drifttrace.schemas import CitationMarker, ParsedDoc

log = logging.getLogger(__name__)
Notify = Callable[[str], Awaitable[None]]
MAX_CHUNK_CHARS = 1800
MIN_CHUNK_CHARS = 200


class PaperNotFound(LookupError):
    pass


async def _noop(_: str) -> None:
    return None


def _apply_meta(paper: Paper, meta: api.PaperMeta) -> None:
    for name in ("openalex_id", "s2_id", "title", "year", "venue", "abstract", "work_type", "language", "oa_url"):
        if getattr(paper, name) in (None, "") and getattr(meta, name):
            setattr(paper, name, getattr(meta, name))
    if not paper.authors and meta.authors:
        paper.authors = meta.authors
    paper.is_oa = paper.is_oa or meta.is_oa
    paper.is_retracted = paper.is_retracted or meta.is_retracted
    if meta.related_versions:
        paper.related_versions = meta.related_versions


async def resolve_paper(session: AsyncSession, http: Polite, doi: str) -> Paper:
    """Step 1: fetch metadata from OpenAlex, falling back to Semantic Scholar and Crossref."""
    existing = await session.scalar(select(Paper).where(Paper.doi == doi))
    if existing is not None and existing.title:
        return existing

    openalex = await api.openalex_by_doi(http, doi)
    s2 = None if openalex and openalex.abstract else await api.semantic_scholar_by_doi(http, doi)
    crossref = await api.crossref_by_doi(http, doi)
    if not any((openalex, s2, crossref)):
        raise PaperNotFound(f"No metadata found for DOI {doi}. Check that it is correct.")

    paper = existing or Paper(id=uuid.uuid4(), doi=doi)
    for meta in (openalex, s2, crossref):
        if meta:
            _apply_meta(paper, meta)

    notice = await api.crossref_retraction(http, doi)
    if notice:
        paper.is_retracted = True
        paper.retraction_note = notice

    session.add(paper)
    await session.commit()
    return paper


async def _pdf_urls(http: Polite, paper: Paper, mode: str) -> tuple[list[str], str | None]:
    urls = await api.unpaywall_pdfs(http, paper.doi)
    arxiv_id = None
    match = re.fullmatch(r"10\.48550/arxiv\.(.+)", paper.doi or "", re.I)
    if match:
        arxiv_id = match.group(1)
    openalex = await api.openalex_by_doi(http, paper.doi)
    if openalex:
        arxiv_id = arxiv_id or openalex.arxiv_id
        urls.extend(openalex.pdf_urls)
    if mode == "thorough":
        s2 = await api.semantic_scholar_by_doi(http, paper.doi)
        if s2:
            arxiv_id = arxiv_id or s2.arxiv_id
            urls.extend(s2.pdf_urls)
    urls = list(dict.fromkeys(urls))
    return urls if mode == "thorough" else urls[:2], arxiv_id


def _store_pdf(pdf: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(pdf).hexdigest()
    path = get_settings().upload_dir / f"{digest}.pdf"
    if not path.exists():
        path.write_bytes(pdf)
    return digest, str(path)


async def parse_pdf(pdf: bytes) -> tuple[ParsedDoc, str | None]:
    """Parse with GROBID, falling back to plain-text extraction when its output is unusable."""
    tei = await process_pdf(pdf)
    if tei:
        try:
            return parse_tei(tei), tei
        except ValueError as exc:
            log.warning("Unusable GROBID output, falling back to plain text: %s", exc)
    return parse_plain_pdf(pdf), None


def _commit_parsed(paper: Paper, parsed: ParsedDoc, source: str, tei: str | None) -> None:
    paper.parsed = parsed.model_dump(mode="json")
    paper.fulltext = parsed.full_text()
    paper.tei_xml = tei
    paper.parse_quality = parsed.quality
    paper.fulltext_status = "FULL"
    paper.fulltext_source = source
    paper.fulltext_note = None
    if not paper.abstract and parsed.abstract:
        paper.abstract = parsed.abstract


async def _write_references(session: AsyncSession, paper: Paper, parsed: ParsedDoc) -> None:
    await session.execute(delete(Reference).where(Reference.citing_paper_id == paper.id))
    seen: set[str] = set()
    for ref in parsed.references:
        if ref.key in seen:
            continue
        seen.add(ref.key)
        session.add(
            Reference(
                citing_paper_id=paper.id, ref_index=ref.index, ref_key=ref.key, raw_text=ref.raw_text,
                title=ref.title, year=ref.year, authors=ref.authors, resolved_doi=ref.doi,
            )
        )


def chunk_document(parsed: ParsedDoc | None, abstract: str | None) -> list[dict]:
    """Chunk by paragraph, keeping section labels: short ones merge and long ones split on sentences."""
    chunks: list[dict] = []
    if abstract:
        chunks.append({"section": "Abstract", "kind": "abstract", "text": abstract, "page": 1, "citations": []})
    if parsed is None:
        return chunks

    def citation_json(markers: list[CitationMarker], offset: int) -> list[dict]:
        return [{"marker": m.marker, "ref_keys": m.ref_keys, "start": m.start + offset} for m in markers]

    for section in parsed.sections:
        if section.kind in ("references", "acknowledgements"):
            continue
        pending: dict | None = None
        for paragraph in section.paragraphs:
            pieces: list[dict] = []
            if len(paragraph.text) <= MAX_CHUNK_CHARS:
                pieces.append({"text": paragraph.text, "citations": citation_json(paragraph.citations, 0)})
            else:
                current_text = ""
                current_markers: list[dict] = []
                for sentence in paragraph.sentences:
                    if current_text and len(current_text) + len(sentence.text) > MAX_CHUNK_CHARS:
                        pieces.append({"text": current_text, "citations": current_markers})
                        current_text, current_markers = "", []
                    offset = len(current_text) + (1 if current_text else 0)
                    current_text = f"{current_text} {sentence.text}".strip()
                    current_markers.extend(citation_json(sentence.citations, offset))
                if current_text:
                    pieces.append({"text": current_text, "citations": current_markers})

            for piece in pieces:
                item = {"section": section.title, "kind": section.kind, "page": paragraph.page, **piece}
                if pending is not None:
                    offset = len(pending["text"]) + 1
                    item["citations"] = [
                        *pending["citations"],
                        *[{**c, "start": c["start"] + offset} for c in item["citations"]],
                    ]
                    item["text"] = f"{pending['text']} {item['text']}"
                    item["page"] = pending["page"]
                    pending = None
                if len(item["text"]) < MIN_CHUNK_CHARS:
                    pending = item
                else:
                    chunks.append(item)
        if pending is not None:
            chunks.append(pending)
    return chunks


async def write_chunks(session: AsyncSession, paper: Paper, parsed: ParsedDoc | None) -> int:
    await session.execute(delete(Chunk).where(Chunk.paper_id == paper.id))
    chunks = chunk_document(parsed, paper.abstract)
    vectors = await embed([c["text"] for c in chunks])
    for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
        session.add(
            Chunk(
                paper_id=paper.id, ordinal=ordinal, section=chunk["section"], section_kind=chunk["kind"],
                text=chunk["text"], page=chunk["page"], citations=chunk["citations"], embedding=vector,
            )
        )
    return len(chunks)


async def ensure_fulltext(
    session: AsyncSession, http: Polite, paper: Paper, mode: str = "fast", notify: Notify = _noop
) -> Paper:
    """Step 2: walk the acquisition chain once. A paper fetched once is never fetched again."""
    if paper.fulltext_status == "FULL":
        return paper
    if paper.fulltext_attempt == "thorough" or (paper.fulltext_attempt == "fast" and mode == "fast"):
        return paper

    best: tuple[ParsedDoc, str, str | None, str | None] | None = None  # parsed, source, tei, pdf path
    failures_before = http.transient_failures
    if paper.doi:
        urls, arxiv_id = await _pdf_urls(http, paper, mode)

        async def try_pdf(pdf: bytes | None, source: str) -> bool:
            """Record a parsed PDF. True stops the chain because the parse is fully structured."""
            nonlocal best
            if pdf is None:
                return False
            try:
                parsed, tei = await parse_pdf(pdf)
            except ValueError as exc:
                log.info("Could not parse PDF from %s: %s", source, exc)
                return False
            _, path = _store_pdf(pdf)
            if best is None or parsed.quality == "structured":
                best = (parsed, source, tei, path)
            return parsed.quality == "structured"

        await notify("Looking for open-access full text")
        done = False
        for url in urls[:1]:
            done = await try_pdf(await http.get_pdf(url), "unpaywall_pdf")
        if not done:
            xml = await api.europepmc_fulltext_xml(http, paper.doi)
            if xml:
                try:
                    best = (parse_jats(xml), "europepmc_xml", xml, best[3] if best else None)
                    done = True
                except ValueError as exc:
                    log.info("Could not parse Europe PMC XML for %s: %s", paper.doi, exc)
        if not done and arxiv_id:
            done = await try_pdf(await api.arxiv_pdf(http, arxiv_id), "arxiv_pdf")
        if not done:
            for url in urls[1:]:
                if await try_pdf(await http.get_pdf(url), "publisher_oa"):
                    break

    # A source that timed out has not said no, so leave the paper eligible for another attempt.
    timed_out = best is None and http.transient_failures > failures_before
    paper.fulltext_attempt = None if timed_out else mode
    if best is not None:
        parsed, source, tei, path = best
        _commit_parsed(paper, parsed, source, tei)
        paper.pdf_path = path
        await _write_references(session, paper, parsed)
        await write_chunks(session, paper, parsed)
    elif paper.abstract:
        paper.fulltext_status = "ABSTRACT_ONLY"
        paper.fulltext = paper.abstract
        paper.fulltext_note = (
            "A full-text source timed out, so only the abstract was checked. Trying again may find more."
            if timed_out
            else "No open-access full text was found. Only the abstract was checked."
        )
        await write_chunks(session, paper, None)
    else:
        paper.fulltext_status = "NONE"
        paper.fulltext_note = "Neither full text nor an abstract is openly available."
    await session.commit()
    return paper


async def ingest_upload(session: AsyncSession, http: Polite, pdf: bytes, filename: str) -> Paper:
    """Parse a user's PDF. An upload outranks every other source for that paper."""
    digest, path = _store_pdf(pdf)
    existing = await session.scalar(select(Paper).where(Paper.content_hash == digest))
    if existing is not None:
        return existing

    parsed, tei = await parse_pdf(pdf)
    paper: Paper | None = None
    if parsed.doi:
        try:
            paper = await resolve_paper(session, http, parsed.doi)
        except PaperNotFound:
            paper = None
    if paper is None:
        paper = Paper(id=uuid.uuid4(), doi=parsed.doi)
        session.add(paper)

    paper.title = paper.title or parsed.title or filename
    paper.authors = paper.authors or parsed.authors
    paper.year = paper.year or parsed.year
    paper.abstract = paper.abstract or parsed.abstract
    paper.content_hash = digest
    paper.pdf_path = path
    paper.fulltext_attempt = "thorough"
    _commit_parsed(paper, parsed, "upload", tei)
    await session.flush()
    await _write_references(session, paper, parsed)
    await write_chunks(session, paper, parsed)
    await session.commit()
    return paper


async def resolve_reference(session: AsyncSession, http: Polite, ref: Reference) -> str | None:
    """Find a DOI for a reference entry, searching by title when it lists none."""
    if ref.resolved_doi or ref.resolution_tried or not ref.title:
        return ref.resolved_doi
    ref.resolution_tried = True
    meta = await api.openalex_by_title(http, ref.title, ref.year) or await api.crossref_by_title(http, ref.title, ref.year)
    if meta and meta.doi:
        ref.resolved_doi = meta.doi
    await session.commit()
    return ref.resolved_doi


def sentences_of(parsed: dict | None) -> list[dict]:
    """Flatten a parsed document into API-friendly sentences."""
    if not parsed:
        return []
    doc = ParsedDoc.model_validate(parsed)
    output: list[dict] = []
    for section in doc.sections:
        for paragraph in section.paragraphs:
            for sentence in paragraph.sentences:
                output.append(
                    {
                        "index": len(output), "text": sentence.text, "section": section.title,
                        "kind": section.kind, "page": paragraph.page,
                        "citations": [c.model_dump() for c in sentence.citations],
                    }
                )
    return output
