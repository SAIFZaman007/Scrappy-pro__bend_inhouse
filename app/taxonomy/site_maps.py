# backend/app/taxonomy/site_maps.py
"""Canonical subcategory -> retailer listing path, built from live retailer navigation.

Every path below was checked against the real site on 2026-08-18 - either by reading
the site's own rendered navigation menu directly, or (where marked) inferred from a
confirmed sibling URL following an obvious, consistent pattern.

Each entry is ``"cat-slug/sub-slug": MappingEntry(path, verified)``:

    verified=True   read directly from the site's live navigation or a fetched page.
                     Seeded with the amber "unverified" dot already cleared.
    verified=False  inferred from a strongly-evidenced URL pattern (e.g. a confirmed
                     child page implies its parent), but not fetched directly.
                     Seeded with the amber dot still showing - check it with
                     `make verify SITE=<site>` before relying on it for a big run.

A subcategory absent from a site's dict means exactly that: this retailer does not
carry it, or no reliable URL could be found. It will show disabled (greyed out) in
the picker rather than pointing at a URL that doesn't exist - that is the correct,
honest state, not a bug. Two of the four sites in particular need more work:

  * TechLand's category menu is rendered client-side in JavaScript, so only the
    subset of paths that also appear in its server-rendered SEO footer text and
    fetched category pages could be confirmed here. `make discover SITE=techland`
    against the real (JS-rendered) nav in a browser is the fastest way to fill gaps.
  * Computer Mania BD is a laptop-and-components specialist, not a full-catalogue
    electronics store - it does not sell TVs, phones, security gear, networking
    equipment, or office equipment at all. Leaving those unmapped here is accurate,
    not incomplete.

Re-running `python -m app.cli seed` (or just restarting the app) re-applies this
file to the database. It only ever touches rows that are still unverified - a path
a human has confirmed with `--mark` is never silently overwritten - and it now also
removes stale unverified rows that no longer appear here, so a category we've since
decided not to guess at won't linger as a false "mapped" option in the picker.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MappingEntry:
    path: str
    verified: bool = True


# --------------------------------------------------------------------------- #
# StarTech - custom PHP storefront. Full mega-menu read directly from the live
# homepage. Paging is `?page=N`, appended by the scraper automatically.
# --------------------------------------------------------------------------- #
STARTECH: dict[str, MappingEntry] = {
    "desktops/gaming-pc": MappingEntry("/desktops/gaming-pc"),
    "desktops/workstation": MappingEntry("/server-networking/workstation"),
    "desktops/office-pc": MappingEntry("/desktops/brand-pc"),
    "desktops/all-in-one": MappingEntry("/desktops/all-in-one-pc"),
    "laptops/gaming": MappingEntry("/gaming-laptop"),
    "laptops/business": MappingEntry("/laptop-notebook/laptop"),
    "laptops/student": MappingEntry("/laptop-notebook/laptop"),
    "laptops/ultrabook": MappingEntry("/laptop-notebook/ultrabook"),
    "laptops/macbook": MappingEntry("/apple-macbook"),
    "components/processor": MappingEntry("/component/processor"),
    "components/graphics-card": MappingEntry("/component/graphics-card"),
    "components/motherboard": MappingEntry("/component/motherboard"),
    "components/ram": MappingEntry("/component/ram"),
    "components/ssd": MappingEntry("/ssd"),
    "components/hdd": MappingEntry("/component/hard-disk-drive"),
    "components/power-supply": MappingEntry("/component/power-supply"),
    "components/cpu-cooler": MappingEntry("/component/CPU-Cooler"),
    "components/casing": MappingEntry("/component/casing"),
    "components/case-fan": MappingEntry("/component/casing-cooler"),
    "components/ups": MappingEntry("/ups"),
    "components/operating-system": MappingEntry("/operating-system"),
    "monitors/gaming": MappingEntry("/gaming-monitor"),
    "monitors/4k": MappingEntry("/4k-monitor"),
    "monitors/office": MappingEntry("/monitor"),
    "phone/iphone": MappingEntry("/apple-iphone"),
    "phone/android": MappingEntry("/mobile-phone"),
    "phone/feature-phone": MappingEntry("/feature-phone"),
    "tablet/ipad": MappingEntry("/apple-ipad"),
    "tablet/android-tablet": MappingEntry("/tablet-pc"),
    "tablet/drawing-tablet": MappingEntry("/graphics-tablet"),
    "office-equipment/printer": MappingEntry("/printer"),
    "office-equipment/scanner": MappingEntry("/office-equipment/Scanner"),
    "office-equipment/photocopier": MappingEntry("/photocopier"),
    "office-equipment/projector": MappingEntry("/projector"),
    "office-equipment/shredder": MappingEntry("/office-equipment/paper-shredder"),
    "camera/dslr": MappingEntry("/dslr-camera"),
    "camera/mirrorless": MappingEntry("/mirrorless-camera"),
    "camera/action-camera": MappingEntry("/camera/action-camera"),
    "camera/lens": MappingEntry("/camera/camera-lenses"),
    "camera/tripod": MappingEntry("/camera/camera-tripod"),
    "security/cctv": MappingEntry("/Security-Camera/cc-camera"),
    "security/ip-camera": MappingEntry("/Security-Camera/ip-camera"),
    "security/dvr-nvr": MappingEntry("/Security-Camera/nvr"),
    "security/smart-lock": MappingEntry("/door-lock"),
    "security/access-control": MappingEntry("/access-control"),
    "networking/router": MappingEntry("/networking/router"),
    "networking/switch": MappingEntry("/networking/network-switch"),
    "networking/cable": MappingEntry("/networking-cable"),
    "networking/wifi-adapter": MappingEntry("/wifi-adapter"),
    "networking/access-point": MappingEntry("/access-point"),
    "software/operating-system": MappingEntry("/operating-system"),
    "software/antivirus": MappingEntry("/software/antivirus"),
    "software/office-suite": MappingEntry("/office-application"),
    "software/design": MappingEntry("/software/adobe"),
    "software/utility": MappingEntry("/software"),
    "server-storage/server": MappingEntry("/server-networking/server"),
    "server-storage/nas": MappingEntry("/nas-storage"),
    "server-storage/external-drive": MappingEntry("/component/portable-hard-disk-drive"),
    "server-storage/enterprise-ssd": MappingEntry("/server-ssd"),
    "accessories/keyboard": MappingEntry("/accessories/keyboards"),
    "accessories/mouse": MappingEntry("/accessories/mouse"),
    "accessories/mouse-pad": MappingEntry("/accessories/mouse-pad"),
    "accessories/headphone": MappingEntry("/accessories/headphone"),
    "accessories/webcam": MappingEntry("/accessories/webcam"),
    "accessories/speaker": MappingEntry("/accessories/speaker-and-home-theater"),
    "accessories/microphone": MappingEntry("/accessories/microphone"),
    "gadgets/smartwatch": MappingEntry("/gadget/smart-watch"),
    "gadgets/earbuds": MappingEntry("/earbuds"),
    "gadgets/power-bank": MappingEntry("/power-bank"),
    "gadgets/drone": MappingEntry("/drone"),
    "gaming-gear/controller": MappingEntry("/game-pad"),
    "gaming-gear/gaming-chair": MappingEntry("/gaming-chair"),
    "gaming-gear/gaming-mouse": MappingEntry("/gaming-mouse"),
    "gaming-gear/headset": MappingEntry("/gaming-headphone"),
    "gaming-gear/console": MappingEntry("/gaming-console"),
    "tv/smart-tv": MappingEntry("/smart-tv"),
    "tv/4k-tv": MappingEntry("/4k-tv"),
    "tv/android-tv": MappingEntry("/android-tv"),
    "tv/tv-box": MappingEntry("/tv-box"),
    "tv/soundbar": MappingEntry("/soundbar"),
    # The Mac product pages (Mac Mini, iMac, Studio, Pro) are single-model pages on
    # StarTech, not a listing - /apple is the closest real listing page, but it may
    # mix in other Apple categories. Worth confirming with `sample` before relying
    # on it.
    "apple-store/mac": MappingEntry("/apple", verified=False),
    "apple-store/ipad": MappingEntry("/apple-ipad"),
    "apple-store/iphone": MappingEntry("/apple-iphone"),
    "apple-store/apple-watch": MappingEntry("/apple-smart-watch"),
    "apple-store/airpods": MappingEntry("/apple-airpods"),
    "apple-store/apple-accessories": MappingEntry("/apple", verified=False),
}

# --------------------------------------------------------------------------- #
# Ryans - Laravel storefront. Full mega-menu read directly from the live
# homepage. Paging is `?page=N`, appended by the scraper automatically.
# --------------------------------------------------------------------------- #
RYANS: dict[str, MappingEntry] = {
    "desktops/gaming-pc": MappingEntry("/category/gaming-gaming-desktop-pc"),
    "desktops/workstation": MappingEntry("/category/desktop-pc-ai-workstation"),
    "desktops/office-pc": MappingEntry("/category/desktop-pc-ryans-pc"),
    "desktops/all-in-one": MappingEntry("/category/desktop-pc-all-in-one-pc"),
    "laptops/gaming": MappingEntry("/category/gaming-gaming-laptop"),
    "laptops/business": MappingEntry("/category/laptop-all-laptop"),
    "laptops/student": MappingEntry("/category/laptop-all-laptop"),
    "laptops/macbook": MappingEntry("/category/all-laptop-apple"),
    "components/processor": MappingEntry("/category/desktop-component-processor"),
    "components/graphics-card": MappingEntry("/category/desktop-component-graphics-card"),
    "components/motherboard": MappingEntry("/category/desktop-component-motherboard"),
    "components/ram": MappingEntry("/category/desktop-component-desktop-ram"),
    "components/ssd": MappingEntry("/category/internal-ssd"),
    "components/hdd": MappingEntry("/category/internal-hdd"),
    "components/power-supply": MappingEntry("/category/desktop-component-power-supply"),
    "components/cpu-cooler": MappingEntry("/category/desktop-component-cpu-cooler"),
    "components/casing": MappingEntry("/category/desktop-component-casing"),
    "components/case-fan": MappingEntry("/category/desktop-component-casing-fan"),
    "components/ups": MappingEntry("/category/desktop-component-ups"),
    "components/operating-system": MappingEntry("/category/operating-system-and-db"),
    "monitors/gaming": MappingEntry("/category/gaming-monitor"),
    "monitors/office": MappingEntry("/category/monitor-all-monitor"),
    "phone/iphone": MappingEntry("/category/smart-phone-apple"),
    "phone/android": MappingEntry("/category/mobile-phone-smart-phone"),
    "phone/feature-phone": MappingEntry("/category/mobile-phone-feature-phone"),
    "tablet/ipad": MappingEntry("/category/apple-tablet-ipad"),
    "tablet/android-tablet": MappingEntry("/category/regular-tablet"),
    "tablet/drawing-tablet": MappingEntry("/category/graphics-tablet"),
    "office-equipment/printer": MappingEntry("/category/printer-all"),
    "office-equipment/scanner": MappingEntry("/category/office-items-scanner"),
    "office-equipment/photocopier": MappingEntry("/category/office-items-photocopier"),
    "office-equipment/projector": MappingEntry("/category/office-items-projector"),
    "office-equipment/shredder": MappingEntry("/category/office-items-paper-shredder"),
    "camera/dslr": MappingEntry("/category/dslr-camera"),
    "camera/mirrorless": MappingEntry("/category/mirrorless-camera"),
    "camera/action-camera": MappingEntry("/category/digital-compact-camera-action-camera"),
    "camera/lens": MappingEntry("/category/camera-dslr-camera-lens"),
    "camera/tripod": MappingEntry("/category/studio-equipments-tripod"),
    "security/cctv": MappingEntry("/category/security-camera-panel-cc-camera"),
    "security/ip-camera": MappingEntry("/category/security-camera-panel-ip-camera"),
    "security/dvr-nvr": MappingEntry("/category/security-system-nvr"),
    "security/smart-lock": MappingEntry("/category/home-security-smart-lock"),
    "security/access-control": MappingEntry("/category/security-attendance-system-access-control"),
    "networking/router": MappingEntry("/category/network-network-router"),
    "networking/switch": MappingEntry("/category/network-network-switch"),
    "networking/cable": MappingEntry("/category/network-network-cable"),
    "networking/wifi-adapter": MappingEntry("/category/lan-card-wifi-adapter"),
    "networking/access-point": MappingEntry("/category/network-access-point"),
    "software/operating-system": MappingEntry("/category/operating-system-and-db"),
    "software/antivirus": MappingEntry("/category/antivirus-and-security"),
    "software/office-suite": MappingEntry("/category/office-application"),
    "software/design": MappingEntry("/category/graphics-editing"),
    "software/utility": MappingEntry("/category/software-remote-management"),
    # Note the real double dash - that's a genuine quirk of Ryans' own slug, not a typo.
    "server-storage/server": MappingEntry("/category/desktop-pc--server-server"),
    "server-storage/nas": MappingEntry("/category/network-network-storage"),
    "server-storage/external-drive": MappingEntry("/category/external-hdd"),
    "server-storage/enterprise-ssd": MappingEntry("/category/storage-internal-server-ssd"),
    "accessories/keyboard": MappingEntry("/category/desktop-component-keyboard"),
    "accessories/mouse": MappingEntry("/category/desktop-component-mouse"),
    "accessories/mouse-pad": MappingEntry("/category/desktop-component-mouse-pad"),
    "accessories/headphone": MappingEntry("/category/audio-video-headphone"),
    "accessories/webcam": MappingEntry("/category/camera-webcam"),
    "accessories/speaker": MappingEntry("/category/audio-video-speaker"),
    "accessories/microphone": MappingEntry("/category/sound-system-microphone"),
    "gadgets/smartwatch": MappingEntry("/category/smartwatch"),
    "gadgets/earbuds": MappingEntry("/category/gadget-earbuds"),
    "gadgets/power-bank": MappingEntry("/category/gadget-power-bank"),
    "gadgets/drone": MappingEntry("/category/camera-drone"),
    "gadgets/smart-home": MappingEntry("/category/gadget-smart-door-bell"),
    "gaming-gear/controller": MappingEntry("/category/gaming-component-gaming-controller"),
    "gaming-gear/gaming-chair": MappingEntry("/category/gaming-component-gaming-chair"),
    "gaming-gear/gaming-mouse": MappingEntry("/category/gaming-desktop-component-mouse"),
    "gaming-gear/headset": MappingEntry("/category/sound-system-gaming-headphone"),
    "gaming-gear/console": MappingEntry("/category/gaming-component-gaming-console"),
    "tv/smart-tv": MappingEntry("/category/television-smart-tv"),
    "tv/soundbar": MappingEntry("/category/sound-system-home-theater-systems"),
    "apple-store/ipad": MappingEntry("/category/apple-tablet-ipad"),
    "apple-store/iphone": MappingEntry("/category/smart-phone-apple"),
    "apple-store/airpods": MappingEntry("/category/earbuds-apple"),
}

# --------------------------------------------------------------------------- #
# TechLand BD - the category mega-menu is rendered client-side in JavaScript
# ("Loading menu..." on a plain fetch), so these are the paths that were
# confirmed a different way: TechLand's own server-rendered SEO footer text and
# directly-fetched category pages. Sparser than the other three sites for that
# reason, not because TechLand's real catalogue is smaller - `make discover
# SITE=techland` from a real browser session is the fastest way to fill gaps.
# --------------------------------------------------------------------------- #
TECHLAND: dict[str, MappingEntry] = {
    "desktops/gaming-pc": MappingEntry("/computers/desktop-computer/gaming-desktop-pc"),
    "laptops/business": MappingEntry("/shop-laptop-computer/brand-laptops"),
    "laptops/student": MappingEntry("/shop-laptop-computer/brand-laptops"),
    "laptops/macbook": MappingEntry("/shop-laptop-computer/brand-laptops/apple-macbook"),
    "components/processor": MappingEntry("/pc-components/processor"),
    "components/graphics-card": MappingEntry("/pc-components/graphics-card"),
    "components/motherboard": MappingEntry("/pc-components/motherboard"),
    "components/ram": MappingEntry("/pc-components/shop-desktop-ram"),
    "components/ssd": MappingEntry("/pc-components/solid-state-drive"),
    "components/casing": MappingEntry("/pc-components/computer-case"),
    "monitors/office": MappingEntry("/monitor-and-display/computer-monitor"),
    "phone/android": MappingEntry("/smartphone-and-tablet/smartphone"),
    "phone/iphone": MappingEntry("/smartphone-and-tablet/smartphone/iphone"),
    "office-equipment/printer": MappingEntry("/office-solution/printer"),
    "office-equipment/scanner": MappingEntry("/office-solution/shop-scanner"),
    "office-equipment/photocopier": MappingEntry("/office-solution/shop-photocopier"),
    "office-equipment/projector": MappingEntry("/office-solution/shop-projectors"),
    "office-equipment/shredder": MappingEntry("/office-solution/paper-shredder"),
    "networking/router": MappingEntry("/server-networking/shop-routers"),
    "accessories/mouse": MappingEntry("/accessories/shop-computer-mouse"),
    "accessories/headphone": MappingEntry("/accessories/headphone-speaker/shop-headphones-headsets"),
    "accessories/mouse-pad": MappingEntry("/accessories/accessories-mouse-pad"),
    "accessories/webcam": MappingEntry("/accessories/brand-webcam"),
    "accessories/speaker": MappingEntry("/tv-home-entertainment/multimedia-speakers"),
    # Inferred parent from confirmed brand sub-links (.../computer-keyboard/logitech-keyboard
    # etc. are real); the bare parent page itself wasn't fetched directly.
    "accessories/keyboard": MappingEntry("/accessories/computer-keyboard", verified=False),
    "gadgets/smartwatch": MappingEntry("/gadget-accessories/smart-watch-gadget/smart-watch"),
    "gadgets/earbuds": MappingEntry("/accessories/headphone-speaker/earbuds"),
    "tv/smart-tv": MappingEntry("/tv-home-entertainment/television-price"),
    "apple-store/iphone": MappingEntry("/smartphone-and-tablet/smartphone/iphone"),
}

# --------------------------------------------------------------------------- #
# Computer Mania BD - WooCommerce (`/product-category/<slug>/`, `/page/N/`
# pagination). This is a laptop-and-desktop-components specialist: it does not
# sell TVs, phones, security gear, networking equipment, or office equipment,
# so those categories are correctly absent below rather than incomplete.
# --------------------------------------------------------------------------- #
COMPUTERMANIA: dict[str, MappingEntry] = {
    "desktops/gaming-pc": MappingEntry("/product-category/desktop/"),
    "laptops/gaming": MappingEntry("/product-category/gaming/"),
    "laptops/business": MappingEntry("/product-category/business-class-laptop/"),
    "laptops/student": MappingEntry("/product-category/laptop/"),
    "laptops/ultrabook": MappingEntry("/product-category/ultrabook/"),
    # Confirmed sibling brand pages (/product-category/hp/, /product-category/msi/,
    # /product-category/dell/alienware/) follow this exact pattern; the Apple page
    # itself wasn't fetched directly.
    "laptops/macbook": MappingEntry("/product-category/apple/", verified=False),
    # All nine of these share one confirmed real page:
    # /product-category/desktop-components/processor/intel-processor-price/ - which
    # confirms both the `desktop-components/<part>/` nesting and that the site's own
    # menu lists exactly these nine part types. Only the `processor` leaf itself was
    # fetched directly; the rest follow the same confirmed pattern.
    "components/processor": MappingEntry("/product-category/desktop-components/processor/", verified=False),
    "components/graphics-card": MappingEntry("/product-category/desktop-components/graphics-card/", verified=False),
    "components/motherboard": MappingEntry("/product-category/desktop-components/motherboard/", verified=False),
    "components/ram": MappingEntry("/product-category/desktop-components/desktop-ram/", verified=False),
    "components/ssd": MappingEntry("/product-category/desktop-components/storage-ssd/", verified=False),
    "components/hdd": MappingEntry("/product-category/desktop-components/storage-hdd/", verified=False),
    "components/power-supply": MappingEntry("/product-category/desktop-components/power-supply/", verified=False),
    "components/cpu-cooler": MappingEntry("/product-category/desktop-components/cpu-cooler/", verified=False),
    "components/casing": MappingEntry("/product-category/desktop-components/casing/", verified=False),
    # The site's own menu lists a "MONITOR" section (children: BenQ, MSI, PC Power,
    # ViewSonic, Xiaomi) and a separate "GAMING MONITOR" section - parents inferred,
    # not fetched directly.
    "monitors/office": MappingEntry("/product-category/monitor/", verified=False),
    "monitors/gaming": MappingEntry("/product-category/gaming-monitor/", verified=False),
    # The menu's "ACCESSORIES" section lists Keyboard, Mouse, Headphone, Earphone as
    # children - parents inferred from that listing, not fetched directly.
    "accessories/keyboard": MappingEntry("/product-category/accessories/keyboard/", verified=False),
    "accessories/mouse": MappingEntry("/product-category/accessories/mouse/", verified=False),
    "accessories/headphone": MappingEntry("/product-category/accessories/headphone/", verified=False),
    "gadgets/earbuds": MappingEntry("/product-category/accessories/earphone/", verified=False),
}

SITE_MAPS: dict[str, dict[str, MappingEntry]] = {
    "startech": STARTECH,
    "ryans": RYANS,
    "techland": TECHLAND,
    "computermania": COMPUTERMANIA,
}