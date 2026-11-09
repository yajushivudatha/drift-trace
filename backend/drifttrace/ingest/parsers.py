"""Turn GROBID TEI, JATS XML and raw PDF text into one ParsedDoc shape."""

import io
import re
from collections.abc import Iterable

import pypdfium2
from lxml import etree

from drifttrace.ingest.text import TextBuffer, build_paragraph, expand_bundles, numbers_in_marker, section_kind
from drifttrace.schemas import CitationMarker, ParsedDoc, RefEntry, Section

TEI = "{http://www.tei-c.org/ns/1.0}"
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
_PARSER = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False, no_network=True)


def _text(element: etree._Element | None) -> str:
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip() if element is not None else ""


def _year(value: str | None) -> int | None:
    match = re.search(r"(1[89]|20)\d{2}", value or "")
    return int(match.group(0)) if match else None


def _clean_doi(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"10\.\d{4,9}/\S+", value)
    return match.group(0).rstrip(".,;").lower() if match else None


# --- TEI (GROBID) --------------------------------------------------------------------------------


def _walk_tei(element: etree._Element, buffer: TextBuffer, markers: list[CitationMarker]) -> None:
    buffer.add(element.text)
    for child in element:
        if not isinstance(child.tag, str):
            buffer.add(child.tail)
            continue
        if child.tag == f"{TEI}ref" and child.get("type") == "bibr":
            start = buffer.length
            buffer.add(_text(child))
            target = (child.get("target") or "").lstrip("#")
            label = buffer.text()[start:]
            if label.strip():
                markers.append(
                    CitationMarker(marker=label, ref_keys=[target] if target else [], start=start, end=buffer.length)
                )
        else:
            _walk_tei(child, buffer, markers)
        buffer.add(child.tail)


def _tei_page(paragraph: etree._Element) -> int | None:
    for node in paragraph.iter():
        coords = node.get("coords") if isinstance(node.tag, str) else None
        if coords:
            return int(float(coords.split(",")[0]))
    return None


def _tei_references(root: etree._Element) -> list[RefEntry]:
    entries: list[RefEntry] = []
    for index, bibl in enumerate(root.iter(f"{TEI}biblStruct")):
        key = bibl.get(XML_ID)
        if not key:
            continue
        analytic = bibl.find(f"{TEI}analytic")
        monogr = bibl.find(f"{TEI}monogr")
        title = None
        for parent in (analytic, monogr):
            if parent is not None and title is None:
                node = parent.find(f"{TEI}title")
                title = _text(node) or None
        date = next((d.get("when") for d in bibl.iter(f"{TEI}date") if d.get("when")), None)
        doi = next((_clean_doi(_text(i)) for i in bibl.iter(f"{TEI}idno") if i.get("type") == "DOI"), None)
        raw = next((_text(n) for n in bibl.iter(f"{TEI}note") if n.get("type") == "raw_reference"), None)
        surnames = [_text(s) for s in bibl.iter(f"{TEI}surname")]
        entries.append(
            RefEntry(key=key, index=index, raw_text=raw or None, doi=doi, title=title, authors=surnames, year=_year(date))
        )
    return entries


