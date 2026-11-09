"""Markdown and PDF reports (spec section 6.4)."""

import io
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

FONT_DIR = Path(__file__).parent / "fonts"
METHOD = (
    "DriftTrace locates the claim in the citing paper, retrieves the most relevant passages of each cited "
    "paper, and compares the two with a language model. Each quotation shown here was matched against the "
    "stored text of the paper. A verdict whose quotations could not be matched was discarded."
)


def label_text(label: str) -> str:
    return label.replace("_", " ").capitalize()


def _location(item: dict | None) -> str:
    if not item:
        return ""
    parts = []
    if item.get("section"):
        parts.append(item["section"])
    if item.get("page"):
        parts.append(f"p. {item['page']}")
    return ", ".join(parts)


def ordered_hops(data: dict) -> list[dict]:
    """Hops on the worst path first, in chain order, then everything else."""
    by_id = {h["id"]: h for h in data["hops"]}
    worst = [by_id[i] for i in data["worst_path"] if i in by_id]
    return worst + [h for h in data["hops"] if h["id"] not in data["worst_path"]]


def _node_names(data: dict) -> dict[str, str]:
    return {n["id"]: (n["paper"]["short_cite"] if n["paper"] else (n["label"] or "Unknown source")) for n in data["nodes"]}


def limitations(data: dict) -> list[str]:
    names = _node_names(data)
    items = []
    for hop in data["hops"]:
        if hop["source_status"] == "ABSTRACT_ONLY":
            items.append(f"{names[hop['to']]}: only the abstract was available.")
        elif hop["primary_label"] == "UNVERIFIABLE":
            items.append(f"{names[hop['to']]}: {hop['confidence_reason']}")
    for node in data["nodes"]:
        if node["stop_reason"] == "max_depth":
            items.append(f"{names[node['id']]}: the trace stopped at the depth limit before reaching an origin.")
    return list(dict.fromkeys(items)) or ["Every source in this trace was read in full."]


def score_text(data: dict) -> str:
    return f"{data['fidelity_score']} / 100" if data["fidelity_score"] is not None else "Not scored"


def report_markdown(data: dict) -> str:
    names = _node_names(data)
    lines = ["# DriftTrace report", ""]
    if data["is_sample"]:
        lines += ["> **Sample data.** This trace uses invented papers to demonstrate the report format.", ""]
    lines += [f"> {data['claim']}", "", f"**Fidelity score:** {score_text(data)}", "", data.get("summary") or "", "", "## Drift path", ""]
    for number, hop in enumerate(ordered_hops(data), start=1):
        secondary = ", ".join(label_text(s) for s in hop["secondary_labels"])
        lines.append(f"{number}. **{names[hop['from']]} → {names[hop['to']]}: {label_text(hop['primary_label'])}** ({hop['confidence']} confidence)")
        if secondary:
            lines.append(f"   - Also: {secondary}")
        lines.append(f"   - Claimed: \"{hop['claimed_quote']}\"")
        for quote in hop["source_quotes"]:
            where = _location(quote)
            lines.append(f"   - Source says: \"{quote['text']}\"" + (f" ({where})" if where else ""))
        lines.append(f"   - {hop['explanation']}")
    lines += ["", "## Methodology", "", METHOD, "", data["score_formula"], "", "## Limitations", ""]
    lines += [f"- {item}" for item in limitations(data)]
    return "\n".join(lines) + "\n"


def _register_fonts() -> None:
    if "DriftSerif" in pdfmetrics.getRegisteredFontNames():
        return
    pdfmetrics.registerFont(TTFont("DriftSerif", str(FONT_DIR / "DejaVuSerif.ttf")))
    pdfmetrics.registerFont(TTFont("DriftSerif-Bold", str(FONT_DIR / "DejaVuSerif-Bold.ttf")))
    pdfmetrics.registerFontFamily("DriftSerif", normal="DriftSerif", bold="DriftSerif-Bold", italic="DriftSerif", boldItalic="DriftSerif-Bold")


def report_pdf(data: dict) -> bytes:
    _register_fonts()
    ink, muted = HexColor("#1b1d22"), HexColor("#5b616e")
    body = ParagraphStyle("body", fontName="DriftSerif", fontSize=9.5, leading=14, textColor=ink, alignment=TA_LEFT, spaceAfter=4)
    h1 = ParagraphStyle("h1", parent=body, fontName="DriftSerif-Bold", fontSize=18, leading=23, spaceAfter=8)
    h2 = ParagraphStyle("h2", parent=body, fontName="DriftSerif-Bold", fontSize=12, leading=16, spaceBefore=12, spaceAfter=6)
    quote = ParagraphStyle("quote", parent=body, leftIndent=12, textColor=ink)
    small = ParagraphStyle("small", parent=body, fontSize=8.5, leading=12.5, textColor=muted)

    def p(text: str, style: ParagraphStyle = body) -> Paragraph:
        return Paragraph(escape(text), style)

    names = _node_names(data)
    story = [p("DriftTrace report", h1)]
    if data["is_sample"]:
        story.append(p("Sample data. This trace uses invented papers to demonstrate the report format.", small))
    story += [p(f"“{data['claim']}”", quote), Spacer(1, 6), p(f"Fidelity score: {score_text(data)}", h2), p(data.get("summary") or ""), p("Drift path", h2)]
    for number, hop in enumerate(ordered_hops(data), start=1):
        title = f"{number}. {names[hop['from']]} → {names[hop['to']]}: {label_text(hop['primary_label'])} ({hop['confidence']} confidence)"
        story.append(Paragraph(f"<b>{escape(title)}</b>", body))
        if hop["secondary_labels"]:
            story.append(p("Also: " + ", ".join(label_text(s) for s in hop["secondary_labels"]), small))
        story.append(p(f"Claimed: “{hop['claimed_quote']}”", quote))
        for item in hop["source_quotes"]:
            where = _location(item)
            story.append(p(f"Source says: “{item['text']}”" + (f" ({where})" if where else ""), quote))
        story += [p(hop["explanation"] or ""), Spacer(1, 6)]
    story += [p("Methodology", h2), p(METHOD), p(data["score_formula"]), p("Limitations", h2)]
    story += [p(f"• {item}") for item in limitations(data)]

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm, topMargin=20 * mm, bottomMargin=20 * mm, title="DriftTrace report").build(story)
    return buffer.getvalue()
