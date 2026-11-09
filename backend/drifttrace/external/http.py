"""HTTP client for scholarly APIs: per-host pacing, retries, and an identifying User-Agent."""

import asyncio
import email.utils
import logging
import time
from urllib.parse import urlparse

import httpx

from drifttrace import __version__
from drifttrace.config import get_settings

log = logging.getLogger(__name__)

# Minimum seconds between requests to one host, set below each API's published limit.
HOST_INTERVAL = {
    "api.openalex.org": 0.15,
    "api.crossref.org": 0.25,
    "api.semanticscholar.org": 1.1,
    "api.unpaywall.org": 0.15,
    "www.ebi.ac.uk": 0.15,
    "export.arxiv.org": 3.1,
    "arxiv.org": 3.1,
}
DEFAULT_INTERVAL = 0.5
MAX_PDF_BYTES = 60 * 1024 * 1024


class Polite:
    def __init__(self, timeout: float = 30.0, retries: int = 3) -> None:
        settings = get_settings()
        contact = f"; mailto:{settings.contact_email}" if settings.contact_email else ""
        self.client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": f"DriftTrace/{__version__} (citation drift tracer{contact})"},
        )
        self.retries = retries
        # Counts requests given up on after timeouts or server errors, as opposed to a clean "no".
        self.transient_failures = 0
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    async def __aenter__(self) -> "Polite":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.client.aclose()

    async def _pace(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            interval = HOST_INTERVAL.get(host, DEFAULT_INTERVAL)
            wait = self._last.get(host, 0.0) + interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last[host] = time.monotonic()

    @staticmethod
    def _retry_after(response: httpx.Response, attempt: int) -> float:
        header = response.headers.get("retry-after")
        if header:
            if header.isdigit():
                return min(float(header), 60.0)
            parsed = email.utils.parsedate_to_datetime(header)
            return min(max(parsed.timestamp() - time.time(), 0.0), 60.0)
        return min(2.0**attempt, 30.0)

    async def get(self, url: str, **kwargs) -> httpx.Response | None:
        """GET with pacing and retries. Returns None when the resource is unavailable."""
        host = urlparse(url).hostname or ""
        for attempt in range(self.retries):
            await self._pace(host)
            try:
                response = await self.client.get(url, **kwargs)
            except httpx.TransportError as exc:
                log.info("GET %s failed: %s", url, exc)
                await asyncio.sleep(min(2.0**attempt, 10.0))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                await asyncio.sleep(self._retry_after(response, attempt))
                continue
            if response.status_code >= 400:
                return None
            return response
        self.transient_failures += 1
        return None

    async def get_json(self, url: str, **kwargs) -> dict | None:
        response = await self.get(url, **kwargs)
        if response is None:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    async def get_pdf(self, url: str) -> bytes | None:
        response = await self.get(url, timeout=60.0, headers={"Accept": "application/pdf"})
        if response is None:
            return None
        content = response.content
        if len(content) > MAX_PDF_BYTES or not content.startswith(b"%PDF"):
            return None
        return content
