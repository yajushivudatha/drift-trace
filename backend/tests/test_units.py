"""Pure-function tests: quotes, sentences, parsers, scoring and the principle-enforcing rules."""

import io
from pathlib import Path

import httpx
import pytest
import respx
from reportlab.pdfgen import canvas

from drifttrace.export.report import report_markdown, report_pdf
from drifttrace.external import scholarly as api
from drifttrace.external.http import Polite
from drifttrace.ingest.parsers import parse_jats, parse_plain_pdf, parse_tei
from drifttrace.ingest.service import chunk_document
from drifttrace.ingest.text import numbers_in_marker, section_kind, split_sentences
from drifttrace.llm.client import api_schema
from drifttrace.llm.prompts import load_prompt
from drifttrace.pipeline.judge import apply_principles
from drifttrace.pipeline.locate import find_sentence, unique_ref_keys
from drifttrace.pipeline.quotes import failed_quotes, quote_in_text, squash
from drifttrace.pipeline.retrieve import fuse
from drifttrace.schemas import Judgment
from drifttrace.taxonomy import ScoredHop, fidelity_score, hop_penalty, worst_path
from tests.fake_llm import judgment

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = "Com-\npound X re­duced serum IL-6 by 12% in diet-induced obese mice at 500 mg/kg. ﬁndings were “modest”."


# --- Quote verification ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "quote",
    [
        "Compound X reduced serum IL-6 by 12%",
        "compound x reduced  serum il6 by 12%",
        "diet induced obese mice",
        'findings were "modest"',
    ],
)
def test_quote_tolerates_pdf_artifacts(quote):
    assert quote_in_text(quote, SOURCE)


@pytest.mark.parametrize(
    "quote",
    [
        "Compound X reduced serum IL-6 by 21%",  # a changed number must never pass
        "Compound X reduced serum IL-6 by 12%% in humans",
        "Compound X increased serum IL-6 by 12%",
        "by 12",  # too short to be evidence
        "",
    ],
)
def test_quote_rejects_altered_text(quote):
    assert not quote_in_text(quote, SOURCE)


def test_near_match_requires_identical_numbers():
    text = "The treatment reduced the measured inflammatory marker by 12.5 percent over eight weeks in all animals."
    assert quote_in_text("The treatment reduced the measured inflammatory markers by 12.5 percent over eight weeks", text)
    assert not quote_in_text("The treatment reduced the measured inflammatory marker by 12.6 percent over eight weeks", text)


def test_range_dash_keeps_digits_apart():
    assert squash("10–20") == "10-20"
    assert squash("IL-6") == "il6"
    assert failed_quotes(["a value of 10-20 units"], "a value of 1020 units") == ["a value of 10-20 units"]


# --- Sentences and markers ---------------------------------------------------------------------


def test_sentence_splitter_respects_abbreviations():
    text = "Smith et al. found this in Fig. 2. A second sentence e.g. here follows. Third one."
    parts = [text[a:b] for a, b in split_sentences(text)]
    assert parts == ["Smith et al. found this in Fig. 2.", "A second sentence e.g. here follows.", "Third one."]


@pytest.mark.parametrize(
    ("marker", "expected"),
    [("[3–7]", [3, 4, 5, 6, 7]), ("[1, 3-4]", [1, 3, 4]), ("(12)", [12]), ("Smith 2019", None), ("[2019-1]", [2019, 1])],
)
def test_numbers_in_marker(marker, expected):
    assert numbers_in_marker(marker) == expected


def test_section_kind():
    assert section_kind("2. Materials and Methods") == "methods"
    assert section_kind("Results and Discussion") == "results"
    assert section_kind("Something else") == "other"


# --- Parsers -----------------------------------------------------------------------------------


