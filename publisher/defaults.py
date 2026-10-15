"""Publisher defaults, merged with the `publisher:` section of config/settings.yaml.

Etsy facts behind these values were checked on 2026-10-15 against the Open API v3 spec
(etsy.com/openapi/generated/oas/3.0.0.json) and developer.etsy.com/documentation.
"""
from __future__ import annotations

from core import etsy
from core.config import load_settings, section

DEFAULTS = {
    "api_base": "https://api.etsy.com/v3/application",
    "token_url": "https://api.etsy.com/v3/public/oauth/token",
    "connect_url": "https://www.etsy.com/oauth/connect",
    # listings_w: createDraftListing + uploads. listings_r: read our drafts back (resume)
    # and own-stats. shops_r: getMe. listings_d: deleteListing, used only by
    # `python -m publisher discard` on drafts this tool created; drop it to forbid deletes.
    "scopes": ["listings_r", "listings_w", "listings_d", "shops_r"],
    "currency": "USD",            # listing.json prices are USD; a shop in another currency is refused
    "max_files": etsy.MAX_FILES,            # Etsy: up to 5 digital files per listing, 20 MB each
    "max_file_bytes": etsy.MAX_FILE_BYTES,  # (Help Center; core/etsy.py, shared with qa)
    "max_images": 20,             # spec: image_ids "can include up to 20 images"
    "alt_text_max_chars": etsy.ALT_TEXT_MAX_CHARS,  # spec: uploadListingImage alt_text "Max length 500 characters"
    # Etsy's price range is USD 0.20-50,000 and a listing holds at most 999 units (Etsy
    # seller docs as quoted by integrators; the spec only says "positive non-zero").
    "min_price_usd": 0.20,
    "max_price_usd": 50_000,
    "max_quantity": 999,
    "extra_fields": {},           # extra createDraftListing fields, e.g. {shop_section_id: 123}
    "timeout_s": 60,
    "retries": 4,                 # per request, for 429 and 5xx
    "backoff_s": 2.0,             # 2, 4, 8, 16 s ...
    "max_wait_s": 120,            # a longer Retry-After (daily quota spent) fails instead of sleeping
    "token_skew_s": 120,          # refresh this long before the access token expires
    "auth_timeout_s": 300,        # how long `auth` waits for the browser redirect
    "own_stats_file": "data/research_inputs/own_shop_stats.csv",
    "dry_run_shop_id": 10000001,  # fake numeric shop id for dry runs without ETSY_SHOP_ID
}


def load_cfg(settings: dict | None = None) -> dict:
    return section(settings or load_settings(), "publisher", DEFAULTS)
