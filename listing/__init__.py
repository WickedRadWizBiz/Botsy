"""Listing stage: title, 13 tags, description and alt text for one QA-passed item.

    write_listing(item_dir, *, client=None, dry_run=False, settings=None, review_dir=None) -> dict
    run_all(out_root=None, *, client=None, dry_run=False, settings=None, review_dir=None) -> list[dict]

Writes <item>/listing.json (docs/CONTRACTS.md section 4) and sets the manifest status to
"listed". The text model sees only our own product facts (listing/facts.py), never a
competitor's title, description or image. Its JSON draft, or in a dry run a template
draft, always goes through the same Etsy rule check (listing/rules.py); a model draft that
fails is sent back with the problems up to `max_retries` times, then repaired in code.

Compliance gates, in order: our own item data is checked against the trademark blocklist
before anything is sent; a blocked term in the data, a model draft or the final listing
raises ListingComplianceError and no listing.json is written; a `?term` sets
needs_review and is added to the review queue. The AI disclosure from
config/ai_disclosure.txt ends every description when the item is AI-assisted, and by
default on every listing (`always_disclose_ai`), because the listing copy itself is
written with AI.
"""
from __future__ import annotations

import logging
from pathlib import Path

from core.compliance import Blocklist, find_urls, has_ai_disclosure, iter_strings, load_ai_disclosure
from core.config import get_shop, load_env, load_settings, section, today
from core.items import iter_item_dirs, output_root, read_json, read_manifest, set_status, write_json
from listing import model, review, rules, template
from listing.errors import ListingComplianceError, ListingError, ListingValidationError
from listing.facts import gather, safe_rel

log = logging.getLogger("listing")

DEFAULTS = {
    "title_max_chars": 140,         # Etsy Help Center; settings listing.title_max_chars
    "tags_count": 13,
    "tag_max_chars": 20,
    "alt_text_max_chars": 500,      # Open API v3 uploadListingImage alt_text: "Max length 500 characters."
    "hook_max_chars": 300,
    "about_max_chars": 900,
    "suggested_uses": {"min": 3, "max": 6, "max_chars": 160},
    # The copy is AI-written even when the designs are not (procedural activity sheets),
    # so the disclosure goes on every listing unless this is switched off.
    "always_disclose_ai": True,
    "quantity": 999,
    # Etsy seller taxonomy id per niche. createDraftListing requires one; null until the
    # user picks them (Etsy's getSellerTaxonomyNodes lists the ids).
    "taxonomy_ids": {"stickers": None, "coloring_pages": None, "activity_sheets": None},
    "max_retries": 2,               # re-asks after the first model draft
    "effort": "low",                # Opus 5.5 always thinks; thinking counts toward max_tokens
    "refusal_fallback": True,       # server-side fallback model if the request is declined
    "max_subjects": 12,             # design subjects passed to the model as facts
    "terms": "For personal use only. Please do not share, resell or redistribute the files or printed copies.",
    # "hand made" also matches "handmade" and "hand-made" (core.compliance term matching).
    "misleading_phrases": ["hand drawn", "hand illustrated", "hand painted", "drawn by hand", "painted by hand",
                           "hand made", "made by hand", "no ai", "ai free", "not ai", "commercial use",
                           "commercial license", "resale rights"],
    "page_size_labels": {"us_letter": "US Letter (8.5 x 11 in)", "a4": "A4 (210 x 297 mm)"},
    # What QA's five listing photos show, in QA's order (qa/mockups.py). Used for alt
    # text only when QA made all five; otherwise each photo gets a generic description.
    "mockup_layouts": {
        "default": [
            "cover photo: sample pages fanned out next to a card with the title, page count and file details",
            "a grid of sample pages from the pack",
            "a close-up of one page with a magnified detail",
            "a 'what you get' card listing what is included, next to two sample pages",
            "the included page sizes shown side by side at true scale",
        ],
        "single_size_last": "a close-up of another page with a magnified detail",
        "stickers": [
            "cover photo: stickers next to a card with the title and file details",
            "a grid of the stickers in the pack",
            "a close-up of one sticker showing its clean transparent edges",
            "a 'what you get' card listing what is included",
            "the printable sticker sheet and loose stickers on a wooden desk",
        ],
    },
}

