"""Clients for OpenAlex, Crossref, Semantic Scholar, Unpaywall, Europe PMC and arXiv."""

import re
from dataclasses import dataclass, field
from urllib.parse import quote

from lxml import html as lxml_html
from rapidfuzz import fuzz

from drifttrace.config import get_settings
from drifttrace.external.http import Polite

TITLE_MATCH_THRESHOLD = 92


@dataclass
class PaperMeta:
    doi: str | None = None
    openalex_id: str | None = None
    s2_id: str | None = None
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    abstract: str | None = None
    work_type: str | None = None
    language: str | None = None
    is_oa: bool = False
    oa_url: str | None = None
    is_retracted: bool = False
    retraction_note: str | None = None
    pdf_urls: list[str] = field(default_factory=list)
    arxiv_id: str | None = None
    related_versions: list[dict] = field(default_factory=list)


DOI_IN_TEXT = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.I)
ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", re.I)


def normalize_doi(value: str) -> str | None:
    """Pull a canonical lowercase DOI out of a DOI, a doi.org link, or a publisher or arXiv URL."""
    value = value.strip()
    arxiv = ARXIV_URL.search(value)
    if arxiv:
        return f"10.48550/arxiv.{arxiv.group(1)}"
    match = DOI_IN_TEXT.search(value)
    if not match:
        return None
    doi = match.group(0).rstrip(".,;)]")
    doi = re.sub(r"(/(full|abstract|pdf|epdf|html|meta|summary)|\.pdf|\.full)$", "", doi, flags=re.I)
    return doi.lower()


def _invert_abstract(index: dict | None) -> str | None:
    if not index:
        return None
    positions = [(pos, word) for word, spots in index.items() for pos in spots]
    return " ".join(word for _, word in sorted(positions)) or None


