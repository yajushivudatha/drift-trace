"""Live ingestion of five real open-access papers. Run with `pytest --live`; needs TEST_DATABASE_URL."""

import pytest

from drifttrace.db.session import session_scope
from drifttrace.external.http import Polite
from drifttrace.ingest.service import ensure_fulltext, resolve_paper, sentences_of

pytestmark = [pytest.mark.live, pytest.mark.db]

OPEN_ACCESS = [
    "10.1371/journal.pmed.0020124",  # PLOS Medicine
    "10.7554/elife.05005",  # eLife
    "10.1186/s12916-015-0469-2",  # BMC Medicine
    "10.1038/s41467-019-08746-5",  # Nature Communications
    "10.1371/journal.pone.0005738",  # PLOS ONE
]


@pytest.mark.parametrize("doi", OPEN_ACCESS)
async def test_ingest_open_access_paper(clean_db, doi):
    async with session_scope() as session, Polite() as http:
        paper = await resolve_paper(session, http, doi)
        assert paper.title
        assert paper.authors
        await ensure_fulltext(session, http, paper, "fast")
        assert paper.fulltext_status == "FULL", paper.fulltext_note
        sentences = sentences_of(paper.parsed)
        assert len(sentences) > 20
        print(f"\n{doi}: {paper.fulltext_source}, {len(sentences)} sentences, {sum(1 for s in sentences if s['citations'])} with citations")
