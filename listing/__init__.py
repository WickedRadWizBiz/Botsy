"""Listing stage: title, 13 tags, description and alt text for one QA-passed item.

    write_listing(item_dir, *, client=None, dry_run=False, settings=None, review_dir=None) -> dict
    run_all(out_root=None, *, client=None, dry_run=False, settings=None, review_dir=None) -> list[dict]
    needs_listing(item_dir, *, dry_run=False, settings=None) -> bool

Writes <item>/listing.json (docs/CONTRACTS.md section 4) and sets the manifest status to
"listed". A dry run on a real item (manifest generator.dry_run false) only previews, like
the publisher: it builds and checks the template copy and returns it with `preview: true`
but writes nothing, so template text can never take the place of a real listing.

The text model sees only our own product facts (listing/facts.py), never a
competitor's title, description or image. Its JSON draft, or in a dry run a template
draft, always goes through the same Etsy rule check (listing/rules.py); a model draft that
fails is sent back with the problems up to `max_retries` times, then repaired in code.

Compliance gates, in order: our own item data is checked against the trademark blocklist
before anything is sent; a blocked term in the data, a model draft or the final listing
raises ListingComplianceError and no listing.json is written; a `?term` sets
needs_review and is added to the review queue. The AI disclosure from
config/ai_disclosure.txt ends every description when the item is AI-assisted, and by
default on every listing (`always_disclose_ai`), because the listing copy itself is
written with AI. Medical or therapeutic wording is a rule problem (fixed like any other)
and, in our fixed description sections, refuses the listing.

Game systems (CLAUDE.md exception, docs/PACKS.md section 4): only a `ttrpg` item whose
manifest `system` is a system other than the default (config/ttrpg_systems.yaml, through
core.systems) may name it, as a compatibility reference ("... for D&D 5e"), and only in
the title, tags and description: those are checked with Blocklist.check(...,
allow_systems=(system,)). Alt text, item data and every other niche are checked with no
system allowed, and core.compliance.system_mentions() also refuses another system's
names in every spelling the registry lists (also joined ones such as "DnD5e"). Such a listing always
carries the system's "unofficial, not affiliated with <owner>" disclaimer, which must
name the owner and itself pass the same check. The owner ("Wizards of the Coast") is named
in the description only: in a title or tag it would read as an official product, so it
is a rule problem there (repaired like alt text).

Settings may add medical_terms and misleading_phrases but never remove the built-in ones
(listing_config).
"""
from __future__ import annotations

import logging
from pathlib import Path

from core import etsy
from core.compliance import (Blocklist, find_urls, has_ai_disclosure, iter_strings, load_ai_disclosure,
                             system_mentions)
from core.config import (PLACEHOLDER_SHOP_NAMES, get_shop, is_placeholder_shop_name, load_settings, require_env,
                         section, today)
from core.items import (iter_item_dirs, output_root, read_json, read_manifest, review_dir_for, set_status,
                        write_json)
from listing import model, review, rules, template
from listing.errors import ListingComplianceError, ListingError, ListingValidationError
from listing.facts import absent_types, gather, safe_rel

log = logging.getLogger("listing")