def test_tei_parser():
    doc = parse_tei((FIXTURES / "sample.tei.xml").read_bytes())
    assert doc.title == "Compound X in obese mice"
    assert doc.doi == "10.1234/abc.5"
    assert doc.year == 2020
    assert doc.abstract == "We tested compound X."
    assert [s.kind for s in doc.sections] == ["introduction", "results"]
    intro = doc.sections[0].paragraphs[0]
    assert intro.page == 2
    assert intro.sentences[0].text == "Compound X reduced inflammation in mice [1]."
    assert intro.sentences[0].citations[0].ref_keys == ["b0"]
    assert intro.sentences[1].citations[0].ref_keys == ["b1", "b2", "b3"], "a dash between markers is a bundle"
    assert intro.sentences[2].citations[0].ref_keys == ["b0", "b2", "b3"], "a range in one marker is a bundle"
    refs = {r.key: r for r in doc.references}
    assert refs["b0"].doi == "10.1000/first"
    assert refs["b0"].raw_text == "Lee. First paper. J Test 2018."
    assert refs["b3"].title == "A Book Title"
    # A marker's offsets must select exactly its own text.
    sentence = intro.sentences[0]
    marker = sentence.citations[0]
    assert sentence.text[marker.start : marker.end] == "[1]"


def test_jats_parser():
    doc = parse_jats((FIXTURES / "sample.jats.xml").read_bytes())
    assert doc.doi == "10.1234/jats.1"
    assert doc.authors == ["Sofia Lindqvist"]
    assert "Must not appear" not in doc.full_text()
    paragraph = doc.sections[0].paragraphs[0]
    assert paragraph.sentences[0].citations[0].ref_keys == ["r1"]
    assert paragraph.sentences[1].citations[0].ref_keys == ["r1", "r2", "r3"]
    assert paragraph.sentences[2].citations[0].ref_keys == ["r2", "r3"]
    assert doc.sections[1].title == "Results › Subgroup"
    assert doc.sections[1].kind == "results", "a subsection inherits the kind of its parent"
    assert doc.references[0].doi == "10.1000/one"
    assert doc.references[2].title == "Book three"
    plain = doc.references[3]
    assert plain.doi == "10.1016/j.devcel.2006.09.009", "a DOI inside an unstructured reference still counts"
    assert plain.title == "The diverse functions of microRNAs in animal development and disease."
    assert plain.year == 2006


def test_plain_pdf_fallback_is_flagged_degraded():
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    lines = [
        "Introduction", "",
        "Compound X reduced inflammation in obese mice at high doses [1].",
        "Later work extended the finding in another strain of animals [2].", "",
        "References", "",
        "1. Lee A. First paper on the topic. J Test 2018.",
        "2. Kim B. Second paper on the topic. J Test 2019. doi:10.1000/two",
    ]
    for index, line in enumerate(lines):
        pdf.drawString(50, 780 - index * 16, line)
    pdf.save()
    doc = parse_plain_pdf(buffer.getvalue())
    assert doc.quality == "degraded"
    assert doc.sections[0].kind == "introduction"
    sentence = doc.sections[0].paragraphs[0].sentences[0]
    assert sentence.citations[0].ref_keys == ["n1"]
    assert {r.key: r.doi for r in doc.references}["n2"] == "10.1000/two"


def test_plain_pdf_rejects_textless_file():
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.showPage()
    pdf.save()
    with pytest.raises(ValueError, match="no extractable text"):
        parse_plain_pdf(buffer.getvalue())


def test_malformed_tei_raises_so_caller_falls_back():
    with pytest.raises(ValueError):
        parse_tei("<TEI xmlns='http://www.tei-c.org/ns/1.0'><text><body/></text></TEI>")


def test_find_sentence_ignores_missing_markers():
    doc = parse_tei((FIXTURES / "sample.tei.xml").read_bytes())
    found = find_sentence(doc, "Compound X reduced inflammation in mice.")
    assert found is not None
    assert found.citations[0].ref_keys == ["b0"]
    assert found.after.startswith("Other work agrees")
    assert find_sentence(doc, "A wholly unrelated assertion about stellar nucleosynthesis.") is None


def test_unique_ref_keys_makes_one_hop_per_paper():
    doc = parse_tei((FIXTURES / "sample.tei.xml").read_bytes())
    sentence = doc.sections[0].paragraphs[0].sentences[2]
    assert [k for k, _ in unique_ref_keys(sentence.citations)] == ["b0", "b2", "b3"]


def test_chunking_keeps_marker_offsets_and_skips_references():
    doc = parse_tei((FIXTURES / "sample.tei.xml").read_bytes())
    doc.sections.append(doc.sections[0].model_copy(update={"title": "References", "kind": "references"}))
    chunks = chunk_document(doc, "An abstract.")
    assert chunks[0]["section"] == "Abstract"
    assert all(c["kind"] != "references" for c in chunks)
    for chunk in chunks:
        for citation in chunk["citations"]:
            assert chunk["text"][citation["start"] :].startswith(citation["marker"])