LISTABLE_STATUSES = ("qa_passed", "listed")
PLACEHOLDER_SHOP_NAME = "YOUR_SHOP_NAME"


def write_listing(item_dir: str | Path, *, client=None, dry_run: bool = False, settings: dict | None = None,
                  review_dir: str | Path | None = None) -> dict:
    """Write listing.json for one QA-passed item and return it. Raises ListingError (or
    ListingComplianceError) when the item cannot be listed; nothing is written then.

    On a rerun that hits a blocked term (say the blocklist grew), the earlier listing.json
    is moved aside and the item goes back to qa_passed, so no stale copy stays ready for
    the publisher."""
    d = Path(item_dir)
    try:
        return _write_listing(d, client=client, dry_run=dry_run, settings=settings or load_settings(),
                              review_dir=review_dir)
    except ListingComplianceError:
        _withdraw_stale(d)
        raise


def _write_listing(d: Path, *, client, dry_run: bool, settings: dict, review_dir) -> dict:
    cfg = section(settings, "listing", DEFAULTS)
    comp = settings.get("compliance") or {}
    manifest, report = _load_item(d)
    item_id = d.name
    try:
        shop = get_shop(settings, manifest.get("shop_id"))
    except KeyError as e:
        raise ListingError(f"{item_id}: {e}") from None
    blocklist = Blocklist.load(comp.get("trademark_blocklist"))
    # Settle everything that can fail cheaply before any API call is spent.
    price, quantity = _price(manifest, shop), _quantity(cfg)
    taxonomy_id = _taxonomy_id(cfg, manifest.get("niche"))
    images = _image_paths(d, report)
    files = _file_paths(d, manifest)
    facts = gather(manifest, images, cfg, settings)
    data_check = rules.check_each(blocklist, iter_strings(facts))
    if data_check.blocked:  # never send a trademark to the model or into a listing
        raise ListingComplianceError(f"{item_id}: item data has blocked terms: {', '.join(data_check.blocked)}",
                                     data_check.blocked)
    if find_urls(facts):
        raise ListingComplianceError(f"{item_id}: item data contains URLs: {find_urls(facts)}")

    fallback = rules.normalize_draft(template.draft(facts, cfg))
    writer = {"kind": "template", "dry_run": bool(dry_run), "attempts": 0, "repaired": []}
    model_name = None
    if dry_run:
        draft = fallback
    else:
        text_cfg = (settings.get("models") or {}).get("text") or {}
        if not text_cfg.get("model"):
            raise ListingError(f"{item_id}: settings models.text.model is not set")
        try:
            result = model.write(client or default_client(), facts, cfg, model=text_cfg["model"],
                                 max_tokens=int(text_cfg.get("max_tokens") or 2000), blocklist=blocklist)
        except ListingComplianceError as e:
            raise ListingComplianceError(f"{item_id}: {e}", e.terms) from None
        except model.API_ERRORS as e:  # the item stays qa_passed, so the next run tries again
            raise ListingError(f"{item_id}: text model request failed: {type(e).__name__}: {e}") from e
        writer["attempts"] = result.attempts
        if result.draft is not None:
            draft, writer["kind"], model_name = result.draft, "model", result.model
        else:
            log.warning("%s: no usable model draft after %d attempts; using template copy", item_id, result.attempts)
            draft = fallback

    n_images = len(images)
    problems = rules.validate(draft, n_images, cfg)
    if problems:
        writer["repaired"] = problems
        draft = rules.repair(draft, fallback, template.tag_candidates(facts, cfg), n_images, cfg)
        left = rules.validate(draft, n_images, cfg)
        if left:
            raise ListingValidationError(f"{item_id}: listing still breaks Etsy rules after repair: {'; '.join(left)}")

    disclose = bool(manifest.get("ai_assisted")) or bool(cfg["always_disclose_ai"])
    disclosure = load_ai_disclosure(shop["name"], comp.get("ai_disclosure")) if disclose else None
    description = template.description(draft, facts, cfg, disclosure)
    included = has_ai_disclosure(description, shop["name"], comp.get("ai_disclosure"))
    if disclose and not included:
        raise ListingError(f"{item_id}: AI disclosure missing from the description")

    # Description checked line by line: its lines are our fixed sections and single
    # model fields, and a term should not be read across a line break between them.
    final_texts = [draft["title"], *draft["tags"], *description.splitlines(), *draft["alt_texts"]]
    found = rules.check_each(blocklist, final_texts)
    if found.blocked:
        raise ListingComplianceError(f"{item_id}: listing has blocked terms: {', '.join(found.blocked)}", found.blocked)
    if find_urls(final_texts):
        raise ListingComplianceError(f"{item_id}: listing contains URLs: {find_urls(final_texts)}")

    listing_reasons = []
    if found.flagged:
        listing_reasons.append(f"listing copy has review terms: {', '.join(found.flagged)}")
    data_only = sorted(set(data_check.flagged) - set(found.flagged))
    if data_only:
        listing_reasons.append(f"item data has review terms: {', '.join(data_only)}")
    reasons = list(listing_reasons)
    if manifest.get("needs_review"):
        reasons.append("item was marked needs_review before listing (brief or generator)")
    if report.get("needs_review"):
        reasons.append("QA warnings: " + "; ".join(report.get("reasons") or ["see qa_report.json"]))

    warnings = []
    if shop.get("name", "").strip() in ("", PLACEHOLDER_SHOP_NAME):
        warnings.append("shop name in config/settings.yaml is still the placeholder; the AI disclosure shows it")
    if taxonomy_id is None:
        warnings.append(f"listing.taxonomy_ids.{manifest.get('niche')} is not set; Etsy needs it to create a draft")
    for w in warnings:
        log.warning("%s: %s", item_id, w)

    listing = {
        "schema": "listing/v1",
        "item_id": item_id,
        "shop_id": shop["id"],
        "title": draft["title"],
        "tags": draft["tags"],
        "description": description,
        "alt_texts": draft["alt_texts"],
        "price_usd": price,
        "quantity": quantity,
        "who_made": "i_did",
        "when_made": "made_to_order",
        "taxonomy_id": taxonomy_id,
        "type": "download",
        "image_paths": images,
        "file_paths": files,
        "ai_disclosure_included": included,
        "compliance": found.merge(data_check).to_dict(),
        "needs_review": bool(reasons),
        "review_reasons": reasons,
        "warnings": warnings,
        "model": model_name,
        # Top level as well as in `writer`: the publisher refuses to draft template copy
        # from a dry run for real (publisher/publish.py reads listing["dry_run"]).
        "dry_run": bool(dry_run),
        "writer": writer,
        "created": today(),
    }
    if listing_reasons:  # queue first: a listing must never exist without its review flag
        rdir = Path(review_dir) if review_dir else review.default_dir(d, settings)
        try:
            review.flag(item_id, manifest.get("niche"), listing_reasons, images, rdir)
        except (OSError, ValueError) as e:  # unreadable entry may hold a decision: do not overwrite it
            raise ListingError(f"{item_id}: could not record review terms in the review queue ({e}); "
                               "not listed") from e
    write_json(d / "listing.json", listing)
    set_status(d, "listed")
    log.info("listed %s (%s, %d attempts): %s", item_id, writer["kind"], writer["attempts"], listing["title"])
    return listing