def parse_tei(xml: str | bytes) -> ParsedDoc:
    data = xml.encode() if isinstance(xml, str) else xml
    root = etree.fromstring(data, parser=_PARSER)
    if root is None:
        raise ValueError("GROBID returned unreadable XML")
    header = root.find(f"{TEI}teiHeader")
    title, doi, abstract, year = None, None, None, None
    authors: list[str] = []
    if header is not None:
        title = _text(next(iter(header.iter(f"{TEI}title")), None)) or None
        source = next(iter(header.iter(f"{TEI}sourceDesc")), None)
        if source is not None:
            doi = next((_clean_doi(_text(i)) for i in source.iter(f"{TEI}idno") if i.get("type") == "DOI"), None)
            authors = [
                " ".join(filter(None, (_text(p.find(f"{TEI}forename")), _text(p.find(f"{TEI}surname")))))
                for p in source.iter(f"{TEI}persName")
            ]
            year = _year(next((d.get("when") for d in source.iter(f"{TEI}date") if d.get("when")), None))
        abstract_node = next(iter(header.iter(f"{TEI}abstract")), None)
        if abstract_node is not None:
            abstract = " ".join(_text(p) for p in abstract_node.iter(f"{TEI}p")) or _text(abstract_node) or None

    references = _tei_references(root)
    ordered = [r.key for r in references]
    sections: list[Section] = []
    body = next(iter(root.iter(f"{TEI}body")), None)
    for div in body.findall(f"{TEI}div") if body is not None else []:
        head = _text(div.find(f"{TEI}head"))
        if head or not sections:
            sections.append(Section(title=head, kind=section_kind(head)))
        section = sections[-1]
        for p in div.findall(f"{TEI}p"):
            buffer, markers = TextBuffer(), []
            _walk_tei(p, buffer, markers)
            text = buffer.text().rstrip()
            if text:
                section.paragraphs.append(build_paragraph(text, expand_bundles(text, markers, ordered), _tei_page(p)))
    sections = [s for s in sections if s.paragraphs]
    if not sections:
        raise ValueError("GROBID output had no body text")
    return ParsedDoc(
        title=title, doi=doi, abstract=abstract, authors=[a for a in authors if a], year=year,
        sections=sections, references=references, source="tei",
    )


# --- JATS (Europe PMC) ---------------------------------------------------------------------------

_JATS_SKIP = {"fig", "table-wrap", "fig-group", "supplementary-material", "disp-formula"}


def _walk_jats(element: etree._Element, buffer: TextBuffer, markers: list[CitationMarker]) -> None:
    buffer.add(element.text)
    for child in element:
        if not isinstance(child.tag, str) or child.tag in _JATS_SKIP:
            buffer.add(child.tail)
            continue
        if child.tag == "xref" and child.get("ref-type") == "bibr":
            start = buffer.length
            buffer.add(_text(child))
            label = buffer.text()[start:]
            keys = (child.get("rid") or "").split()
            if label.strip() and keys:
                markers.append(CitationMarker(marker=label, ref_keys=keys, start=start, end=buffer.length))
        else:
            _walk_jats(child, buffer, markers)
        buffer.add(child.tail)


def _title_from_raw(raw: str | None) -> str | None:
    """In "Authors. Title. Journal. Year" strings the title is the second sentence."""
    parts = [part.strip() for part in re.split(r"(?<=[.?!])\s+", raw or "")]
    return parts[1] if len(parts) > 1 and len(parts[1]) > 15 else None


def _jats_references(root: etree._Element) -> list[RefEntry]:
    entries: list[RefEntry] = []
    for index, ref in enumerate(root.iter("ref")):
        key = ref.get("id")
        if not key:
            continue
        citation = next(
            (c for c in ref if isinstance(c.tag, str) and c.tag in ("element-citation", "mixed-citation", "citation", "nlm-citation")),
            None,
        )
        if citation is None:
            continue
        doi = next((_clean_doi(_text(p)) for p in citation.iter("pub-id") if p.get("pub-id-type") == "doi"), None)
        title_node = next(iter(citation.iter("article-title")), None)
        if title_node is None:
            title_node = next(iter(citation.iter("source")), None)
        year_node = next(iter(citation.iter("year")), None)
        raw = _text(citation) or None
        # Some publishers deposit references as one unstructured string.
        title = _text(title_node) or _title_from_raw(raw)
        entries.append(
            RefEntry(
                key=key, index=index, raw_text=raw, doi=doi or _clean_doi(raw), title=title,
                authors=[_text(s) for s in citation.iter("surname")], year=_year(_text(year_node)) or _year(raw),
            )
        )
    return entries