def test_fusion_applies_section_prior():
    ranked = fuse([0, 1], [0, 1], ["methods", "results"])
    assert ranked[0][0] == 1


# --- Metadata clients --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "doi"),
    [
        ("https://doi.org/10.1016/J.CELL.2020.01.001", "10.1016/j.cell.2020.01.001"),
        ("doi:10.1371/journal.pone.0000308.", "10.1371/journal.pone.0000308"),
        ("https://elifesciences.org/articles/10.7554/eLife.123/full", "10.7554/elife.123"),
        ("https://arxiv.org/abs/1706.03762v5", "10.48550/arxiv.1706.03762"),
        ("not a doi", None),
    ],
)
def test_normalize_doi(value, doi):
    assert api.normalize_doi(value) == doi


def test_retraction_from_notices():
    notices = [{"DOI": "10.1/notice", "update-to": [{"DOI": "10.1/PAPER", "type": "retraction"}]}]
    assert "10.1/notice" in api.retraction_from_notices("10.1/paper", notices)
    assert api.retraction_from_notices("10.1/other", notices) is None
    concern = [{"DOI": "10.1/n", "update-to": [{"DOI": "10.1/p", "type": "expression_of_concern"}]}]
    assert api.retraction_from_notices("10.1/p", concern).startswith("Expression of concern")
    correction = [{"DOI": "10.1/n", "update-to": [{"DOI": "10.1/p", "type": "correction"}]}]
    assert api.retraction_from_notices("10.1/p", correction) is None


@respx.mock
async def test_openalex_rebuilds_abstract_and_sends_contact():
    route = respx.get("https://api.openalex.org/works/doi:10.1/x").mock(
        return_value=httpx.Response(200, json={
            "id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/x", "display_name": "T", "publication_year": 2020,
            "abstract_inverted_index": {"world": [1], "hello": [0]}, "is_retracted": True, "language": "en", "type": "article",
            "authorships": [{"author": {"display_name": "A B"}}], "open_access": {"is_oa": True},
            "best_oa_location": {"pdf_url": "https://x/a.pdf"}, "locations": [{"pdf_url": "https://x/a.pdf", "landing_page_url": "https://arxiv.org/abs/2101.00001"}],
        })
    )
    async with Polite() as http:
        meta = await api.openalex_by_doi(http, "10.1/x")
    assert meta.abstract == "hello world"
    assert meta.openalex_id == "W1"
    assert meta.is_retracted and meta.arxiv_id == "2101.00001"
    assert meta.pdf_urls == ["https://x/a.pdf"]
    request = route.calls[0].request
    assert "mailto=tests%40example.org" in str(request.url) and "mailto:tests@example.org" in request.headers["user-agent"]


@respx.mock
async def test_title_search_rejects_a_different_paper():
    respx.get("https://api.openalex.org/works").mock(
        return_value=httpx.Response(200, json={"results": [{"doi": "https://doi.org/10.1/x", "display_name": "A quite different paper altogether", "publication_year": 2020}]})
    )
    async with Polite() as http:
        assert await api.openalex_by_title(http, "Compound X in obese mice", 2020) is None


@respx.mock
async def test_retries_after_rate_limit():
    route = respx.get("https://api.crossref.org/works/10.1/x").mock(
        side_effect=[httpx.Response(429, headers={"retry-after": "0"}), httpx.Response(200, json={"message": {"DOI": "10.1/X", "title": ["T"]}})]
    )
    async with Polite() as http:
        meta = await api.crossref_by_doi(http, "10.1/x")
    assert route.call_count == 2 and meta.doi == "10.1/x"


@respx.mock
async def test_pdf_download_rejects_html():
    respx.get("https://x/a.pdf").mock(return_value=httpx.Response(200, text="<html>Sign in</html>"))
    async with Polite() as http:
        assert await http.get_pdf("https://x/a.pdf") is None


# --- Principles --------------------------------------------------------------------------------


def _j(label: str, confidence: str = "high") -> Judgment:
    return Judgment.model_validate(judgment(label, "a claim here", ["a quote here"], confidence))