DEFAULTS = {
    "title_max_chars": etsy.TITLE_MAX_CHARS,   # Etsy Help Center (core/etsy.py); settings listing.title_max_chars
    "tags_count": etsy.TAGS_MAX,
    "tag_max_chars": etsy.TAG_MAX_CHARS,
    "alt_text_max_chars": etsy.ALT_TEXT_MAX_CHARS,  # Open API v3 uploadListingImage: "Max length 500 characters."
    "hook_max_chars": 300,
    "about_max_chars": 900,
    "suggested_uses": {"min": 3, "max": 6, "max_chars": 160},
    # The copy is AI-written even when the designs are not (procedural activity sheets),
    # so the disclosure goes on every listing unless this is switched off.
    "always_disclose_ai": True,
    "quantity": 999,
    # Etsy seller taxonomy id per niche. createDraftListing requires one; null until the
    # user picks them (Etsy's getSellerTaxonomyNodes lists the ids).
    "taxonomy_ids": {"stickers": None, "coloring_pages": None, "activity_sheets": None, "stationery": None,
                     "ttrpg": None},
    "max_retries": 2,               # re-asks after the first model draft
    "effort": "low",                # Opus 5.5 always thinks; thinking counts toward max_tokens
    "refusal_fallback": True,       # server-side fallback model if the request is declined
    "max_subjects": 12,             # design subjects passed to the model as facts
    "terms": "For personal use only. Please do not share, resell or redistribute the files or printed copies.",
    # "hand made" also matches "handmade" and "hand-made" (core.compliance term matching).
    "misleading_phrases": ["hand drawn", "hand illustrated", "hand painted", "drawn by hand", "painted by hand",
                           "hand made", "made by hand", "no ai", "ai free", "not ai", "commercial use",
                           "commercial license", "resale rights",
                           # never implying an official product (CLAUDE.md, ttrpg exception)
                           "official", "officially", "licensed product", "officially licensed", "endorsed by"],
    # No medical or therapeutic claims (CLAUDE.md; docs/PACKS.md section 4), with their word
    # forms. Matched as whole words; a plural or possessive "s" is matched too. At least the
    # words research cuts from themes and keywords (research/defaults.py medical_terms).
    # Settings can add words and misleading phrases, never remove these (listing_config).
    "medical_terms": ["treat", "treated", "treating", "treatment", "cure", "cured", "curing", "heal", "healed",
                      "healing", "diagnose", "diagnosed", "diagnoses", "diagnosing", "diagnosis", "diagnostic",
                      "therapy", "therapies", "therapist", "therapeutic", "therapeutics", "therapeutically",
                      "clinical", "clinically", "medical", "medically", "medication", "medicine", "prescription",
                      "prescribed", "symptom relief", "symptoms relief", "relieves symptoms", "relieve symptoms",
                      "anxiety relief", "pain relief"],
    # Ordinary phrases with those words that make no claim (themes such as "sweet treats").
    "medical_ok_phrases": ["trick or treat", "trick or treating", "trick or treaters", "sweet treat",
                           "treat bag", "treat box", "treat jar", "holiday treat"],
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
        "stationery": [
            "cover photo: planner pages fanned out next to a card with the title, page count and file details",
            "a grid of pages from the pack",
            "a close-up of one page with a magnified detail",
            "a 'what you get' card listing what is included, next to two sample pages",
            "the included page sizes shown side by side at true scale",
            "printed planner pages on a wooden desk",
        ],
        "ttrpg": [
            "cover photo: game sheets fanned out next to a card with the title, page count and file details",
            "a grid of sheets from the pack",
            "a close-up of one sheet with a magnified detail",
            "a 'what you get' card listing what is included, next to two sample sheets",
            "the included page sizes shown side by side at true scale",
            "printed game sheets on a wooden desk",
        ],
    },
}

LISTABLE_STATUSES = ("qa_passed", "listed")


def write_listing(item_dir: str | Path, *, client=None, dry_run: bool = False, settings: dict | None = None,
                  review_dir: str | Path | None = None) -> dict:
    """Write listing.json for one QA-passed item and return it. Raises ListingError (or
    ListingComplianceError) when the item cannot be listed; nothing is written then.

    A dry run on a real item is a preview: the listing is returned (with `preview: true`)
    but listing.json, the status and the review queue are left alone.

    On a rerun that hits a blocked term (say the blocklist grew), the earlier listing.json
    is moved aside and the item goes back to qa_passed, so no stale copy stays ready for
    the publisher. This also happens in a preview: a refusal only ever tightens."""
    d = Path(item_dir)
    try:
        return _write_listing(d, client=client, dry_run=dry_run, settings=settings or load_settings(),
                              review_dir=review_dir)
    except ListingComplianceError:
        _withdraw_stale(d)
        raise


# Lists a settings file may extend but never shorten: the rules they hold are CLAUDE.md
# compliance rules (no medical claims, no misleading claims).
STRICT_LISTS = ("medical_terms", "misleading_phrases")


def listing_config(settings: dict) -> dict:
    """settings `listing:` over DEFAULTS, where medical_terms and misleading_phrases keep
    every built-in entry (a setting adds words, never removes one), and medical_ok_phrases,
    the exemptions from the medical-claims rule, can only shrink (a setting may drop a
    built-in phrase, never add one that would let a claim through)."""
    cfg = section(settings, "listing", DEFAULTS)
    for key in STRICT_LISTS:
        extra = cfg.get(key) if isinstance(cfg.get(key), (list, tuple)) else []
        cfg[key] = list(dict.fromkeys([*DEFAULTS[key], *(str(x) for x in extra if str(x).strip())]))
    given = cfg.get("medical_ok_phrases")
    keep = ({str(x).strip().casefold() for x in given} if isinstance(given, (list, tuple))
            else {p.casefold() for p in DEFAULTS["medical_ok_phrases"]})
    cfg["medical_ok_phrases"] = [p for p in DEFAULTS["medical_ok_phrases"] if p.casefold() in keep]
    return cfg