def run_all(out_root: str | Path | None = None, *, client=None, dry_run: bool = False,
            settings: dict | None = None, review_dir: str | Path | None = None) -> list[dict]:
    """List every qa_passed item. One result per item: {item_id, ok, listing, error}.
    Items rejected in the review queue are skipped (no API call spent on them)."""
    settings = settings or load_settings()
    root = Path(out_root) if out_root else output_root(settings)
    results = []
    for d in iter_item_dirs(root):
        try:
            status = read_manifest(d).get("status")
        except (OSError, ValueError, AttributeError):
            status = None
        if status != "qa_passed":
            continue
        rdir = Path(review_dir) if review_dir else review.default_dir(d, settings)
        try:
            if review.read_status(d.name, rdir) == "rejected":
                log.info("skipping %s: rejected in the review queue", d.name)
                continue
        except ValueError:
            pass  # not a valid item id; write_listing still lists it, it just has no queue entry
        if client is None and not dry_run:
            client = default_client()
        try:
            lst = write_listing(d, client=client, dry_run=dry_run, settings=settings, review_dir=review_dir)
            results.append({"item_id": d.name, "ok": True, "listing": lst, "error": None})
        except Exception as e:  # one bad item must not stop the batch
            if not isinstance(e, ListingError):
                log.exception("listing %s crashed", d.name)
            results.append({"item_id": d.name, "ok": False, "listing": None, "error": f"{type(e).__name__}: {e}"})
    return results