def _collect_sec(sec: etree._Element, kind: str | None, prefix: str, sections: list[Section], ordered: list[str]) -> None:
    title = _text(sec.find("title"))
    full_title = f"{prefix} › {title}" if prefix and title else title or prefix
    section = Section(title=full_title, kind=kind or section_kind(title))
    for child in sec:
        if child.tag == "p":
            buffer, markers = TextBuffer(), []
            _walk_jats(child, buffer, markers)
            text = buffer.text().rstrip()
            if text:
                section.paragraphs.append(build_paragraph(text, expand_bundles(text, markers, ordered)))
    if section.paragraphs:
        sections.append(section)
    for child in sec:
        if child.tag == "sec":
            _collect_sec(child, section.kind if section.kind != "other" else None, full_title, sections, ordered)


def parse_jats(xml: str | bytes) -> ParsedDoc:
    data = xml.encode() if isinstance(xml, str) else xml
    root = etree.fromstring(data, parser=_PARSER)
    if root is None:
        raise ValueError("Unreadable JATS XML")
    meta = next(iter(root.iter("article-meta")), None)
    title, doi, abstract, year = None, None, None, None
    authors: list[str] = []
    if meta is not None:
        title = _text(next(iter(meta.iter("article-title")), None)) or None
        doi = next((_clean_doi(_text(a)) for a in meta.iter("article-id") if a.get("pub-id-type") == "doi"), None)
        abstract = _text(next(iter(meta.iter("abstract")), None)) or None
        year = _year(_text(next(iter(meta.iter("year")), None)))
        for contrib in meta.iter("contrib"):
            given, surname = contrib.find("name/given-names"), contrib.find("name/surname")
            if surname is not None:
                authors.append(f"{_text(given)} {_text(surname)}".strip())

    references = _jats_references(root)
    ordered = [r.key for r in references]
    sections: list[Section] = []
    body = next(iter(root.iter("body")), None)
    if body is None:
        raise ValueError("JATS document has no body")

    loose = Section(title="", kind="other")
    for child in body:
        if child.tag == "p":
            buffer, markers = TextBuffer(), []
            _walk_jats(child, buffer, markers)
            text = buffer.text().rstrip()
            if text:
                loose.paragraphs.append(build_paragraph(text, expand_bundles(text, markers, ordered)))
        elif child.tag == "sec":
            _collect_sec(child, None, "", sections, ordered)
    if loose.paragraphs:
        sections.insert(0, loose)
    if not sections:
        raise ValueError("JATS document has no body text")
    return ParsedDoc(
        title=title, doi=doi, abstract=abstract, authors=authors, year=year,
        sections=sections, references=references, source="jats",
    )


# --- Plain text fallback -------------------------------------------------------------------------

_NUM_MARKER = re.compile(r"\[\s*\d+(?:\s*[,;–—-]\s*\d+)*\s*\]")
_AUTHOR_YEAR = re.compile(r"\(([^()]*?\b(?:19|20)\d{2}[a-z]?(?:\s*;[^()]*?\b(?:19|20)\d{2}[a-z]?)*)\)")
_REF_NUM_LINE = re.compile(r"^\s*(?:\[(\d{1,3})\]|(\d{1,3})\.)\s+(?=\S)")
_HEADING = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?(abstract|introduction|background|materials? and methods|methods?|results?(?: and discussion)?|discussion|conclusions?|references|bibliography|acknowledge?ments?)\s*:?\s*$",
    re.I,
)


def _extract_pages(pdf: bytes) -> list[str]:
    document = pypdfium2.PdfDocument(io.BytesIO(pdf))
    try:
        pages = []
        for index in range(len(document)):
            page = document[index]
            textpage = page.get_textpage()
            pages.append(textpage.get_text_range())
            textpage.close()
            page.close()
        return pages
    finally:
        document.close()


