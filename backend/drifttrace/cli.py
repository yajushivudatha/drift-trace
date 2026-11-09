"""Command-line tools: `drifttrace ingest`, `trace-hop`, and `drifttrace seed-sample`."""

import asyncio
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from sqlalchemy import select

from drifttrace.db.models import Reference
from drifttrace.db.session import session_scope
from drifttrace.external.http import Polite
from drifttrace.external.scholarly import normalize_doi
from drifttrace.ingest.service import ensure_fulltext, resolve_paper, resolve_reference, sentences_of
from drifttrace.pipeline.judge import extract_claim, run_hop
from drifttrace.pipeline.locate import ClaimContext, find_sentence, unique_ref_keys
from drifttrace.schemas import ParsedDoc

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


async def _ingest(doi: str, mode: str, limit: int) -> None:
    async with session_scope() as session, Polite() as http:
        paper = await resolve_paper(session, http, doi)
        await ensure_fulltext(session, http, paper, mode)
        console.print(Panel(f"[bold]{paper.title}[/bold]\n{', '.join(paper.authors[:4])} ({paper.year}), {paper.venue}"))
        console.print(f"Full text: [bold]{paper.fulltext_status}[/bold] via {paper.fulltext_source or 'nothing'}   Retracted: {paper.is_retracted}")
        references = {
            r.ref_key: r for r in await session.scalars(select(Reference).where(Reference.citing_paper_id == paper.id))
        }
        shown = 0
        sentences = sentences_of(paper.parsed)
        console.print(f"{len(sentences)} sentences, {len(references)} references\n")
        for sentence in sentences:
            if not sentence["citations"]:
                continue
            console.print(f"[dim]{sentence['section']}[/dim] {sentence['text']}")
            for citation in sentence["citations"]:
                for key in citation["ref_keys"]:
                    reference = references.get(key)
                    if reference is None:
                        console.print(f"    {citation['marker']} -> [red]missing from the reference list[/red]")
                        continue
                    doi_found = await resolve_reference(session, http, reference)
                    console.print(f"    {citation['marker']} -> {doi_found or '[yellow]no DOI found[/yellow]'}  [dim]{(reference.title or '')[:70]}[/dim]")
            shown += 1
            if shown >= limit:
                break


@app.command()
def ingest(
    doi: str,
    mode: Annotated[str, typer.Option(help="fast or thorough")] = "fast",
    limit: Annotated[int, typer.Option(help="How many citing sentences to print")] = 15,
) -> None:
    """Ingest a DOI and print its sentences with resolved citations."""
    canonical = normalize_doi(doi)
    if not canonical:
        raise typer.BadParameter("Not a DOI or paper URL")
    asyncio.run(_ingest(canonical, mode, limit))


async def _trace_hop(doi: str, sentence: str, mode: str) -> None:
    async with session_scope() as session, Polite() as http:
        paper = await resolve_paper(session, http, doi)
        await ensure_fulltext(session, http, paper, mode)
        context = find_sentence(ParsedDoc.model_validate(paper.parsed), sentence) if paper.parsed else None
        if context is None:
            console.print("[red]That sentence was not found in the paper's full text.[/red]")
            raise typer.Exit(1)
        references = {
            r.ref_key: r for r in await session.scalars(select(Reference).where(Reference.citing_paper_id == paper.id))
        }
        pairs = unique_ref_keys(context.citations)
        if not pairs:
            console.print("[red]That sentence has no citations.[/red]")
            raise typer.Exit(1)
        claim_frame = await extract_claim(ClaimContext(sentence=context.sentence, before=context.before, after=context.after))
        for key, marker in pairs:
            reference = references.get(key)
            cited_doi = await resolve_reference(session, http, reference) if reference else None
            if not cited_doi:
                console.print(f"{marker}: reference could not be resolved to a DOI. [yellow]UNVERIFIABLE[/yellow]")
                continue
            cited = await resolve_paper(session, http, cited_doi)
            await ensure_fulltext(session, http, cited, mode)
            verdict = await run_hop(session, context, claim_frame, cited)
            secondary = f"\nAlso: {', '.join(verdict.secondary_labels)}" if verdict.secondary_labels else ""
            quotes = "\n".join(f"  \"{q['text']}\" ({q['section']})" for q in verdict.source_quotes)
            console.print(
                Panel(
                    f"[bold]{verdict.primary_label}[/bold]  {verdict.confidence} confidence  source: {verdict.source_status}{secondary}\n"
                    f"{verdict.confidence_reason}\n\n[bold]Claimed[/bold]\n  \"{verdict.claimed_quote}\"\n\n"
                    f"[bold]Source says[/bold]\n{quotes or '  (no quotation)'}\n\n{verdict.explanation}",
                    title=f"{marker} {cited.title}",
                )
            )


def trace_hop_command(
    doi: str,
    sentence: str,
    mode: Annotated[str, typer.Option(help="fast or thorough")] = "fast",
) -> None:
    """Judge one citing sentence against each paper it cites."""
    canonical = normalize_doi(doi)
    if not canonical:
        raise typer.BadParameter("Not a DOI or paper URL")
    asyncio.run(_trace_hop(canonical, sentence, mode))


app.command("trace-hop")(trace_hop_command)


def trace_hop_entry() -> None:
    typer.run(trace_hop_command)


@app.command("seed-sample")
def seed_sample() -> None:
    """Add a sample trace built from invented papers, clearly marked, for exploring the interface."""
    from drifttrace.sample import seed

    trace_id = asyncio.run(seed())
    console.print(f"Sample trace ready: {trace_id}")


if __name__ == "__main__":
    app()