def default_client():
    """Anthropic client from .env (ANTHROPIC_API_KEY); only built when not a dry run."""
    import anthropic

    load_env()
    return anthropic.Anthropic()


# ------------------------------------------------------------------ helpers

def _load_item(d: Path) -> tuple[dict, dict]:
    if not (d / "manifest.json").is_file():
        raise ListingError(f"no manifest.json in {d}")
    try:
        manifest = read_manifest(d)
        report_path = d / "qa_report.json"
        report = read_json(report_path) if report_path.is_file() else None
    except (OSError, ValueError) as e:
        raise ListingError(f"{d.name}: unreadable manifest.json or qa_report.json: {e}") from e
    status = manifest.get("status") if isinstance(manifest, dict) else None
    if status not in LISTABLE_STATUSES:
        raise ListingError(f"{d.name} is {status!r}; only {' or '.join(LISTABLE_STATUSES)} items are listed")
    if not isinstance(report, dict) or report.get("passed") is not True:
        raise ListingError(f"{d.name} has no passing qa_report.json; run QA first")
    return manifest, report


def _withdraw_stale(d: Path) -> None:
    """After a compliance refusal: move an earlier listing.json aside (kept for the user to
    read, never read by the publisher) and set a 'listed' item back to qa_passed."""
    old = d / "listing.json"
    try:
        if old.is_file():
            old.replace(d / "listing.withdrawn.json")
            log.warning("%s: earlier listing.json moved to listing.withdrawn.json after a compliance refusal",
                        d.name)
        if read_manifest(d).get("status") == "listed":
            set_status(d, "qa_passed")
    except (OSError, ValueError) as e:
        log.error("%s: could not withdraw the earlier listing: %s", d.name, e)


def _inside(d: Path, rel: str) -> bool:
    """`rel` resolves inside the item folder (no symlink pointing elsewhere)."""
    try:
        return (d / rel).resolve().is_relative_to(d.resolve())
    except (OSError, RuntimeError):
        return False


def _image_paths(d: Path, report: dict) -> list[str]:
    """QA's listing photos, in QA's order, that exist on disk."""
    out = []
    for p in report.get("mockups") or []:
        rel = safe_rel(p)
        if rel is None or not _inside(d, rel):
            raise ListingError(f"{d.name}: unsafe mockup path {p!r} in qa_report.json")
        if (d / rel).is_file():
            out.append(rel)
        else:
            log.warning("%s: mockup %s is missing; left out of the listing", d.name, rel)
    return out


def _file_paths(d: Path, manifest: dict) -> list[str]:
    out = []
    for e in manifest.get("files") or []:
        rel = safe_rel(e.get("path") if isinstance(e, dict) else None)
        if rel is None or not _inside(d, rel):
            raise ListingError(f"{d.name}: unsafe or missing file path {e!r} in manifest.json")
        if not (d / rel).is_file():
            raise ListingError(f"{d.name}: digital file {rel} does not exist")
        out.append(rel)
    if not out:
        raise ListingError(f"{d.name}: manifest lists no digital files to sell")
    return out


def _positive_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def _price(manifest: dict, shop: dict) -> float:
    price = manifest.get("price_usd")
    if not _positive_number(price):
        price = (shop.get("default_price_usd") or {}).get(manifest.get("niche"))
    if not _positive_number(price):
        raise ListingError(f"no price: manifest price_usd and shop default_price_usd.{manifest.get('niche')} are unset")
    return round(float(price), 2)


def _quantity(cfg: dict) -> int:
    q = cfg["quantity"]
    if not isinstance(q, int) or isinstance(q, bool) or q < 1:
        raise ListingError(f"listing.quantity must be a positive integer, not {q!r}")
    return q


def _taxonomy_id(cfg: dict, niche) -> int | None:
    value = (cfg.get("taxonomy_ids") or {}).get(niche)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ListingError(f"listing.taxonomy_ids.{niche} must be a positive integer, not {value!r}")
    return value


__all__ = ["DEFAULTS", "ListingComplianceError", "ListingError", "ListingValidationError", "default_client",
           "run_all", "write_listing"]
