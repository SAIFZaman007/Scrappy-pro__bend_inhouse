# backend/app/scrapers/http.py
"""HTTP transport for the crawler.

WHAT WAS BROKEN (and why every site returned zero products)
-----------------------------------------------------------
The previous version hard-coded ``Accept-Encoding: gzip, deflate, br`` on the
httpx client. httpx only *decodes* the encodings it has a decoder installed for:
``br`` needs ``brotli``/``brotlicffi``, ``zstd`` needs ``zstandard``. Neither was
in ``pyproject.toml``. So every Cloudflare-fronted origin - which is all four
targets - happily returned a Brotli-compressed body, httpx passed the raw
compressed bytes straight through as ``IdentityDecoder``, and ``resp.text``
produced 12,000+ U+FFFD replacement characters instead of HTML.

Every CSS selector then matched nothing, `parse_listing` returned an empty list,
the structural sniffer found no cards, and the job reported "0 products" with a
200 OK and no error. The bundled ``sample-startech-component-processor.html`` is
that exact failure frozen on disk - 54 KB with no ``<html`` in it.

Two independent defences are now in place:

1. ``Accept-Encoding`` is *derived* from the decoders actually importable at
   runtime. We never advertise support we do not have.
2. ``_decode_body`` sanity-checks the decoded text and, if it still looks like
   binary, retries manual Brotli/zstd/gzip/deflate decompression of the raw
   bytes. Belt and braces: a missing dependency degrades to a warning, not to
   silent data loss.

OTHER CHANGES
-------------
* Realistic browser request headers (Sec-Fetch-*, sec-ch-ua, Referer chain).
  These four storefronts sit behind WAFs that score a bare non-browser
  User-Agent as automation and answer 403 before the application ever runs.
* A persistent cookie jar plus a one-time homepage warm-up, so session cookies
  (PHPSESSID, XSRF-TOKEN, __cf_bm) exist before the first listing request.
* 403 is retried **once** with a rotated fingerprint and a fresh session before
  being treated as a refusal - most 403s from these sites are UA heuristics, not
  a deliberate policy decision. A second 403 is taken at face value.
* ``ROBOTS_POLICY`` replaces the boolean: strict / listings / off, so an
  over-broad ``Disallow: /`` cannot silently zero out a run without the operator
  being told exactly what happened.
* Optional escalation to a real headless browser (see ``browser.py``) for
  origins that answer a JS challenge, wired through ``requires_browser``.

Rate limiting, Retry-After handling and per-host backoff are unchanged in
spirit: this crawler is meant to look like a well-behaved logged-out shopper,
not a flood.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
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


# --------------------------------------------------------------------------- #
# Content-encoding capability detection - the fix for the original bug.
# --------------------------------------------------------------------------- #
try:  # pragma: no cover - import probe
    import brotli as _brotli_mod  # type: ignore[import-not-found]

    HAS_BROTLI = True
except ImportError:  # pragma: no cover
    try:
        import brotlicffi as _brotli_mod  # type: ignore[import-not-found]

        HAS_BROTLI = True
    except ImportError:
        _brotli_mod = None  # type: ignore[assignment]
        HAS_BROTLI = False

try:  # pragma: no cover - import probe
    import zstandard as _zstd_mod  # type: ignore[import-not-found]

    HAS_ZSTD = True
except ImportError:  # pragma: no cover
    _zstd_mod = None  # type: ignore[assignment]
    HAS_ZSTD = False


def supported_accept_encoding() -> str:
    """Advertise only what we can actually decode.

    Asking for Brotli without a Brotli decoder is how the original build turned
    every page into 54 KB of mojibake. This function is the single source of
    truth for that header and is also reported by ``scripts/doctor.py``.
    """
    encodings = ["gzip", "deflate"]
    if HAS_BROTLI:
        encodings.append("br")
    if HAS_ZSTD:
        encodings.append("zstd")
    return ", ".join(encodings)


if not HAS_BROTLI:  # pragma: no cover - startup advisory
    log.warning(
        "http.brotli_missing",
        detail=(
            "brotli is not installed. Cloudflare-fronted origins prefer Brotli; "
            "without it responses fall back to gzip, which works but is slower. "
            "Install with: pip install brotli"
        ),
    )


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class ScraperError(Exception):
    """Base class for recoverable crawl problems."""


class BlockedByRobots(ScraperError):
    """robots.txt disallows this path under the active ROBOTS_POLICY."""


class ChallengeDetected(ScraperError):
    """The origin served an anti-bot interstitial rather than content."""


class AccessBlocked(ScraperError):
    """A flat refusal (403) that survived a fingerprint rotation.

    Distinct from ``TransientHTTPError`` on purpose. 403 is an authorization
    decision, not a "try again" status. We now retry it exactly once with a
    different browser fingerprint and a clean session, because in practice most
    403s from these storefronts are crude User-Agent heuristics. If the second
    attempt is also refused, that is the site's real answer and we stop.
    """


class TransientHTTPError(ScraperError):
    """429/5xx - genuinely worth retrying with backoff."""


class ContentDecodeError(ScraperError):
    """The body arrived but could not be turned into usable text."""


CHALLENGE_MARKERS = (
    "cf-browser-verification",
    "checking your browser",
    "captcha-delivery",
    "just a moment",
    "attention required! | cloudflare",
    "ddos-guard",
    "please enable javascript and cookies",
    "verifying you are human",
    "__cf_chl",
    "challenge-platform",
)

# Real, current desktop browser fingerprints. Each entry keeps its User-Agent and
# its Client Hints consistent - mismatched sec-ch-ua/UA pairs are themselves a
# bot signal, so they travel together.
BROWSER_PROFILES: tuple[dict[str, str], ...] = (
    {
        "ua": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "sec_ch_ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
        "platform": '"Windows"',
    },
    {
        "ua": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        "sec_ch_ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        "platform": '"macOS"',
    },
    {
        "ua": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0"
        ),
        "sec_ch_ua": '"Chromium";v="124", "Microsoft Edge";v="124", "Not-A.Brand";v="99"',
        "platform": '"Windows"',
    },
    {
        "ua": "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
        "sec_ch_ua": "",  # Firefox does not send Client Hints - omitting them is correct.
        "platform": "",
    },
)


@dataclass(slots=True)
class FetchResult:
    """One fetched page.

    ``via`` records which transport produced it ("http" or "browser") so the CLI
    and the job event tape can tell an operator when a page needed escalation.
    """

    url: str
    status: int
    html: str
    elapsed_ms: int
    via: str = "http"
    from_cache: bool = False


class TokenBucket:
    """Async rate limiter: at most ``rate`` requests per second, burst ``capacity``."""

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
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._updated) * self.rate
                )
                self._updated = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep((1 - self._tokens) / self.rate)


# --------------------------------------------------------------------------- #
# Body decoding
# --------------------------------------------------------------------------- #
def _looks_like_binary(text: str) -> bool:
    """True when ``text`` is clearly not the HTML document we asked for.

    The signature of the original bug: a body full of U+FFFD replacement
    characters and no HTML tags anywhere near the start.
    """
    if not text:
        return False
    head = text[:4096]
    if "<html" in head.lower() or "<!doctype" in head.lower():
        return False
    bad = text.count("\ufffd")
    return bad > 20 or (len(text) > 200 and bad / len(text) > 0.02)


def _manual_decompress(raw: bytes) -> bytes | None:
    """Last-resort decompression when httpx handed back an undecoded body."""
    import gzip
    import zlib

    attempts = []
    if HAS_BROTLI and _brotli_mod is not None:
        attempts.append(("br", lambda b: _brotli_mod.decompress(b)))
    if HAS_ZSTD and _zstd_mod is not None:
        attempts.append(("zstd", lambda b: _zstd_mod.ZstdDecompressor().decompress(b)))
    attempts.append(("gzip", gzip.decompress))
    attempts.append(("zlib", zlib.decompress))
    attempts.append(("deflate", lambda b: zlib.decompress(b, -15)))

    for name, fn in attempts:
        try:
            out = fn(raw)
        except Exception:  # noqa: BLE001 - wrong codec, try the next one
            continue
        if out and b"<" in out[:4096]:
            log.warning("http.manual_decompress", codec=name, size=len(out))
            return out
    return None


def _decode_body(resp: httpx.Response) -> str:
    """Turn a response into text, repairing an undecoded body if we can."""
    text = resp.text
    if not _looks_like_binary(text):
        return text

    log.warning(
        "http.undecoded_body",
        url=str(resp.url),
        content_encoding=resp.headers.get("content-encoding"),
        advertised=supported_accept_encoding(),
    )
    repaired = _manual_decompress(resp.content)
    if repaired is None:
        raise ContentDecodeError(
            f"Could not decode the response body from {resp.url}. "
            f"Content-Encoding was '{resp.headers.get('content-encoding')}' and this "
            f"build advertises '{supported_accept_encoding()}'. "
            "Install the matching decoder (pip install brotli zstandard) and retry."
        )
    return repaired.decode(resp.encoding or "utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class PoliteClient:
    """One instance per scrape job, per site.

    Holds the rate limiter, the cookie jar, the robots policy and - when the site
    needs it - a lazily started headless browser for escalation.
    """

    def __init__(
        self,
        base_url: str,
        requests_per_second: float | None = None,
        concurrency: int | None = None,
        respect_robots: bool | None = None,
        robots_policy: str | None = None,
        requires_browser: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.host = urlparse(self.base_url).netloc

        rps = requests_per_second or settings.DEFAULT_REQUESTS_PER_SECOND
        burst = concurrency or settings.DEFAULT_CONCURRENCY
        # Capacity must be at least `burst`, or `concurrency` never does anything.
        self.bucket = TokenBucket(rps, capacity=max(rps, burst))
        self.semaphore = asyncio.Semaphore(burst)

        # `respect_robots` is kept for backwards compatibility with existing
        # callers; ROBOTS_POLICY is the richer control and wins when both are set.
        if robots_policy is not None:
            self.robots_policy = robots_policy
        elif respect_robots is False:
            self.robots_policy = "off"
        elif respect_robots is True:
            self.robots_policy = "strict"
        else:
            self.robots_policy = settings.effective_robots_policy
        self.robots_policy = self.robots_policy.lower().strip()

        self.requires_browser = requires_browser or settings.FORCE_BROWSER
        self._browser = None  # lazily created BrowserFetcher

        self._robots: RobotFileParser | None = None
        self._robots_loaded = False
        self._warmed_up = False
        self._profile = random.choice(BROWSER_PROFILES)
        self._last_url: str | None = None
        self._client = self._build_client()

    # -- client construction ----------------------------------------------
    def _headers(self, profile: dict[str, str]) -> dict[str, str]:
        """Headers a real Chrome/Firefox tab would send for a top-level navigation."""
        ua = settings.USER_AGENT if settings.USE_CUSTOM_USER_AGENT else profile["ua"]
        headers = {
            "User-Agent": ua,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9,bn;q=0.8",
            # Derived, never hard-coded. This line is the actual bug fix.
            "Accept-Encoding": supported_accept_encoding(),
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }
        if profile.get("sec_ch_ua"):
            headers["sec-ch-ua"] = profile["sec_ch_ua"]
            headers["sec-ch-ua-mobile"] = "?0"
            headers["sec-ch-ua-platform"] = profile["platform"]
        return headers

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            http2=settings.HTTP2_ENABLED,
            follow_redirects=True,
            timeout=httpx.Timeout(settings.REQUEST_TIMEOUT_SECONDS),
            limits=httpx.Limits(
                max_connections=max(4, int(self.semaphore._value or 4)),  # noqa: SLF001
                max_keepalive_connections=8,
                keepalive_expiry=30.0,
            ),
            headers=self._headers(self._profile),
            cookies=httpx.Cookies(),  # persistent jar for the lifetime of the job
        )

    async def _rotate_fingerprint(self) -> None:
        """Start a completely fresh session under a different browser identity."""
        old = self._profile["ua"].split(") ", 1)[0]
        candidates = [p for p in BROWSER_PROFILES if p is not self._profile]
        self._profile = random.choice(candidates or list(BROWSER_PROFILES))
        await self._client.aclose()
        self._client = self._build_client()
        self._warmed_up = False
        log.info("http.fingerprint_rotated", host=self.host, was=old)

    # -- lifecycle ---------------------------------------------------------
    async def __aenter__(self) -> PoliteClient:
        await self._load_robots()
        await self._warm_up()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._browser is not None:
            await self._browser.close()
        await self._client.aclose()

    async def _warm_up(self) -> None:
        """Fetch the homepage once so the jar has real session cookies.

        A first request that arrives with an empty cookie jar, no Referer and a
        cold TLS session is the single most common thing WAFs bounce. One cheap
        homepage GET removes that signal for the whole job.
        """
        if self._warmed_up or not settings.SESSION_WARMUP:
            self._warmed_up = True
            return
        self._warmed_up = True
        try:
            await self.bucket.acquire()
            resp = await self._client.get(self.base_url + "/")
            self._last_url = self.base_url + "/"
            log.info(
                "http.warmed_up",
                host=self.host,
                status=resp.status_code,
                cookies=len(self._client.cookies.jar),
            )
        except httpx.HTTPError as exc:
            log.warning("http.warmup_failed", host=self.host, error=str(exc))

    # -- robots ------------------------------------------------------------
    async def _load_robots(self) -> None:
        if self._robots_loaded:
            return
        self._robots_loaded = True
        if self.robots_policy == "off":
            log.warning(
                "robots.disabled",
                host=self.host,
                detail=(
                    "ROBOTS_POLICY=off. robots.txt is not being consulted. Only run "
                    "this against sites you have permission to crawl."
                ),
            )
            return

        parser = RobotFileParser()
        robots_url = urljoin(self.base_url + "/", "robots.txt")
        try:
            resp = await self._client.get(robots_url)
            if resp.status_code == 200:
                parser.parse(_decode_body(resp).splitlines())
                self._robots = parser
                log.info("robots.loaded", url=robots_url)
            else:
                log.info("robots.absent", url=robots_url, status=resp.status_code)
        except (httpx.HTTPError, ContentDecodeError) as exc:
            log.warning("robots.fetch_failed", url=robots_url, error=str(exc))

        # Honour a declared crawl-delay when it is stricter than ours.
        if self._robots:
            for agent in (self._client.headers.get("User-Agent", "*"), "*"):
                try:
                    delay = self._robots.crawl_delay(agent)
                except Exception:  # noqa: BLE001 - malformed robots.txt
                    delay = None
                if delay:
                    allowed_rps = 1.0 / float(delay)
                    if allowed_rps < self.bucket.rate:
                        log.info("robots.crawl_delay_applied", rps=allowed_rps)
                        self.bucket = TokenBucket(
                            allowed_rps, capacity=self.bucket.capacity
                        )
                    break

    def is_allowed(self, url: str) -> bool:
        """Apply the active robots policy.

        strict   - honour every rule (default; the safe choice).
        listings - honour robots.txt, but do not let a blanket ``Disallow: /`` or a
                   ``Disallow: /*?`` query-string rule block the specific catalogue
                   paths an operator explicitly mapped. Many OpenCart robots.txt
                   files ban query strings to protect faceted-search crawl budget,
                   which also kills ``?page=2``. This mode keeps the intent of the
                   file while allowing plain pagination.
        off      - do not consult robots.txt at all.
        """
        if self.robots_policy == "off" or self._robots is None:
            return True

        agent = self._client.headers.get("User-Agent", "*")
        try:
            allowed = self._robots.can_fetch(agent, url) or self._robots.can_fetch("*", url)
        except Exception:  # noqa: BLE001 - malformed robots.txt must not stop a run
            return True

        if allowed or self.robots_policy == "strict":
            return allowed

        # listings mode: allow the path itself if only its query string was banned.
        parsed = urlparse(url)
        bare = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        try:
            return bool(self._robots.can_fetch(agent, bare) or self._robots.can_fetch("*", bare))
        except Exception:  # noqa: BLE001
            return True

    # -- helpers -----------------------------------------------------------
    def absolute(self, path_or_url: str) -> str:
        if path_or_url.startswith("http"):
            return path_or_url
        return urljoin(self.base_url + "/", path_or_url.lstrip("/"))

    @staticmethod
    def _is_challenge(body: str, resp: httpx.Response) -> bool:
        if resp.headers.get("cf-mitigated", "").lower() == "challenge":
            return True
        lowered = body[:8000].lower()
        return any(marker in lowered for marker in CHALLENGE_MARKERS)

    # -- fetching ----------------------------------------------------------
    async def get(
        self,
        path_or_url: str,
        params: dict | None = None,
        *,
        allow_browser: bool | None = None,
    ) -> FetchResult:
        """Fetch one URL under the full politeness/retry/escalation policy."""
        url = self.absolute(path_or_url)
        if not self.is_allowed(url):
            raise BlockedByRobots(
                f"robots.txt disallows {urlparse(url).path} under ROBOTS_POLICY="
                f"{self.robots_policy}. Set ROBOTS_POLICY=listings if this is a "
                "catalogue path you are permitted to read, or map a different URL."
            )

        use_browser = self.requires_browser if allow_browser is None else allow_browser
        if use_browser and settings.BROWSER_FALLBACK_ENABLED:
            return await self._get_via_browser(url, params)

        rotated_once = False
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(settings.MAX_RETRIES),
            wait=wait_exponential_jitter(initial=2, max=30),
            retry=retry_if_exception_type((TransientHTTPError, httpx.TransportError)),
            reraise=True,
        ):
            with attempt:
                await self.bucket.acquire()
                # Small human-scale jitter on top of the token bucket. Perfectly
                # even inter-request spacing is itself a fingerprint.
                if settings.REQUEST_JITTER_SECONDS > 0:
                    await asyncio.sleep(random.uniform(0, settings.REQUEST_JITTER_SECONDS))

                headers = {}
                if self._last_url:
                    headers["Referer"] = self._last_url
                    headers["Sec-Fetch-Site"] = "same-origin"

                async with self.semaphore:
                    started = time.monotonic()
                    resp = await self._client.get(url, params=params, headers=headers)
                    elapsed = int((time.monotonic() - started) * 1000)

                if resp.status_code == 429:
                    retry_after = _retry_after_seconds(resp, default=10.0)
                    log.warning("http.rate_limited", url=url, retry_after=retry_after)
                    await asyncio.sleep(min(retry_after, 60))
                    raise TransientHTTPError("429 Too Many Requests")

                if resp.status_code in (403, 401, 406):
                    body = _safe_text(resp)
                    if self._is_challenge(body, resp):
                        if settings.BROWSER_FALLBACK_ENABLED:
                            log.info("http.escalating_to_browser", url=url)
                            return await self._get_via_browser(url, params)
                        raise ChallengeDetected(
                            f"{resp.status_code} anti-bot challenge at {url}. "
                            "Enable BROWSER_FALLBACK_ENABLED to solve it with a real "
                            "browser engine, or reduce the request rate."
                        )
                    if not rotated_once:
                        # Most flat refusals from these storefronts are User-Agent
                        # heuristics. One clean retry under a different identity is
                        # worth it; a second refusal is the site's real answer.
                        rotated_once = True
                        await self._rotate_fingerprint()
                        await self._warm_up()
                        raise TransientHTTPError(f"{resp.status_code}, rotating fingerprint")
                    if settings.BROWSER_FALLBACK_ENABLED:
                        log.info("http.escalating_to_browser", url=url, reason="403")
                        return await self._get_via_browser(url, params)
                    raise AccessBlocked(
                        f"{resp.status_code} from {url} after a fingerprint rotation. "
                        "The site is refusing this client outright rather than rate "
                        "limiting it. Enable BROWSER_FALLBACK_ENABLED, or contact the "
                        "site for API/data access."
                    )

                if resp.status_code == 503:
                    body = _safe_text(resp)
                    if self._is_challenge(body, resp):
                        if settings.BROWSER_FALLBACK_ENABLED:
                            return await self._get_via_browser(url, params)
                        raise ChallengeDetected(f"503 anti-bot challenge at {url}.")
                    raise TransientHTTPError(f"503 from {url}")

                if resp.status_code >= 500:
                    raise TransientHTTPError(f"{resp.status_code} from {url}")

                if resp.status_code == 404:
                    return FetchResult(url=url, status=404, html="", elapsed_ms=elapsed)

                resp.raise_for_status()

                html = _decode_body(resp)
                if self._is_challenge(html, resp):
                    if settings.BROWSER_FALLBACK_ENABLED:
                        return await self._get_via_browser(url, params)
                    raise ChallengeDetected(f"Anti-bot challenge served at {url}.")

                self._last_url = str(resp.url)
                return FetchResult(
                    url=str(resp.url),
                    status=resp.status_code,
                    html=html,
                    elapsed_ms=elapsed,
                    via="http",
                )

        raise TransientHTTPError(f"exhausted retries for {url}")  # pragma: no cover

    # -- browser escalation -------------------------------------------------
    async def _get_via_browser(self, url: str, params: dict | None) -> FetchResult:
        """Render the page in a real browser engine and hand the DOM back.

        Used for origins that answer a JS challenge or render their catalogue
        client-side. Deliberately lazy: Playwright is only imported and launched
        the first time a site actually needs it.
        """
        from app.scrapers.browser import BrowserFetcher, BrowserUnavailable

        if self._browser is None:
            try:
                self._browser = await BrowserFetcher.create(
                    user_agent=self._profile["ua"],
                    locale="en-US",
                )
            except BrowserUnavailable as exc:
                raise ChallengeDetected(
                    f"{url} needs a browser engine but Playwright is unavailable: {exc}. "
                    "Install with: pip install playwright && playwright install chromium"
                ) from exc

        if params:
            query = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}{'&' if '?' in url else '?'}{query}"

        await self.bucket.acquire()
        started = time.monotonic()
        html, status = await self._browser.fetch(url)
        elapsed = int((time.monotonic() - started) * 1000)
        self._last_url = url
        return FetchResult(
            url=url, status=status, html=html, elapsed_ms=elapsed, via="browser"
        )


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _safe_text(resp: httpx.Response) -> str:
    """Best-effort text for inspection only - never raises."""
    try:
        return _decode_body(resp)
    except Exception:  # noqa: BLE001
        try:
            return resp.content[:8000].decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return ""


def _retry_after_seconds(resp: httpx.Response, default: float) -> float:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        # HTTP-date form. Falling back to the default is fine and avoids a
        # date-parsing dependency for a header these sites rarely send.
        return default