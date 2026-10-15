"""publish_draft(): one listed item -> an Etsy DRAFT with photos and digital files.

Gates run before anything is sent (PublishGateError lists every reason):
QA passed; listing.json present, not written in dry-run mode, blocklist-clean (title,
tags, description, alt texts and the photo and file names) and carrying the AI
disclosure when the item is AI-assisted; items needing review are `approved` in the
review queue (a `rejected` entry always blocks); taxonomy_id set; photos and files
present inside the item folder and within Etsy's limits; title and tags within Etsy's
character rules; price and quantity within Etsy's range; the client is for the item's shop.

Idempotency (docs/CONTRACTS.md section 5): publish.json is written as soon as the draft
exists and after every upload, so a rerun resumes the uploads on the same draft instead
of creating a second one. It also keeps a digest of what the draft was created from; if
listing.json, the photos or the files change before the draft is finished, a rerun stops
instead of adding new photos to a draft whose text was checked against the old listing.
The publisher never sends state=active; the user approves and activates each draft in Etsy.

Dry runs use FakeEtsyClient. On items generated in dry-run mode they write publish.json
(dry_run: true) and set the status to "drafted", completing the simulated pipeline. On
real items a dry run only previews: it runs the gates and the fake requests and writes
nothing, so it can never block or fake a real draft.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from core.compliance import Blocklist, has_ai_disclosure
from core.config import get_shop, load_settings, resolve, section, today
from core.items import (
    iter_item_dirs,
    output_root,
    read_json,
    read_manifest,
    set_status,
    write_json,
)
from publisher.client import EtsyAPIError, EtsyClient
from publisher.defaults import load_cfg
from publisher.fake import DRY_RUN_TAXONOMY_ID, FakeEtsyClient

log = logging.getLogger("publisher")

PUBLISH_FILE = "publish.json"
SCHEMA = "publish/v1"
WHO_MADE = ("i_did", "someone_else", "collective")  # Open API v3 spec enums
WHEN_MADE = ("made_to_order", "2020_2026", "2010_2019", "2007_2009", "before_2007", "2000_2006",
             "1990s", "1980s", "1970s", "1960s", "1950s", "1940s", "1930s", "1920s", "1910s",
             "1900s", "1800s", "1700s", "before_1700")
MARKS = set("™©®")
PLACEHOLDER_SHOP_NAMES = ("", "YOUR_SHOP_NAME")  # config/settings.yaml ships with the placeholder
ITEM_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class PublishGateError(RuntimeError):
    """The item is not ready, or not allowed, to become a draft. Nothing was sent."""


class PublishError(RuntimeError):
    """The draft on Etsy is not in a state this tool may continue with."""


@dataclass
class Plan:
    item_id: str
    shop_key: str
    fields: dict
    images: list[tuple[str, str | None]] = field(default_factory=list)  # (path, alt text)
    files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    digest: str = ""  # sha256 of fields, photos, alt texts and files (see _digest)


# --- gates ------------------------------------------------------------------------

def _title_problems(title: str) -> list[str]:
    """Etsy: letters, numbers, punctuation, math symbols, spaces, ™©®; %, :, & and + once each."""
    bad = sorted({c for c in title if c not in MARKS and unicodedata.category(c)[0] not in "LP"
                  and unicodedata.category(c) not in ("Nd", "Sm", "Zs")})
    out = [f"title has characters Etsy rejects: {''.join(bad)!r}"] if bad else []
    out += [f"title uses {c!r} more than once" for c in "%:&+" if title.count(c) > 1]
    return out


def _tag_problems(tag: str) -> list[str]:
    """Etsy: letters, numbers, spaces, -, ' and ™©® only (so no commas)."""
    bad = sorted({c for c in tag if c not in MARKS and c not in "-'" and unicodedata.category(c)[0] != "L"
                  and unicodedata.category(c) not in ("Nd", "Zs")})
    return [f"tag {tag!r} has characters Etsy rejects: {''.join(bad)!r}"] if bad else []


def _inside(item_dir: Path, rel: str) -> Path | None:
    """Resolve a listing path; None if it escapes the item folder."""
    p = (item_dir / rel).resolve()
    return p if p.is_relative_to(item_dir.resolve()) else None


