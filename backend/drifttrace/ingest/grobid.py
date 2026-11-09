"""Client for the GROBID service."""

import asyncio
import logging

import httpx

from drifttrace.config import get_settings

log = logging.getLogger(__name__)


async def grobid_alive() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{get_settings().grobid_url}/api/isalive")
        return response.status_code == 200
    except httpx.HTTPError:
        return False


async def process_pdf(pdf: bytes) -> str | None:
    """Return TEI XML for a PDF, or None when GROBID is down or cannot process it."""
    form = {
        "segmentSentences": "1",
        "includeRawCitations": "1",
        "teiCoordinates": ["s", "ref"],
    }
    async with httpx.AsyncClient(timeout=240.0) as client:
        for attempt in range(4):
            try:
                response = await client.post(
                    f"{get_settings().grobid_url}/api/processFulltextDocument",
                    files={"input": ("paper.pdf", pdf, "application/pdf")},
                    data=form,
                )
            except httpx.HTTPError as exc:
                log.warning("GROBID request failed: %s", exc)
                return None
            if response.status_code == 503:  # GROBID's "all workers busy" response
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            if response.status_code != 200:
                log.warning("GROBID returned %s", response.status_code)
                return None
            return response.text
    return None