def _plain_references(lines: Iterable[str]) -> list[RefEntry]:
    blocks: list[tuple[int | None, str]] = []
    for line in lines:
        if not line.strip():
            continue
        match = _REF_NUM_LINE.match(line)
        if match:
            blocks.append((int(match.group(1) or match.group(2)), line[match.end():].strip()))
        elif blocks:
            blocks[-1] = (blocks[-1][0], f"{blocks[-1][1]} {line.strip()}")
        else:
            blocks.append((None, line.strip()))

    entries = []
    for index, (number, raw) in enumerate(blocks):
        raw = re.sub(r"-\s+(?=[a-z])", "", raw)
        doi = _clean_doi(raw)
        sentences = [s.strip() for s in re.split(r"(?<=[.?!])\s+", raw)]
        title = max(sentences[1:3] or sentences[:1], key=len) if sentences else None
        surnames = re.findall(r"\b([A-Z][a-zA-Z'-]+),?\s+[A-Z]\.?", raw[:200])
        key = f"n{number}" if number is not None else f"p{index}"
        entries.append(RefEntry(key=key, index=index, raw_text=raw, doi=doi, title=title, authors=surnames, year=_year(raw)))
    return entries


def _plain_markers(text: str, references: list[RefEntry]) -> list[CitationMarker]:
    by_number = {int(r.key[1:]): r.key for r in references if r.key.startswith("n")}
    markers: list[CitationMarker] = []
    for match in _NUM_MARKER.finditer(text):
        numbers = numbers_in_marker(match.group(0)) or []
        keys = [by_number[n] for n in numbers if n in by_number]
        if keys:
            markers.append(CitationMarker(marker=match.group(0), ref_keys=keys, start=match.start(), end=match.end()))
    if markers:
        return markers
    for match in _AUTHOR_YEAR.finditer(text):
        keys: list[str] = []
        for piece in match.group(1).split(";"):
            year = _year(piece)
            name = re.match(r"\s*([A-Z][\w'-]+)", piece)
            if not year or not name:
                continue
            hit = next(
                (r.key for r in references if r.year == year and name.group(1) in (r.authors or [])[:3]),
                None,
            )
            if hit:
                keys.append(hit)
        if keys:
            markers.append(CitationMarker(marker=match.group(0), ref_keys=keys, start=match.start(), end=match.end()))
    return markers


def parse_plain_pdf(pdf: bytes) -> ParsedDoc:
    """Last resort for PDFs GROBID cannot read. The result is flagged as degraded."""
    pages = _extract_pages(pdf)
    if not "".join(pages).strip():
        raise ValueError("The PDF contains no extractable text. It may be a scan.")

    rows: list[tuple[int, str]] = [
        (number, line.rstrip()) for number, page in enumerate(pages, start=1) for line in page.splitlines()
    ]
    ref_start = None
    for index, (_, line) in enumerate(rows):
        if re.fullmatch(r"\s*(?:\d+\.?\s+)?(references|bibliography|literature cited)\s*:?\s*", line, re.I):
            ref_start = index
    references = _plain_references(line for _, line in rows[ref_start + 1:]) if ref_start is not None else []
    body_rows = rows[:ref_start] if ref_start is not None else rows

    sections = [Section(title="", kind="other")]
    current: list[str] = []
    page_of = 1

    def flush() -> None:
        nonlocal current
        if not current:
            return
        joined = ""
        for line in current:
            line = line.strip()
            if joined.endswith("-") and line[:1].islower():
                joined = joined[:-1] + line
            else:
                joined = f"{joined} {line}".strip()
        current = []
        if len(joined) >= 40:
            sections[-1].paragraphs.append(build_paragraph(joined, _plain_markers(joined, references), page_of))

    for number, line in body_rows:
        heading = _HEADING.match(line)
        if heading:
            flush()
            title = heading.group(1).strip().capitalize()
            sections.append(Section(title=title, kind=section_kind(title)))
            continue
        if not line.strip():
            flush()
            continue
        if not current:
            page_of = number
        current.append(line)
    flush()

    sections = [s for s in sections if s.paragraphs]
    abstract = None
    for section in sections:
        if section.kind == "abstract":
            abstract = " ".join(p.text for p in section.paragraphs)
    return ParsedDoc(sections=sections, references=references, source="plain", quality="degraded", abstract=abstract)