def _write_listing(d: Path, *, client, dry_run: bool, settings: dict, review_dir) -> dict:
    cfg = listing_config(settings)
    comp = settings.get("compliance") or {}
    manifest, report = _load_item(d)
    item_id = d.name
    preview = bool(dry_run) and not _generated_dry(manifest)
    try:
        shop = get_shop(settings, manifest.get("shop_id"))
    except KeyError as e:
        raise ListingError(f"{item_id}: {e}") from None
    system = _game_system(manifest, settings, item_id)
    blocklist = Blocklist.load(comp.get("trademark_blocklist"), catalog=system["catalog"] if system else None)
    allow = (system["id"],) if system else ()
    # Settle everything that can fail cheaply before any API call is spent.
    price, quantity = _price(manifest, shop), _quantity(cfg)
    taxonomy_id = _taxonomy_id(cfg, manifest.get("niche"))
    images = _image_paths(d, report)
    files = _file_paths(d, manifest)
    facts = gather(manifest, images, cfg, settings, game_system=system)
    own = {k: v for k, v in facts.items() if k not in ("game_system", "game_system_names")}
    data_check = blocklist.check(*iter_strings(own))  # our item data never names a system
    if system:
        data_check = data_check.merge(blocklist.check(facts["game_system"], *facts["game_system_names"],
                                                      system["disclaimer"], allow_systems=allow))
        if not blocklist.system_names(system["id"]):
            raise ListingError(f"{item_id}: no game-system (!) term in the blocklist belongs to {system['id']!r}; "
                               "check config/trademark_blocklist.txt and config/ttrpg_systems.yaml")
    if data_check.blocked:  # never send a trademark to the model or into a listing
        raise ListingComplianceError(f"{item_id}: item data has blocked terms: {', '.join(data_check.blocked)}",
                                     data_check.blocked)
    if find_urls(facts):
        raise ListingComplianceError(f"{item_id}: item data contains URLs: {find_urls(facts)}")
    # The theme, keywords, audience and page types reach our fixed description sections, so
    # medical wording there cannot be repaired (subjects only reach repairable fields).
    fixed_data = [facts["theme"], facts["audience"], *facts["keywords"], *(facts.get("page_types") or {})]
    medical = sorted({w for t in fixed_data for w in rules.medical(t, cfg)})
    if medical:  # checked before any API call
        raise ListingValidationError(f"{item_id}: item data has medical wording ({', '.join(medical)}); "
                                     "listings never make medical or therapeutic claims")
    # A ttrpg listing names its system in title, tags and description only, never in alt text,
    # and its trademark owner only in the description (the disclaimer), never in title or tags.
    alt_names = _system_finder(blocklist, system, settings) if system else None
    owner_names = _owner_finder(system) if system else None
    # A mixed pack's title names only page types the pack includes (stationery, ttrpg).
    absent = (lambda text: absent_types(text, facts)) if facts.get("page_types") else None

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
            # settings and item_id: the spend ledger names the item, and the cap and ledger
            # path come from the settings this stage was given (core.spend).
            result = model.write(client or default_client(), facts, cfg, model=text_cfg["model"],
                                 max_tokens=int(text_cfg.get("max_tokens") or 2000), blocklist=blocklist,
                                 settings=settings, item_id=item_id, allow_systems=allow, alt_names=alt_names,
                                 owner_names=owner_names, absent_types=absent)
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
    names = {"alt_names": alt_names, "owner_names": owner_names, "absent_types": absent}
    problems = rules.validate(draft, n_images, cfg, **names)
    if problems:
        writer["repaired"] = problems
        draft = rules.repair(draft, fallback, template.tag_candidates(facts, cfg), n_images, cfg, **names)
        left = rules.validate(draft, n_images, cfg, **names)
        if left:
            raise ListingValidationError(f"{item_id}: listing still breaks Etsy rules after repair: {'; '.join(left)}")

    disclose = bool(manifest.get("ai_assisted")) or bool(cfg["always_disclose_ai"])
    disclosure = load_ai_disclosure(shop["name"], comp.get("ai_disclosure")) if disclose else None
    description = template.description(draft, facts, cfg, disclosure, system["disclaimer"] if system else None)
    included = has_ai_disclosure(description, shop["name"], comp.get("ai_disclosure"))
    if disclose and not included:
        raise ListingError(f"{item_id}: AI disclosure missing from the description")
    fixed = sorted({w for line in description.splitlines() for w in rules.medical(line, cfg)})
    if fixed:  # theme or page wording in our own fixed sections: no repair can fix that
        raise ListingValidationError(f"{item_id}: the description has medical wording ({', '.join(fixed)}); "
                                     "listings never make medical or therapeutic claims")

    # Description checked line by line: its lines are our fixed sections and single
    # model fields, and a term should not be read across a line break between them.
    # The item's game system may be named in title, tags and description; never in alt text.
    copy_texts = [draft["title"], *draft["tags"], *description.splitlines()]
    final_texts = [*copy_texts, *draft["alt_texts"]]
    found = blocklist.check(*copy_texts, allow_systems=allow).merge(blocklist.check(*draft["alt_texts"]))
    if found.blocked:
        raise ListingComplianceError(f"{item_id}: listing has blocked terms: {', '.join(found.blocked)}", found.blocked)
    if find_urls(final_texts):
        raise ListingComplianceError(f"{item_id}: listing contains URLs: {find_urls(final_texts)}")
    others = _other_systems(copy_texts, draft["alt_texts"], system, settings)
    if others:
        raise ListingComplianceError(f"{item_id}: listing names game systems it may not name: {', '.join(others)}",
                                     others)

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
    if is_placeholder_shop_name(shop):
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
    if preview:  # dry run on a real item: never replace its listing with template text
        listing["preview"] = True
        log.info("previewed %s (dry run on a real item; nothing written): %s", item_id, listing["title"])
        return listing
    if listing_reasons:  # queue first: a listing must never exist without its review flag
        rdir = Path(review_dir) if review_dir else review_dir_for(d, settings)
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
    """List every item that needs_listing(). One result per item: {item_id, ok, listing,
    error}. Items rejected in the review queue are skipped (no API call spent on them).
    Raises MissingSecretError up front when a real run has no ANTHROPIC_API_KEY."""
    settings = settings or load_settings()
    root = Path(out_root) if out_root else output_root(settings)
    results = []
    for d in iter_item_dirs(root):
        if not needs_listing(d, dry_run=dry_run, settings=settings):
            continue
        rdir = Path(review_dir) if review_dir else review_dir_for(d, settings)
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