def test_abstract_only_never_says_unsupported():
    result = apply_principles(_j("UNSUPPORTED_ATTRIBUTION"), "ABSTRACT_ONLY", False)
    assert result.primary_label == "UNVERIFIABLE"
    assert "full text unavailable" in result.explanation


def test_abstract_only_caps_confidence():
    assert apply_principles(_j("HEDGE_LOSS"), "ABSTRACT_ONLY", False).confidence == "medium"
    assert apply_principles(_j("HEDGE_LOSS"), "FULL", False).confidence == "high"


def test_degraded_parse_lowers_confidence():
    assert apply_principles(_j("HEDGE_LOSS"), "FULL", True).confidence == "medium"


def test_full_text_keeps_unsupported():
    assert apply_principles(_j("UNSUPPORTED_ATTRIBUTION"), "FULL", False).primary_label == "UNSUPPORTED_ATTRIBUTION"


# --- Scoring -----------------------------------------------------------------------------------


def hop(a, b, label, confidence="high", secondary=()):
    return ScoredHop(f"{a}{b}", a, b, label, tuple(secondary), confidence)


def test_score_arithmetic():
    assert fidelity_score([hop("r", "a", "FAITHFUL")], "r") == 100
    assert fidelity_score([hop("r", "a", "HEDGE_LOSS")], "r") == 92
    assert fidelity_score([hop("r", "a", "CAUSAL_INFLATION", "medium")], "r") == 86  # 18 x 0.75 = 13.5
    assert hop_penalty(hop("r", "a", "HEDGE_LOSS", "high", ["CAUSAL_INFLATION"])) == 8 + 4.5


def test_worst_path_dominates():
    hops = [hop("r", "a", "FAITHFUL"), hop("r", "b", "RETRACTED_SOURCE"), hop("b", "c", "HEDGE_LOSS")]
    assert fidelity_score(hops, "r") == 57
    assert [h.to_node for h in worst_path(hops, "r")] == ["b", "c"]


def test_score_floors_at_zero_and_handles_cycles():
    hops = [hop("r", "a", "RETRACTED_SOURCE"), hop("a", "b", "RETRACTED_SOURCE"), hop("b", "c", "RETRACTED_SOURCE"), hop("c", "a", "UNVERIFIABLE")]
    assert fidelity_score(hops, "r") == 0


def test_all_unverifiable_is_unscored_not_zero():
    assert fidelity_score([hop("r", "a", "UNVERIFIABLE")], "r") is None
    assert fidelity_score([], "r") is None


# --- Prompts and schema ------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["claim_extract", "source_frame", "rerank", "judge"])
def test_prompts_load(name):
    prompt = load_prompt(name)
    assert prompt.version >= 1 and prompt.system and prompt.user


def test_prompt_requires_all_variables():
    with pytest.raises(KeyError):
        load_prompt("rerank").render(claim="x")


def test_schema_fits_structured_outputs():
    schema = api_schema(Judgment)

    def check(node):
        if isinstance(node, dict):
            assert "default" not in node
            if node.get("type") == "object" and "properties" in node:
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for item in node:
                check(item)

    check(schema)
    assert "primary_label" in schema["properties"], "a schema field named title or default must survive"


# --- Reports -----------------------------------------------------------------------------------

REPORT = {
    "claim": "IL-6 fell by 12 µg in β-cells", "fidelity_score": None, "summary": "S", "is_sample": False, "score_formula": "F", "worst_path": ["h1"],
    "nodes": [
        {"id": "a", "paper": {"short_cite": "Lee 2020"}, "label": None, "stop_reason": None},
        {"id": "b", "paper": None, "label": "Some book", "stop_reason": "max_depth"},
    ],
    "hops": [{
        "id": "h1", "from": "a", "to": "b", "primary_label": "HEDGE_LOSS", "secondary_labels": ["CAUSAL_INFLATION"], "confidence": "low",
        "claimed_quote": "x & y < z", "source_quotes": [{"text": "q", "section": "Results", "page": 3}], "explanation": "e", "source_status": "ABSTRACT_ONLY", "confidence_reason": "r",
    }],
}


def test_markdown_report():
    text = report_markdown(REPORT)
    assert "Not scored" in text and "Results, p. 3" in text and "only the abstract" in text and "depth limit" in text


def test_pdf_report_handles_symbols_and_markup():
    assert report_pdf(REPORT).startswith(b"%PDF")
