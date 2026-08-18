"""Polite HTTP layer.

The crawler is deliberately conservative. Three mechanisms keep it that way:

1. **robots.txt** is fetched once per host, cached, and consulted before every URL.
2. **A token bucket** caps requests per second per host, so a burst of concurrent
   tasks still lands on the origin at a steady, human-scale rate.
3. **Backoff with jitter** on 429/5xx, and ``Retry-After`` is honoured when present.

If a site starts returning 403 or a challenge page, that is the site telling you it
does not want automated traffic. Slow down, or get written permission - do not try to
out-run the block. ``ChallengeDetected`` is raised so the job surfaces it clearly
instead of silently writing garbage rows.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


class ScraperError(Exception):
    """Base class for recoverable crawl problems."""


class BlockedByRobots(ScraperError):
    pass


class ChallengeDetected(ScraperError):
    """The origin served an anti-bot interstitial rather than content."""


class TransientHTTPError(ScraperError):
    pass


CHALLENGE_MARKERS = (
    "cf-browser-verification",
    "checking your browser",
    "captcha-delivery",
    "just a moment...",
    "attention required! | cloudflare",
    "ddos-guard",
)


class TokenBucket:
    """Simple async rate limiter: at most ``rate`` requests per second."""

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        self.rate = max(rate, 0.05)
        self.capacity = capacity if capacity is not None else max(1.0, rate)
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep((1 - self._tokens) / self.rate)


@dataclass(slots=True)
class FetchResult:
    url: str
    status: int
    html: str
    elapsed_ms: int


class PoliteClient:
    """One instance per scrape job, per site."""

    def __init__(
        self,
        base_url: str,
        requests_per_second: float | None = None,
        concurrency: int | None = None,
        respect_robots: bool | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        rps = requests_per_second or settings.DEFAULT_REQUESTS_PER_SECOND
        burst = concurrency or settings.DEFAULT_CONCURRENCY
        # Capacity must be at least `burst`, or `concurrency` never does anything:
        # with the naive default (capacity == 1), every request queues behind the
        # last one no matter how many are allowed to run in parallel. Sizing
        # capacity to the burst lets up to `burst` requests fire immediately, then
        # throttles back to the sustained rate - concurrency now actually means
        # something instead of being silently capped at one in-flight request.
        self.bucket = TokenBucket(rps, capacity=max(rps, burst))
        self.semaphore = asyncio.Semaphore(burst)
        self.respect_robots = (
            settings.RESPECT_ROBOTS_TXT if respect_robots is None else respect_robots
        )
        self._robots: RobotFileParser | None = None
        self._robots_loaded = False
        self._client = httpx.AsyncClient(
            http2=True,
            follow_redirects=True,
            timeout=httpx.Timeout(settings.REQUEST_TIMEOUT_SECONDS),
            limits=httpx.Limits(max_connections=concurrency or settings.DEFAULT_CONCURRENCY),
            headers={
                "User-Agent": settings.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,bn;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
            },
        )

    async def __aenter__(self) -> PoliteClient:
        await self._load_robots()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    # -- robots ------------------------------------------------------------
    async def _load_robots(self) -> None:
        if self._robots_loaded or not self.respect_robots:
            self._robots_loaded = True
            return
        self._robots_loaded = True
        parser = RobotFileParser()
        robots_url = urljoin(self.base_url + "/", "robots.txt")
        try:
            resp = await self._client.get(robots_url)
            if resp.status_code == 200:
                parser.parse(resp.text.splitlines())
                self._robots = parser
                log.info("robots.loaded", url=robots_url)
            else:
                log.info("robots.absent", url=robots_url, status=resp.status_code)
        except httpx.HTTPError as exc:
            log.warning("robots.fetch_failed", url=robots_url, error=str(exc))

        # Honour a site declared crawl-delay if it is stricter than ours.
        if self._robots:
            delay = self._robots.crawl_delay(settings.USER_AGENT)
            if delay:
                allowed_rps = 1.0 / float(delay)
                if allowed_rps < self.bucket.rate:
                    log.info("robots.crawl_delay_applied", rps=allowed_rps)
                    # Keep the existing burst capacity - only the sustained rate
                    # should drop to respect the site's declared crawl-delay.
                    self.bucket = TokenBucket(allowed_rps, capacity=self.bucket.capacity)

    def is_allowed(self, url: str) -> bool:
        if not self.respect_robots or self._robots is None:
            return True
        return self._robots.can_fetch(settings.USER_AGENT, url)

    # -- fetching ----------------------------------------------------------
    def absolute(self, path_or_url: str) -> str:
        if path_or_url.startswith("http"):
            return path_or_url
        return urljoin(self.base_url + "/", path_or_url.lstrip("/"))

    async def get(self, path_or_url: str, params: dict | None = None) -> FetchResult:
        url = self.absolute(path_or_url)
        if not self.is_allowed(url):
            raise BlockedByRobots(f"robots.txt disallows {urlparse(url).path}")

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(settings.MAX_RETRIES),
            wait=wait_exponential_jitter(initial=2, max=30),
            retry=retry_if_exception_type((TransientHTTPError, httpx.TransportError)),
            reraise=True,
        ):
            with attempt:
                await self.bucket.acquire()
                async with self.semaphore:
                    started = time.monotonic()
                    resp = await self._client.get(url, params=params)
                    elapsed = int((time.monotonic() - started) * 1000)

                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", 10))
                    log.warning("http.rate_limited", url=url, retry_after=retry_after)
                    await asyncio.sleep(min(retry_after, 60))
                    raise TransientHTTPError("429 Too Many Requests")
                if resp.status_code in (403, 503):
                    body = resp.text[:4000].lower()
                    if any(m in body for m in CHALLENGE_MARKERS):
                        raise ChallengeDetected(
                            f"{resp.status_code} anti-bot challenge at {url}. "
                            "Stop and review the site's terms before continuing."
                        )
                    raise TransientHTTPError(f"{resp.status_code} from {url}")
                if resp.status_code >= 500:
                    raise TransientHTTPError(f"{resp.status_code} from {url}")
                if resp.status_code == 404:
                    return FetchResult(url=url, status=404, html="", elapsed_ms=elapsed)
                resp.raise_for_status()

                lowered = resp.text[:4000].lower()
                if any(m in lowered for m in CHALLENGE_MARKERS):
                    raise ChallengeDetected(f"Anti-bot challenge served at {url}.")

                return FetchResult(
                    url=str(resp.url), status=resp.status_code, html=resp.text, elapsed_ms=elapsed
                )

        raise TransientHTTPError(f"exhausted retries for {url}")  # pragma: no cover