def needs_listing(item_dir: str | Path, *, dry_run: bool = False, settings: dict | None = None) -> bool:
    """True when the listing stage should (re)write this item: every qa_passed item and, in
    a real run, a 'listed' real item whose listing.json the publisher would refuse as stale:
    - it still holds dry-run template text (dry_run or writer.dry_run), or
    - it no longer fits the current settings: the description lacks the AI disclosure for
      the current shop name while one is required (the same rule as the publisher's gate),
      or any text shows the placeholder shop name. Only once the shop name is set, since
      re-listing before that would write the placeholder again.
    Without this such items would be stuck at 'listed' in pipeline runs."""
    d = Path(item_dir)
    try:
        manifest = read_manifest(d)
        status = manifest.get("status")
    except (OSError, ValueError, AttributeError):
        return False
    if status == "qa_passed":
        return True
    if status != "listed" or dry_run or _generated_dry(manifest):
        return False
    try:
        lst = read_json(d / "listing.json")
        if lst.get("dry_run") or (lst.get("writer") or {}).get("dry_run"):
            return True
        return _stale_copy(manifest, lst, settings or load_settings())
    except (OSError, ValueError, AttributeError, TypeError):
        return False  # unreadable or missing: the publisher reports it


def _stale_copy(manifest: dict, lst: dict, settings: dict) -> bool:
    try:
        shop = get_shop(settings, lst.get("shop_id") or manifest.get("shop_id"))
    except KeyError:
        return False  # the publisher reports the unknown shop
    if is_placeholder_shop_name(shop):
        return False
    texts = [str(lst.get("title") or ""), str(lst.get("description") or ""),
             *map(str, lst.get("tags") or []), *map(str, lst.get("alt_texts") or [])]
    if any(p and p.casefold() in t.casefold() for p in PLACEHOLDER_SHOP_NAMES for t in texts):
        return True
    cfg = listing_config(settings)
    writer = lst.get("writer") or {}
    must_disclose = bool(manifest.get("ai_assisted", True) or lst.get("ai_disclosure_included")
                         or writer.get("kind") == "model" or lst.get("model") or cfg["always_disclose_ai"])
    comp = settings.get("compliance") or {}
    return must_disclose and not has_ai_disclosure(str(lst.get("description") or ""), str(shop.get("name")).strip(),
                                                   comp.get("ai_disclosure"))