def default_review_dir(item_dir: Path, settings: dict) -> Path:
    """Same rule as the QA stage: settings paths.review_dir for items in the configured
    output dir, otherwise a review/ folder next to the item (dry runs, tests)."""
    if item_dir.resolve().parent == output_root(settings).resolve():
        return resolve(settings["paths"]["review_dir"])
    return item_dir.parent / "review"


def _load(path: Path, item_id: str) -> dict:
    """Read an item JSON file; a broken one is this item's problem, not the whole run's."""
    try:
        data = read_json(path)
    except (OSError, ValueError) as e:
        raise PublishGateError(f"{item_id}: {path.name} is unreadable ({type(e).__name__}: {e})") from e
    if not isinstance(data, dict):
        raise PublishGateError(f"{item_id}: {path.name} is not a JSON object")
    return data


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _digest(item_dir: Path, fields: dict, images: list[tuple[str, str | None]], files: list[str]) -> str:
    """What the draft is made of. A resumed draft must still match it."""
    blob = {"fields": fields,
            "images": [(rel, alt, _sha256(item_dir / rel)) for rel, alt in images],
            "files": [(rel, _sha256(item_dir / rel)) for rel in files]}
    return hashlib.sha256(json.dumps(blob, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _form(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return str(value)


def check_ready(item_dir: str | Path, *, settings: dict | None = None,
                review_dir: str | Path | None = None, dry_run: bool = False) -> Plan:
    """Run every gate and build the createDraftListing fields. Raises PublishGateError.

    Real items get every gate in every mode, so `publish --dry-run` answers "would this
    draft live?". Only a simulated item (dry-run item in a dry run) may lack a taxonomy id
    or a real shop name, so the pipeline's dry run still exercises create and upload."""
    item_dir = Path(item_dir)
    settings = settings or load_settings()
    cfg = load_cfg(settings)
    manifest = _load(item_dir / "manifest.json", item_dir.name)
    item_id = str(manifest.get("item_id") or item_dir.name)
    if not ITEM_ID_RE.match(item_id) or ".." in item_id:  # it names the review entry file
        raise PublishGateError(f"invalid item id {item_id!r} in {item_dir / 'manifest.json'}")
    generated_dry = bool((manifest.get("generator") or {}).get("dry_run"))
    simulated = dry_run and generated_dry
    problems: list[str] = []
    warnings: list[str] = []

    if manifest.get("status") not in ("listed", "drafted"):
        problems.append(f"status is {manifest.get('status')!r}; run QA and the listing stage first")
    if generated_dry and not dry_run:
        problems.append("item was generated in dry-run mode (placeholder content); never drafted for real")

    qa_path = item_dir / "qa_report.json"
    qa_report = _load(qa_path, item_id) if qa_path.exists() else None
    if not qa_report or qa_report.get("passed") is not True:
        problems.append("qa_report.json missing or QA did not pass")

    listing_path = item_dir / "listing.json"
    if not listing_path.exists():
        raise PublishGateError(f"{item_id}: listing.json missing; run the listing stage. "
                               + "; ".join(problems))
    listing = _load(listing_path, item_id)
    # The listing stage records its dry-run mode under writer.dry_run.
    if (listing.get("dry_run") or (listing.get("writer") or {}).get("dry_run")) and not simulated:
        problems.append("listing.json was written in dry-run mode (template text)")
    shop_key = listing.get("shop_id") or manifest.get("shop_id")
    if manifest.get("shop_id") and shop_key != manifest["shop_id"]:
        problems.append(f"listing shop {shop_key!r} differs from manifest shop {manifest['shop_id']!r}")
    try:
        shop = get_shop(settings, shop_key)
    except KeyError as e:
        raise PublishGateError(f"{item_id}: {e.args[0]}") from None
    if shop.get("name", "").strip() in PLACEHOLDER_SHOP_NAMES and not simulated:
        problems.append("shop name in config/settings.yaml is still the placeholder (the AI "
                        "disclosure would show it); set it and rerun the listing stage")

    title = (listing.get("title") or "").strip()
    description = (listing.get("description") or "").strip()
    tags = [str(t).strip() for t in listing.get("tags") or []]
    alt_texts = list(listing.get("alt_texts") or [])

    images = [str(x) for x in listing.get("image_paths") or []]
    files = [str(x) for x in listing.get("file_paths") or []]

    comp = section(settings, "compliance", {})
    blocklist = Blocklist.load(comp.get("trademark_blocklist"))
    # File names are sent to Etsy too; buyers see the download's name.
    found = blocklist.check_obj({"title": title, "tags": tags, "description": description,
                                 "alt_texts": alt_texts,
                                 "file_names": [Path(rel).name for rel in images + files]})
    blocked = sorted(set(found.blocked) | set((listing.get("compliance") or {}).get("blocked") or []))
    if blocked:
        problems.append(f"blocked trademark terms: {', '.join(blocked)}")
    if manifest.get("ai_assisted", True) and not has_ai_disclosure(
            description, shop.get("name", ""), comp.get("ai_disclosure")):
        problems.append("AI-assisted item but the description lacks the text of config/ai_disclosure.txt")

    review_dir = Path(review_dir) if review_dir else default_review_dir(item_dir, settings)
    entry_path = review_dir / f"{item_id}.json"
    entry = _load(entry_path, item_id) if entry_path.exists() else {}
    review_status = entry.get("status")
    needs_review = bool(manifest.get("needs_review") or listing.get("needs_review")
                        or (qa_report or {}).get("needs_review") or entry.get("needs_review")
                        or found.flagged or (listing.get("compliance") or {}).get("flagged"))
    if review_status == "rejected":
        problems.append("rejected in the review queue")
    elif needs_review and review_status != "approved":
        problems.append(f"needs review (review status: {review_status or 'no entry'}); approve with "
                        f"`python -m qa review approve {item_id}`")

    taxonomy_id = listing.get("taxonomy_id") or \
        (section(settings, "listing", {}).get("taxonomy_ids") or {}).get(manifest.get("niche"))
    if not str(taxonomy_id or "").isdigit() or int(taxonomy_id) <= 0:
        msg = (f"no taxonomy_id (set listing.taxonomy_ids.{manifest.get('niche')} in "
               "config/settings.yaml; `python -m publisher taxonomy --search ...` lists ids)")
        if simulated:
            warnings.append(msg + f"; dry run used fake id {DRY_RUN_TAXONOMY_ID}")
            taxonomy_id = DRY_RUN_TAXONOMY_ID
        else:
            problems.append(msg)

    if listing.get("type", "download") != "download":
        problems.append(f"type is {listing.get('type')!r}; this shop only sells digital downloads")
    if not title or len(title) > 140:
        problems.append(f"title must be 1-140 characters (has {len(title)})")
    problems += _title_problems(title)
    if not description:
        problems.append("description is empty")
    if len(tags) > 13 or len({t.lower() for t in tags}) != len(tags):
        problems.append("tags must be at most 13 and unique")
    for t in tags:
        if not t or len(t) > 20:
            problems.append(f"tag {t!r} must be 1-20 characters")
        problems += _tag_problems(t)
    try:
        raw_price = listing.get("price_usd")
        price = float(raw_price if raw_price is not None else manifest.get("price_usd") or 0)
        quantity = int(listing.get("quantity") or 0)
    except (TypeError, ValueError):
        price, quantity = 0.0, 0
    lo, hi = float(cfg["min_price_usd"]), float(cfg["max_price_usd"])
    if not (math.isfinite(price) and lo <= round(price, 2) <= hi):
        problems.append(f"price_usd {price!r} must be between {lo:.2f} and {hi:,.0f} (Etsy's range)")
    if not 1 <= quantity <= int(cfg["max_quantity"]):
        problems.append(f"quantity {quantity} must be 1-{cfg['max_quantity']} (Etsy's limit)")
    who_made, when_made = listing.get("who_made", "i_did"), listing.get("when_made", "made_to_order")
    if who_made not in WHO_MADE or when_made not in WHEN_MADE:
        problems.append(f"who_made/when_made {who_made!r}/{when_made!r} are not Etsy values")

    if not 1 <= len(images) <= cfg["max_images"]:
        problems.append(f"needs 1-{cfg['max_images']} listing photos (has {len(images)})")
    if not 1 <= len(files) <= cfg["max_files"]:
        problems.append(f"needs 1-{cfg['max_files']} digital files (has {len(files)}; Etsy's limit)")
    for rel in images + files:
        p = _inside(item_dir, rel)
        if p is None:
            problems.append(f"{rel} is outside the item folder")
        elif not p.is_file() or p.stat().st_size == 0:
            problems.append(f"{rel} is missing or empty")
        elif rel in files and p.stat().st_size > cfg["max_file_bytes"]:
            problems.append(f"{rel} is {p.stat().st_size:,} bytes; Etsy allows {cfg['max_file_bytes']:,}")

    if problems:
        raise PublishGateError(f"{item_id}: " + "; ".join(problems))

    if len(alt_texts) != len(images):
        warnings.append(f"{len(alt_texts)} alt texts for {len(images)} photos")
    for w in warnings:
        log.warning("%s: %s", item_id, w)
    max_alt = int(cfg["alt_text_max_chars"])
    alts = [(str(a).strip()[:max_alt] or None) if a else None
            for a in (alt_texts + [None] * len(images))[:len(images)]]
    fields = {
        "quantity": str(quantity), "title": title, "description": description,
        "price": f"{price:.2f}", "who_made": who_made, "when_made": when_made,
        "is_supply": "false", "taxonomy_id": str(int(taxonomy_id)), "type": "download",
    }
    if tags:
        fields["tags"] = ",".join(tags)  # spec: "a comma-separated list of tag strings"
    for k, v in (cfg.get("extra_fields") or {}).items():
        fields.setdefault(str(k), _form(v))  # cannot override the fields above
    photos = list(zip(images, alts, strict=True))
    return Plan(item_id, shop["id"], fields, photos, files, warnings,
                _digest(item_dir, fields, photos, files))


# --- publishing -------------------------------------------------------------------

def _read_record(item_dir: Path) -> dict | None:
    """publish.json, or None. A broken one is never treated as missing: that would create
    a second draft."""
    p = item_dir / PUBLISH_FILE
    if not p.exists():
        return None
    try:
        rec = read_json(p)
    except (OSError, ValueError) as e:
        raise PublishError(f"{item_dir.name}: {PUBLISH_FILE} is unreadable ({e}); fix it or check the "
                           "draft in Etsy and remove the file by hand") from e
    if not isinstance(rec, dict):
        raise PublishError(f"{item_dir.name}: {PUBLISH_FILE} is not a JSON object")
    return rec


def _check_shop(client: EtsyClient, cfg: dict) -> None:
    """Prices in listing.json are USD; Etsy reads `price` in the shop's currency."""
    if client.shop_info is None:
        client.shop_info = client.get_shop() or {}
    currency = client.shop_info.get("currency_code")
    if currency and currency != cfg["currency"]:
        raise PublishGateError(f"shop currency is {currency}, listing prices are {cfg['currency']}; "
                               "convert prices before drafting")


def publish_draft(item_dir: str | Path, *, client: EtsyClient | None = None, dry_run: bool = False,
                  settings: dict | None = None, review_dir: str | Path | None = None) -> dict:
    """Create (or finish) the Etsy draft for one item. Returns the publish.json record."""
    item_dir = Path(item_dir)
    settings = settings or load_settings()
    cfg = load_cfg(settings)
    manifest = _load(item_dir / "manifest.json", item_dir.name)
    persist = not dry_run or bool((manifest.get("generator") or {}).get("dry_run"))

    existing = _read_record(item_dir)
    if existing and not existing.get("dry_run"):
        record = existing          # a real draft exists: never create another one
    elif existing and dry_run:
        record = existing          # resume a dry-run record
    else:
        record = None              # a stale dry-run record never blocks a real draft
    if record and record.get("complete"):
        if persist and manifest.get("status") != "drafted":
            set_status(item_dir, "drafted")
        return record
    if record and not persist:  # dry-run preview of a real half-done draft: work on a copy
        record = {**copy.deepcopy(record), "preview": True}

    plan = check_ready(item_dir, settings=settings, review_dir=review_dir, dry_run=dry_run)
    if client is None:
        client = (FakeEtsyClient(settings=settings, shop_id=(record or {}).get("etsy_shop_id"),
                                 shop_key=plan.shop_key)
                  if dry_run else EtsyClient(settings=settings, shop_key=plan.shop_key))
    if getattr(client, "shop_key", plan.shop_key) != plan.shop_key:
        raise PublishGateError(f"{plan.item_id}: item belongs to shop {plan.shop_key!r}, the client is "
                               f"for shop {client.shop_key!r}")
    if record and str(record.get("etsy_shop_id")) != str(client.shop_id):
        raise PublishError(f"{plan.item_id}: publish.json is for shop {record.get('etsy_shop_id')}, "
                           f"configured shop is {client.shop_id}")
    _check_shop(client, cfg)

    def save() -> None:
        record["updated"] = today()
        if persist:
            write_json(item_dir / PUBLISH_FILE, record)

    discard_hint = f"run `python -m publisher discard --item {plan.item_id}` to start over"
    if record is None:
        try:
            resp = client.create_draft_listing(plan.fields)
        except EtsyAPIError as e:
            if e.status is None or e.status >= 500:  # not retried: Etsy may have created it
                raise PublishError(f"{plan.item_id}: createDraftListing failed without a clear answer "
                                   f"({e}); Etsy may have created the draft anyway. Check the drafts "
                                   "in Shop Manager before rerunning, because a rerun creates another") from e
            raise
        record = {"schema": SCHEMA, "item_id": plan.item_id, "shop_id": plan.shop_key,
                  "etsy_shop_id": client.shop_id, "listing_id": int(resp["listing_id"]),
                  "state": resp.get("state", "draft"), "image_ids": [], "file_ids": [],
                  "created": today(), "dry_run": bool(dry_run), "complete": False,
                  "listing_type": resp.get("listing_type"), "plan_digest": plan.digest}
        warnings = list(plan.warnings)
        if resp.get("listing_type") not in (None, "download"):
            # Not fixable here (updateListing is never sent); a digital file usually converts it.
            warnings.append(f"Etsy created the draft as {resp['listing_type']!r}, not 'download'; "
                            "check it is a digital listing before activating")
            log.warning("%s: %s", plan.item_id, warnings[-1])
        if warnings:
            record["warnings"] = warnings
        save()  # before any upload, so a crash from here on resumes this draft
        if record["state"] != "draft":
            raise PublishError(f"Etsy created listing {record['listing_id']} as {record['state']!r}, not draft")
        log.info("%s: created draft %s", plan.item_id, record["listing_id"])
    else:
        if record.get("plan_digest") and record["plan_digest"] != plan.digest:
            raise PublishError(f"{plan.item_id}: listing.json, photos or files changed since draft "
                               f"{record['listing_id']} was created, so the draft no longer matches; "
                               + discard_hint)
        client.own(record["listing_id"])
        live = client.listing_state(record["listing_id"])
        if live is None:
            raise PublishError(f"{plan.item_id}: draft {record['listing_id']} no longer exists on Etsy; "
                               + discard_hint)
        if live.get("state") != "draft":
            raise PublishError(f"{plan.item_id}: listing {record['listing_id']} is "
                               f"{live.get('state')!r} on Etsy; the publisher only adds to drafts")
        log.info("%s: resuming draft %s", plan.item_id, record["listing_id"])

    lid = record["listing_id"]
    for i, (rel, alt) in enumerate(plan.images):
        if i < len(record["image_ids"]):
            continue
        resp = client.upload_listing_image(lid, item_dir / rel, rank=i + 1, alt_text=alt)
        record["image_ids"].append(int(resp["listing_image_id"]))
        save()
    for i, rel in enumerate(plan.files):
        if i < len(record["file_ids"]):
            continue
        try:
            resp = client.upload_listing_file(lid, item_dir / rel, rank=i + 1)
        except EtsyAPIError as e:
            if e.status is None or e.status >= 500:  # not retried: Etsy may have stored it
                raise PublishError(f"{plan.item_id}: uploading {rel} to draft {lid} failed without a "
                                   f"clear answer ({e}); Etsy may have stored it anyway. Check the "
                                   "draft's files in Etsy before rerunning, because a rerun uploads "
                                   "it again") from e
            raise
        record["file_ids"].append(int(resp["listing_file_id"]))
        save()

    record["complete"] = True
    save()
    if persist:
        set_status(item_dir, "drafted")
    return record


def owned_listing_ids(out_root: str | Path) -> set[int]:
    """Listing ids from every real publish.json: the only listings this tool may touch."""
    ids = set()
    for d in iter_item_dirs(out_root):
        try:
            rec = _read_record(d)
        except PublishError:  # reported per item by run_all; grants nothing here
            continue
        if rec and not rec.get("dry_run") and str(rec.get("listing_id", "")).isdigit():
            ids.add(int(rec["listing_id"]))
    return ids


def run_all(out_root: str | Path | None = None, *, client: EtsyClient | None = None,
            dry_run: bool = False, settings: dict | None = None,
            review_dir: str | Path | None = None, item_ids: list[str] | None = None) -> list[dict]:
    """Draft every listed item (and finish half-done ones). One result dict per item.

    Gate refusals and Etsy errors are recorded per item; a SafetyViolation or missing
    credentials stop the whole run, because they mean the setup is wrong."""
    settings = settings or load_settings()
    out_root = Path(out_root) if out_root else output_root(settings)
    todo, results = [], []
    for d in iter_item_dirs(out_root):
        if item_ids and d.name not in item_ids:
            continue
        try:
            rec = _read_record(d)
            status = _load(d / "manifest.json", d.name).get("status")
        except (PublishGateError, PublishError) as e:
            results.append({"item_id": d.name, "ok": False, "listing_id": None, "detail": str(e)})
            continue
        unfinished = rec is not None and not rec.get("complete") and (dry_run or not rec.get("dry_run"))
        if item_ids or status == "listed" or unfinished:
            todo.append(d)
    if not todo:
        return results
    owned = owned_listing_ids(out_root)
    if client is not None:
        for lid in owned:
            client.own(lid)
    clients: dict[str, EtsyClient] = {}

    def client_for(d: Path) -> EtsyClient | None:
        """The caller's client, else one real client per shop (dry runs: a fake per item)."""
        if client is not None or dry_run:
            return client
        key = _shop_key(d, settings)
        if key not in clients:
            clients[key] = EtsyClient(settings=settings, shop_key=key)
            for lid in owned:
                clients[key].own(lid)
        return clients[key]

    for d in todo:
        try:
            rec = publish_draft(d, client=client_for(d), dry_run=dry_run, settings=settings,
                                review_dir=review_dir)
            results.append({"item_id": d.name, "ok": True, "listing_id": rec["listing_id"],
                            "detail": f"draft {rec['listing_id']}" + (" (dry run)" if rec.get("dry_run") else "")})
        except (PublishGateError, PublishError, EtsyAPIError) as e:
            results.append({"item_id": d.name, "ok": False, "listing_id": None, "detail": str(e)})
    return results


def _shop_key(item_dir: Path, settings: dict) -> str | None:
    """The configured shop an item belongs to, or None (first shop; the gates then report
    an unknown shop before anything is sent)."""
    known = {s.get("id") for s in settings.get("shops") or []}
    for name in ("listing.json", "manifest.json"):
        try:
            key = read_json(item_dir / name).get("shop_id")
        except (OSError, ValueError, AttributeError):
            continue
        if key:
            return key if key in known else None
    return None


def discard_draft(item_dir: str | Path, *, client: EtsyClient | None = None,
                  settings: dict | None = None) -> dict:
    """Delete this tool's draft for an item (if it is still a draft) and forget it, so the
    next publish starts over. Used to clean up a failed or outdated partial draft."""
    item_dir = Path(item_dir)
    settings = settings or load_settings()
    record = _read_record(item_dir)
    if not record or not record.get("listing_id"):
        raise PublishError(f"{item_dir.name}: no publish.json draft to discard")
    deleted = False
    if not record.get("dry_run"):
        shop_key = record.get("shop_id")
        if shop_key not in {s.get("id") for s in settings.get("shops") or []}:
            raise PublishError(f"{item_dir.name}: publish.json names shop {shop_key!r}, which is not "
                               "in config/settings.yaml")
        client = client or EtsyClient(settings=settings, shop_key=shop_key)
        if getattr(client, "shop_key", shop_key) != shop_key or \
                str(record.get("etsy_shop_id")) != str(client.shop_id):
            raise PublishError(f"publish.json is for shop {shop_key!r} ({record.get('etsy_shop_id')}), "
                               f"the client is for {getattr(client, 'shop_key', '?')!r} ({client.shop_id})")
        client.own(record["listing_id"])
        deleted = client.delete_listing(record["listing_id"])  # False: already gone in Etsy
    archived = item_dir / f"publish.discarded-{record['listing_id']}.json"
    done = {**record, "discarded": today(), "deleted": deleted}
    write_json(archived, done)
    (item_dir / PUBLISH_FILE).unlink()
    if read_manifest(item_dir).get("status") == "drafted":
        set_status(item_dir, "listed")
    return {**done, "archived": archived.name}
