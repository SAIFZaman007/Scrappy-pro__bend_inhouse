# backend/app/scrapers/browser.py
"""Headless-browser fallback for origins the plain HTTP client cannot read.

Three of the four targets (StarTech, TechLand, Ryans) are fully server-rendered
and never need this. Computer Mania BD sits behind a Cloudflare configuration
that intermittently serves a JavaScript interstitial, and that interstitial
cannot be satisfied by any amount of header tuning - it has to execute.

Design notes
------------
* **Lazy and shared.** Playwright is imported only when a site actually escalates,
  and one browser process is reused for the whole job. Launching Chromium per
  request would cost ~1s and ~150 MB each time.
* **Optional dependency.** If Playwright is not installed the caller gets a clear
  ``BrowserUnavailable`` with the exact install command, instead of an
  ImportError buried in a worker traceback.
* **Automation flags stripped.** ``navigator.webdriver`` and the headless Chrome
  argv flags are the two things every bot-detection script checks first. We turn
  them off so a legitimate crawler is not misclassified purely on those.
* **Images and fonts blocked.** We only ever want the DOM. Blocking media cuts
  page weight by roughly 80% and is also the polite thing to do - it is far less
  origin bandwidth than a real visitor would consume.

This module is intentionally small. If a site ever needs full interaction
(clicking "load more", infinite scroll) that logic belongs in the site adapter,
which can call ``fetch`` with a ``wait_for`` selector.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

BLOCKED_RESOURCE_TYPES = {"image", "media", "font", "stylesheet"}

# Removes the two highest-signal automation tells before any page script runs.
STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || { runtime: {} };
"""

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",  # required in containers with a small /dev/shm
    "--no-sandbox",
    "--disable-gpu",
    "--disable-background-networking",
]


class BrowserUnavailable(RuntimeError):
    """Playwright is not installed, or Chromium was never downloaded."""


class BrowserFetcher:
    """One long-lived Chromium context, shared across a job."""

    def __init__(self, playwright: Any, browser: Any, context: Any) -> None:
        self._playwright = playwright
        self._browser = browser
        self._context = context
        self._lock = asyncio.Lock()

    # -- construction -------------------------------------------------------
    @classmethod
    async def create(cls, user_agent: str, locale: str = "en-US") -> BrowserFetcher:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise BrowserUnavailable(
                "playwright is not installed. Install with: "
                "pip install 'playwright>=1.48' && playwright install chromium"
            ) from exc

        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.launch(
                headless=settings.BROWSER_HEADLESS, args=LAUNCH_ARGS
            )
        except Exception as exc:  # pragma: no cover - missing browser binary
            await playwright.stop()
            raise BrowserUnavailable(
                f"Could not launch Chromium ({exc}). Run: playwright install chromium"
            ) from exc

        context = await browser.new_context(
            user_agent=user_agent,
            locale=locale,
            timezone_id="Asia/Dhaka",
            viewport={"width": 1440, "height": 900},
            java_script_enabled=True,
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9,bn;q=0.8"},
        )
        await context.add_init_script(STEALTH_INIT_SCRIPT)
        await context.route("**/*", cls._route_filter)
        log.info("browser.started", headless=settings.BROWSER_HEADLESS)
        return cls(playwright, browser, context)

    @staticmethod
    async def _route_filter(route: Any, request: Any) -> None:
        if request.resource_type in BLOCKED_RESOURCE_TYPES:
            await route.abort()
        else:
            await route.continue_()

    # -- fetching -----------------------------------------------------------
    async def fetch(
        self,
        url: str,
        wait_for: str | None = None,
        timeout_ms: int | None = None,
    ) -> tuple[str, int]:
        """Return ``(html, status)`` for ``url``.

        Serialised behind a lock: one page at a time per browser keeps memory
        bounded and keeps the request rate matched to the token bucket that
        already gated this call.
        """
        timeout = timeout_ms or int(settings.BROWSER_TIMEOUT_SECONDS * 1000)
        async with self._lock:
            page = await self._context.new_page()
            try:
                response = await page.goto(
                    url, wait_until="domcontentloaded", timeout=timeout
                )
                status = response.status if response else 0

                # Cloudflare's interstitial replaces itself once the challenge
                # clears. Waiting for network idle is the cheapest reliable way
                # to let that happen without polling for vendor-specific markup.
                try:
                    await page.wait_for_load_state("networkidle", timeout=timeout // 2)
                except Exception:  # noqa: BLE001 - long-poll pages never idle
                    pass

                if wait_for:
                    try:
                        await page.wait_for_selector(wait_for, timeout=timeout // 2)
                    except Exception:  # noqa: BLE001 - absent selector is not fatal
                        log.info("browser.selector_timeout", url=url, selector=wait_for)

                html = await page.content()
                log.info("browser.fetched", url=url, status=status, bytes=len(html))
                return html, status
            finally:
                await page.close()

    async def close(self) -> None:
        try:
            await self._context.close()
            await self._browser.close()
            await self._playwright.stop()
            log.info("browser.stopped")
        except Exception as exc:  # noqa: BLE001 - shutdown must never raise
            log.warning("browser.close_failed", error=str(exc))