def _game_system(manifest: dict, settings: dict, item_id: str) -> dict | None:
    """{id, label, names, disclaimer, catalog} when this listing may name a game system: a
    ttrpg item whose manifest `system` is a system from config/ttrpg_systems.yaml other
    than the default. None otherwise (every other niche, and system-neutral sheets).
    Raises ListingError when the system cannot be read or its disclaimer is unusable; the
    item then stays qa_passed until the file is fixed."""
    if manifest.get("niche") != "ttrpg":
        return None
    sid = manifest.get("system")
    if sid in (None, "", "generic"):
        return None
    try:
        from core import systems  # lazy: only ttrpg listings need the registry

        if sid == systems.default_system(settings):
            return None
        catalog = systems.load_systems(settings)
        entry = catalog[sid]
        names = list(systems.names_for(sid, settings))
        label = systems.label_for(sid, settings) if hasattr(systems, "label_for") else (names or [sid])[0]
        disclaimer = " ".join(str(systems.disclaimer_for(sid, settings) or "").split())
    except Exception as e:  # unknown id, missing or broken file
        raise ListingError(f"{item_id}: game system {sid!r} cannot be read from config/ttrpg_systems.yaml "
                           f"({type(e).__name__}: {e})") from None
    low = disclaimer.lower()
    if not disclaimer or "unofficial" not in low or "not affiliated" not in low:
        raise ListingError(f"{item_id}: game system {sid!r} has no 'unofficial, not affiliated with <owner>' "
                           "disclaimer in config/ttrpg_systems.yaml; a listing that names it needs one")
    owners = list(dict.fromkeys(str(o) for o in [entry.get("owner"), *(entry.get("owner_names") or [])]
                                if isinstance(o, str) and o.strip()))
    if not owners or not Blocklist([], [], {o: sid for o in owners}).check(disclaimer).blocked:
        raise ListingError(f"{item_id}: game system {sid!r} needs its trademark owner in config/ttrpg_systems.yaml "
                           "and named in its disclaimer ('not affiliated with <owner>')")
    return {"id": str(entry.get("id") or sid), "label": str(label), "names": names, "disclaimer": disclaimer,
            "owners": owners, "catalog": catalog}


def _system_finder(blocklist: Blocklist, system: dict, settings: dict):
    """text -> the item's game-system names in it: its blocklist `!` terms, plus the label
    when another spelling of the system is found ("DnD5e", core.compliance.system_mentions)."""
    allow = (system["id"],)

    def names(text: str) -> list[str]:
        found = blocklist.check(text, allow_systems=allow).allowed
        if not found and system["id"] in system_mentions([text], system["catalog"]):
            found = [system["label"]]
        return found

    return names


def _owner_finder(system: dict):
    """text -> the spellings of the system's trademark owner in it (config/ttrpg_systems.yaml
    owner and owner_names), with the blocklist's word rules."""
    owners = Blocklist([], [], {name: system["id"] for name in system.get("owners") or []})

    def names(text: str) -> list[str]:
        return owners.check(text).blocked if text else []

    return names


def _other_systems(copy_texts: list[str], alt_texts: list[str], system: dict | None, settings: dict) -> list[str]:
    """Game systems named where they may not be, in any spelling config/ttrpg_systems.yaml
    lists (core.compliance.system_mentions, also joined ones such as "DnD5e"): any system in
    alt text or in another niche's copy, another system in a ttrpg listing's copy. [] when
    the registry cannot be read (the blocklist check has already run)."""
    catalog = system["catalog"] if system else None
    named = set(system_mentions(copy_texts, catalog)) - ({system["id"]} if system else set())
    named |= set(system_mentions(alt_texts, catalog))
    return sorted(named)


def default_client():
    """Anthropic client from .env; only built when not a dry run. Raises MissingSecretError
    when ANTHROPIC_API_KEY is not set (the SDK itself would only fail at the first request)."""
    import anthropic

    return anthropic.Anthropic(api_key=require_env("ANTHROPIC_API_KEY"))


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


def _generated_dry(manifest: dict) -> bool:
    """The item was generated in dry-run mode (placeholder content). A missing flag counts
    as real, so a dry run on it only previews."""
    return bool((manifest.get("generator") or {}).get("dry_run"))


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
           "needs_listing", "run_all", "write_listing"]