def strip_markup(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = lxml_html.fromstring(f"<div>{text}</div>").text_content()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return re.sub(r"^Abstract\s*:?\s*", "", cleaned, flags=re.I) or None


def _openalex_params() -> dict[str, str]:
    settings = get_settings()
    params: dict[str, str] = {}
    if settings.contact_email:
        params["mailto"] = settings.contact_email
    if settings.openalex_api_key:
        params["api_key"] = settings.openalex_api_key
    return params


def _meta_from_openalex(work: dict) -> PaperMeta:
    source = ((work.get("primary_location") or {}).get("source") or {})
    pdf_urls = []
    arxiv_id = None
    for location in work.get("locations") or []:
        if location.get("pdf_url"):
            pdf_urls.append(location["pdf_url"])
        arxiv = ARXIV_URL.search(location.get("landing_page_url") or "")
        if arxiv:
            arxiv_id = arxiv.group(1)
    best = (work.get("best_oa_location") or {}).get("pdf_url")
    if best:
        pdf_urls.insert(0, best)
    oa = work.get("open_access") or {}
    doi = work.get("doi")
    return PaperMeta(
        doi=normalize_doi(doi) if doi else None,
        openalex_id=(work.get("id") or "").rsplit("/", 1)[-1] or None,
        title=work.get("display_name") or work.get("title"),
        authors=[
            a["author"]["display_name"]
            for a in work.get("authorships") or []
            if (a.get("author") or {}).get("display_name")
        ],
        year=work.get("publication_year"),
        venue=source.get("display_name"),
        abstract=_invert_abstract(work.get("abstract_inverted_index")),
        work_type=work.get("type"),
        language=work.get("language"),
        is_oa=bool(oa.get("is_oa")),
        oa_url=oa.get("oa_url"),
        is_retracted=bool(work.get("is_retracted")),
        pdf_urls=list(dict.fromkeys(pdf_urls)),
        arxiv_id=arxiv_id,
    )


async def openalex_by_doi(http: Polite, doi: str) -> PaperMeta | None:
    data = await http.get_json(
        f"https://api.openalex.org/works/doi:{quote(doi, safe='/')}", params=_openalex_params()
    )
    return _meta_from_openalex(data) if data else None


async def openalex_by_title(http: Polite, title: str, year: int | None) -> PaperMeta | None:
    query = re.sub(r"[^\w\s-]", " ", title)
    params = {**_openalex_params(), "search": query[:300], "per-page": "5"}
    data = await http.get_json("https://api.openalex.org/works", params=params)
    return _best_title_match(
        [_meta_from_openalex(w) for w in (data or {}).get("results", [])], title, year
    )


def _best_title_match(candidates: list[PaperMeta], title: str, year: int | None) -> PaperMeta | None:
    """Accept a search hit only when the title clearly matches. A wrong paper is worse than none."""
    best: PaperMeta | None = None
    best_score = 0.0
    for candidate in candidates:
        if not candidate.title or not candidate.doi:
            continue
        if year and candidate.year and abs(year - candidate.year) > 1:
            continue
        score = fuzz.ratio(title.lower(), candidate.title.lower())
        if score > best_score:
            best, best_score = candidate, score
    return best if best_score >= TITLE_MATCH_THRESHOLD else None


def _crossref_params() -> dict[str, str]:
    email = get_settings().contact_email
    return {"mailto": email} if email else {}


def _meta_from_crossref(item: dict) -> PaperMeta:
    issued = (item.get("issued") or {}).get("date-parts") or [[None]]
    authors = [
        " ".join(p for p in (a.get("given"), a.get("family")) if p) or a.get("name", "")
        for a in item.get("author") or []
    ]
    relation = item.get("relation") or {}
    related = [
        {"relation": kind, "doi": entry["id"].lower()}
        for kind in ("is-preprint-of", "has-preprint")
        for entry in relation.get(kind) or []
        if entry.get("id-type") == "doi"
    ]
    return PaperMeta(
        doi=(item.get("DOI") or "").lower() or None,
        title=(item.get("title") or [None])[0],
        authors=[a for a in authors if a],
        year=issued[0][0],
        venue=(item.get("container-title") or [None])[0],
        abstract=strip_markup(item.get("abstract")),
        work_type=item.get("type"),
        language=item.get("language"),
        related_versions=related,
    )


async def crossref_by_doi(http: Polite, doi: str) -> PaperMeta | None:
    data = await http.get_json(
        f"https://api.crossref.org/works/{quote(doi, safe='/')}", params=_crossref_params()
    )
    return _meta_from_crossref(data["message"]) if data and data.get("message") else None


async def crossref_by_title(http: Polite, title: str, year: int | None) -> PaperMeta | None:
    data = await http.get_json(
        "https://api.crossref.org/works",
        params={**_crossref_params(), "query.bibliographic": title[:300], "rows": "5"},
    )
    items = ((data or {}).get("message") or {}).get("items", [])
    return _best_title_match([_meta_from_crossref(i) for i in items], title, year)


RETRACTION_TYPES = {"retraction", "withdrawal", "removal", "expression_of_concern", "expression-of-concern"}


def retraction_from_notices(doi: str, items: list[dict]) -> str | None:
    for item in items:
        for update in item.get("update-to") or []:
            kind = (update.get("type") or "").lower().replace(" ", "_")
            if (update.get("DOI") or "").lower() == doi and kind in RETRACTION_TYPES:
                label = "Expression of concern" if "concern" in kind else "Retraction"
                notice = (item.get("DOI") or "").lower()
                return f"{label} notice: https://doi.org/{notice}" if notice else label
    return None


async def crossref_retraction(http: Polite, doi: str) -> str | None:
    """Return a description of the retraction or concern notice for a DOI, if Crossref has one."""
    data = await http.get_json(
        "https://api.crossref.org/works",
        params={**_crossref_params(), "filter": f"updates:{doi}", "rows": "5"},
    )
    return retraction_from_notices(doi, ((data or {}).get("message") or {}).get("items", []))


async def semantic_scholar_by_doi(http: Polite, doi: str) -> PaperMeta | None:
    settings = get_settings()
    headers = {"x-api-key": settings.semantic_scholar_api_key} if settings.semantic_scholar_api_key else {}
    data = await http.get_json(
        f"https://api.semanticscholar.org/graph/v1/paper/DOI:{quote(doi, safe='/')}",
        params={"fields": "paperId,title,authors,year,venue,abstract,isOpenAccess,openAccessPdf,externalIds"},
        headers=headers,
    )
    if not data:
        return None
    pdf = (data.get("openAccessPdf") or {}).get("url")
    return PaperMeta(
        doi=doi,
        s2_id=data.get("paperId"),
        title=data.get("title"),
        authors=[a["name"] for a in data.get("authors") or [] if a.get("name")],
        year=data.get("year"),
        venue=data.get("venue") or None,
        abstract=data.get("abstract"),
        is_oa=bool(data.get("isOpenAccess")),
        pdf_urls=[pdf] if pdf else [],
        arxiv_id=(data.get("externalIds") or {}).get("ArXiv"),
    )


async def unpaywall_pdfs(http: Polite, doi: str) -> list[str]:
    """Open-access PDF links known to Unpaywall, best first. Unpaywall requires a contact email."""
    email = get_settings().contact_email
    if not email:
        return []
    data = await http.get_json(
        f"https://api.unpaywall.org/v2/{quote(doi, safe='/')}", params={"email": email}
    )
    if not data:
        return []
    best = (data.get("best_oa_location") or {}).get("url_for_pdf")
    others = [loc.get("url_for_pdf") for loc in data.get("oa_locations") or []]
    return list(dict.fromkeys(u for u in [best, *others] if u))


EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"


async def europepmc_fulltext_xml(http: Polite, doi: str) -> str | None:
    data = await http.get_json(
        f"{EPMC}/search", params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "lite"}
    )
    results = ((data or {}).get("resultList") or {}).get("result") or []
    pmcid = next((r["pmcid"] for r in results if r.get("pmcid")), None)
    if not pmcid:
        return None
    response = await http.get(f"{EPMC}/{pmcid}/fullTextXML", timeout=120.0)
    return response.text if response is not None and "<article" in response.text else None


async def arxiv_pdf(http: Polite, arxiv_id: str) -> bytes | None:
    return await http.get_pdf(f"https://arxiv.org/pdf/{arxiv_id}")
