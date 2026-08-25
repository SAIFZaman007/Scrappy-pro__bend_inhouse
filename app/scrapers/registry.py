"""Site adapter registry - the single place that knows every supported retailer."""
from __future__ import annotations

from app.scrapers.base import BaseScraper
from app.scrapers.sites.computermania import ComputerManiaScraper
from app.scrapers.sites.ryans import RyansScraper
from app.scrapers.sites.startech import StarTechScraper
from app.scrapers.sites.techland import TechLandScraper

SCRAPERS: dict[str, type[BaseScraper]] = {
    StarTechScraper.key: StarTechScraper,
    TechLandScraper.key: TechLandScraper,
    RyansScraper.key: RyansScraper,
    ComputerManiaScraper.key: ComputerManiaScraper,
}


def get_scraper_class(site_key: str) -> type[BaseScraper]:
    try:
        return SCRAPERS[site_key]
    except KeyError as exc:
        raise ValueError(f"No scraper registered for site '{site_key}'") from exc

