"""Starter mapping: canonical subcategory -> retailer listing path.

IMPORTANT
---------
Every path below ships with ``is_verified = False``. Retailer URL structures change
without notice, so treat this file as a *starting point*, not as truth. Two tools
turn these guesses into verified mappings:

    python -m app.cli discover --site startech     # crawl the site's own category menu
    python -m app.cli verify   --site startech     # fetch each path, report hit/miss

Anything that stays unmapped simply appears as unavailable for that site in the UI,
which is the correct behaviour: not every retailer sells every category.

Keys are ``"<category-slug>/<subcategory-slug>"``.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# StarTech - custom PHP storefront, path-based categories, ``?page=N`` paging.
# --------------------------------------------------------------------------- #
STARTECH: dict[str, str] = {
    "desktops/gaming-pc": "/desktops/gaming-pc",
    "desktops/all-in-one": "/desktops/all-in-one-pc",
    "desktops/office-pc": "/desktops/brand-pc",
    "desktops/workstation": "/desktops/workstation-pc",
    "laptops/gaming": "/laptop-notebook/gaming-laptop",
    "laptops/business": "/laptop-notebook/laptop",
    "laptops/student": "/laptop-notebook/laptop",
    "laptops/ultrabook": "/laptop-notebook/ultrabook",
    "laptops/macbook": "/laptop-notebook/macbook",
    "components/processor": "/component/processor",
    "components/graphics-card": "/component/graphics-card",
    "components/motherboard": "/component/motherboard",
    "components/ram": "/component/ram",
    "components/ssd": "/component/ssd",
    "components/hdd": "/component/hard-disk-drive",
    "components/power-supply": "/component/power-supply",
    "components/cpu-cooler": "/component/cpu-cooler",
    "components/casing": "/component/casing",
    "components/case-fan": "/component/casing-cooler",
    "components/ups": "/ups",
    "components/operating-system": "/software/operating-system",
    "monitors/gaming": "/monitor/gaming-monitor",
    "monitors/4k": "/monitor/4k-monitor",
    "monitors/office": "/monitor",
    "phone/iphone": "/smartphone/iphone",
    "phone/android": "/smartphone",
    "tablet/ipad": "/tablet-pc/ipad",
    "tablet/android-tablet": "/tablet-pc",
    "office-equipment/printer": "/office-equipment/printer",
    "office-equipment/scanner": "/office-equipment/scanner",
    "office-equipment/photocopier": "/office-equipment/photocopier",
    "office-equipment/projector": "/office-equipment/projector",
    "camera/dslr": "/camera/dslr-camera",
    "camera/mirrorless": "/camera/mirrorless-camera",
    "camera/action-camera": "/camera/action-camera",
    "camera/lens": "/camera/camera-lens",
    "camera/tripod": "/camera/tripod",
    "security/cctv": "/security/cc-camera",
    "security/ip-camera": "/security/ip-camera",
    "security/dvr-nvr": "/security/nvr-dvr",
    "security/access-control": "/security/access-control",
    "networking/router": "/networking/router",
    "networking/switch": "/networking/switch",
    "networking/wifi-adapter": "/networking/wireless-adapter",
    "networking/access-point": "/networking/access-point",
    "software/operating-system": "/software/operating-system",
    "software/antivirus": "/software/antivirus",
    "server-storage/server": "/server-networking/server",
    "server-storage/nas": "/server-networking/nas-storage",
    "server-storage/external-drive": "/storage-device/external-hard-disk",
    "accessories/keyboard": "/accessories/keyboard",
    "accessories/mouse": "/accessories/mouse",
    "accessories/mouse-pad": "/accessories/mouse-pad",
    "accessories/headphone": "/accessories/headphone",
    "accessories/webcam": "/accessories/webcam",
    "accessories/speaker": "/accessories/speaker",
    "accessories/microphone": "/accessories/microphone",
    "gadgets/smartwatch": "/gadget/smart-watch",
    "gadgets/earbuds": "/gadget/earphone",
    "gadgets/power-bank": "/gadget/power-bank",
    "gadgets/drone": "/gadget/drone",
    "gaming-gear/controller": "/gaming/gaming-controller",
    "gaming-gear/gaming-chair": "/gaming/gaming-chair",
    "gaming-gear/gaming-mouse": "/gaming/gaming-mouse",
    "gaming-gear/headset": "/gaming/gaming-headset",
    "gaming-gear/console": "/gaming/gaming-console",
    "tv/smart-tv": "/tv/smart-tv",
    "tv/android-tv": "/tv/android-tv",
    "tv/soundbar": "/tv/soundbar",
    "apple-store/mac": "/apple/macbook",
    "apple-store/ipad": "/apple/ipad",
    "apple-store/iphone": "/apple/iphone",
    "apple-store/apple-watch": "/apple/apple-watch",
    "apple-store/airpods": "/apple/airpods",
}

# --------------------------------------------------------------------------- #
# Ryans - listing pages live under /category/<slug>, paged with ``?page=N``.
# --------------------------------------------------------------------------- #
RYANS: dict[str, str] = {
    "desktops/gaming-pc": "/category/desktop-gaming-pc",
    "desktops/all-in-one": "/category/desktop-all-in-one-pc",
    "desktops/office-pc": "/category/desktop-brand-pc",
    "laptops/gaming": "/category/laptop-gaming-laptop",
    "laptops/business": "/category/laptop-all-laptop",
    "laptops/macbook": "/category/laptop-macbook",
    "components/processor": "/category/component-processor",
    "components/graphics-card": "/category/component-graphics-card",
    "components/motherboard": "/category/component-motherboard",
    "components/ram": "/category/component-desktop-ram",
    "components/ssd": "/category/component-ssd",
    "components/hdd": "/category/component-hard-disk-drive",
    "components/power-supply": "/category/component-power-supply",
    "components/cpu-cooler": "/category/component-cpu-cooler",
    "components/casing": "/category/component-casing",
    "components/case-fan": "/category/component-casing-cooler",
    "components/ups": "/category/ups-all-ups",
    "monitors/gaming": "/category/monitor-gaming-monitor",
    "monitors/office": "/category/monitor-all-monitor",
    "phone/android": "/category/phone-all-phone",
    "tablet/android-tablet": "/category/tab-all-tab",
    "office-equipment/printer": "/category/printer-all-printer",
    "office-equipment/scanner": "/category/printer-scanner",
    "office-equipment/projector": "/category/projector-all-projector",
    "camera/dslr": "/category/camera-dslr-camera",
    "camera/action-camera": "/category/camera-action-camera",
    "security/cctv": "/category/security-cc-camera",
    "security/ip-camera": "/category/security-ip-camera",
    "networking/router": "/category/networking-router",
    "networking/switch": "/category/networking-switch",
    "networking/access-point": "/category/networking-access-point",
    "server-storage/server": "/category/server-server",
    "accessories/keyboard": "/category/accessories-keyboard",
    "accessories/mouse": "/category/accessories-mouse",
    "accessories/mouse-pad": "/category/accessories-mouse-pad",
    "accessories/headphone": "/category/accessories-headphone",
    "accessories/webcam": "/category/accessories-webcam",
    "accessories/speaker": "/category/accessories-speaker",
    "gadgets/smartwatch": "/category/gadget-smart-watch",
    "gadgets/power-bank": "/category/gadget-power-bank",
    "gaming-gear/gaming-chair": "/category/gaming-gaming-chair",
    "gaming-gear/gaming-mouse": "/category/gaming-gaming-mouse",
    "tv/smart-tv": "/category/tv-smart-tv",
}

# --------------------------------------------------------------------------- #
# TechLand BD - OpenCart. Category paths, paging via ``?page=N`` and ``&limit=``.
# --------------------------------------------------------------------------- #
TECHLAND: dict[str, str] = {
    "desktops/gaming-pc": "/gaming-pc",
    "desktops/all-in-one": "/all-in-one-pc",
    "laptops/gaming": "/laptop/gaming-laptop",
    "laptops/business": "/laptop",
    "laptops/macbook": "/laptop/macbook",
    "components/processor": "/components/processor",
    "components/graphics-card": "/components/graphics-card",
    "components/motherboard": "/components/motherboard",
    "components/ram": "/components/ram",
    "components/ssd": "/components/ssd",
    "components/hdd": "/components/hard-disk-drive",
    "components/power-supply": "/components/power-supply",
    "components/cpu-cooler": "/components/cpu-cooler",
    "components/casing": "/components/casing",
    "components/case-fan": "/components/casing-fan",
    "components/ups": "/ups",
    "monitors/gaming": "/monitor/gaming-monitor",
    "monitors/office": "/monitor",
    "networking/router": "/networking/router",
    "networking/switch": "/networking/switch",
    "security/cctv": "/security-system/cc-camera",
    "office-equipment/printer": "/office-equipment/printer",
    "accessories/keyboard": "/accessories/keyboard",
    "accessories/mouse": "/accessories/mouse",
    "accessories/mouse-pad": "/accessories/mouse-pad",
    "accessories/headphone": "/accessories/headphone",
    "accessories/webcam": "/accessories/webcam",
    "accessories/speaker": "/accessories/speaker",
    "gadgets/smartwatch": "/gadget/smart-watch",
    "gadgets/power-bank": "/gadget/power-bank",
    "gaming-gear/gaming-chair": "/gaming/gaming-chair",
    "gaming-gear/gaming-mouse": "/gaming/gaming-mouse",
    "gaming-gear/headset": "/gaming/gaming-headset",
    "server-storage/external-drive": "/storage/external-hdd",
}

# --------------------------------------------------------------------------- #
# Computer Mania BD - WooCommerce, ``/product-category/<slug>/``, ``/page/N/``.
# --------------------------------------------------------------------------- #
COMPUTERMANIA: dict[str, str] = {
    "desktops/gaming-pc": "/product-category/desktop/gaming-pc/",
    "laptops/gaming": "/product-category/laptop/gaming-laptop/",
    "laptops/business": "/product-category/laptop/",
    "components/processor": "/product-category/components/processor/",
    "components/graphics-card": "/product-category/components/graphics-card/",
    "components/motherboard": "/product-category/components/motherboard/",
    "components/ram": "/product-category/components/ram/",
    "components/ssd": "/product-category/components/ssd/",
    "components/hdd": "/product-category/components/hdd/",
    "components/power-supply": "/product-category/components/power-supply/",
    "components/casing": "/product-category/components/casing/",
    "components/ups": "/product-category/ups/",
    "monitors/office": "/product-category/monitor/",
    "monitors/gaming": "/product-category/monitor/gaming-monitor/",
    "networking/router": "/product-category/networking/router/",
    "security/cctv": "/product-category/security/cc-camera/",
    "office-equipment/printer": "/product-category/printer/",
    "accessories/keyboard": "/product-category/accessories/keyboard/",
    "accessories/mouse": "/product-category/accessories/mouse/",
    "accessories/headphone": "/product-category/accessories/headphone/",
    "accessories/speaker": "/product-category/accessories/speaker/",
    "gadgets/smartwatch": "/product-category/gadget/smart-watch/",
    "gadgets/power-bank": "/product-category/gadget/power-bank/",
    "gaming-gear/gaming-chair": "/product-category/gaming/gaming-chair/",
}

SITE_MAPS: dict[str, dict[str, str]] = {
    "startech": STARTECH,
    "ryans": RYANS,
    "techland": TECHLAND,
    "computermania": COMPUTERMANIA,
}
