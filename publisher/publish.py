"""publish_draft(): one listed item -> an Etsy DRAFT with photos and digital files.

Gates run before anything is sent (PublishGateError lists every reason):
QA passed; listing.json present, not written in dry-run mode, blocklist-clean (title,
tags, description, alt texts and the photo and file names; game-system `!` names only in
a ttrpg item's title, tags and description, for the system its manifest names, and then
with that system's disclaimer in the description; never another system, never in alt text
or file names, and the system's trademark owner never in the title or tags), free of
medical or therapeutic wording (listing.rules.medical), free of the placeholder shop
name and carrying the AI disclosure for the current shop name (required whenever the item
is AI-assisted, the copy was written by the text model, or listing.always_disclose_ai is
on, the default); items needing review are `approved` in the review queue (a `rejected`
entry always blocks); taxonomy_id set; photos and files
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
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from core import etsy
from core.compliance import Blocklist, has_ai_disclosure, system_mentions
from core.config import PLACEHOLDER_SHOP_NAMES, get_shop, is_placeholder_shop_name, load_settings, section, today
from core.items import (
    iter_item_dirs,
    output_root,
    read_json,
    read_manifest,
    repo_relative,
    review_dir_for,
    review_entry_path,
    set_status,
    valid_item_id,
    write_json,
)
from publisher.client import EtsyAPIError, EtsyClient, title_key
from publisher.defaults import load_cfg
from publisher.fake import DRY_RUN_TAXONOMY_ID, FakeEtsyClient

log = logging.getLogger("publisher")

PUBLISH_FILE = "publish.json"
SCHEMA = "publish/v1"
WHO_MADE = ("i_did", "someone_else", "collective")  # Open API v3 spec enums
WHEN_MADE = ("made_to_order", "2020_2026", "2010_2019", "2007_2009", "before_2007", "2000_2006",
             "1990s", "1980s", "1970s", "1960s", "1950s", "1940s", "1930s", "1920s", "1910s",
             "1900s", "1800s", "1700s", "before_1700")


class PublishGateError(RuntimeError):
    """The item is not ready, or not allowed, to become a draft. Nothing was sent.
    `problems` lists every reason (the message joins them)."""

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = list(problems) if problems else [message]


class ReviewPending(PublishGateError):
    """The only reason the item is not drafted: it waits for `python -m qa review approve`."""


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

# Etsy's character rules for titles and tags, the same ones the listing stage applies.
_title_problems = etsy.title_char_problems
_tag_problems = etsy.tag_char_problems


def _text_limits(settings: dict) -> tuple[int, int, int]:
    """(title chars, tag count, tag chars): settings `listing:` (what the listing stage wrote
    to), capped at Etsy's own limits so a looser setting never passes copy Etsy rejects."""
    cfg = section(settings, "listing", {})

    def limit(key: str, etsy_max: int) -> int:
        try:
            value = int(cfg.get(key) or etsy_max)
        except (TypeError, ValueError):
            value = etsy_max
        return max(1, min(value, etsy_max))

    return (limit("title_max_chars", etsy.TITLE_MAX_CHARS), limit("tags_count", etsy.TAGS_MAX),
            limit("tag_max_chars", etsy.TAG_MAX_CHARS))


def _inside(item_dir: Path, rel: str) -> Path | None:
    """Resolve a listing path; None if it escapes the item folder."""
    p = (item_dir / rel).resolve()
    return p if p.is_relative_to(item_dir.resolve()) else None


def _with_out(cmd: str, item_dir: Path, settings: dict) -> str:
    """`cmd` plus `--out <folder>` when the item is not in the configured output dir, so
    the printed command finds it (dry runs and --out runs keep their items elsewhere)."""
    parent = item_dir.resolve().parent
    if parent == output_root(settings).resolve():
        return cmd
    return f"{cmd} --out {shlex.quote(repo_relative(parent))}"


def _approve_hint(item_id: str, item_dir: Path, review_dir: Path, settings: dict) -> str:
    """The `qa review approve` command that reaches the review entry this gate read."""
    cmd = f"python -m qa review approve {item_id}"
    if review_dir.resolve() == review_dir_for(item_dir, settings).resolve():
        return _with_out(cmd, item_dir, settings)
    return f"{cmd} --review-dir {shlex.quote(repo_relative(review_dir))}"


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


def _copy_texts(listing: dict) -> list[str]:
    """Title, tags and description: the only places a ttrpg listing may name its system."""
    return [(listing.get("title") or "").strip(), *[str(t).strip() for t in listing.get("tags") or []],
            (listing.get("description") or "").strip()]


def _other_texts(listing: dict) -> list[str]:
    """Alt texts and the photo and file names: never a game-system name."""
    names = [Path(str(rel)).name for rel in [*(listing.get("image_paths") or []), *(listing.get("file_paths") or [])]]
    return [*map(str, listing.get("alt_texts") or []), *names]


def _listing_check(listing: dict, blocklist: Blocklist, allow_systems: tuple[str, ...] = ()):
    """Blocklist result over everything sent to Etsy: title, tags, description, alt texts
    and the photo and file names (buyers see the download's name). `allow_systems` (a
    ttrpg item's own game system) applies to the title, tags and description only."""
    return blocklist.check(*_copy_texts(listing), allow_systems=allow_systems).merge(
        blocklist.check(*_other_texts(listing)))


def _game_system(manifest: dict, settings: dict) -> dict | None:
    """{id, disclaimer, catalog} for a ttrpg item whose manifest names a game system other
    than the default; None otherwise (nothing allowed). Raises PublishGateError when the
    system cannot be read: such an item is never drafted until config/ttrpg_systems.yaml
    is fixed, because its copy may name the system."""
    if manifest.get("niche") != "ttrpg" or manifest.get("system") in (None, "", "generic"):
        return None
    sid = str(manifest["system"])
    try:
        from core import systems  # lazy: only ttrpg items need the registry

        if sid == systems.default_system(settings):
            return None
        catalog = systems.load_systems(settings)
        if sid not in catalog:
            raise KeyError(f"unknown game system id {sid!r}")
        disclaimer = " ".join(str(systems.disclaimer_for(sid, settings) or "").split())
        entry = catalog[sid]
        owners = list(dict.fromkeys(o for o in [entry.get("owner"), *(entry.get("owner_names") or [])]
                                    if isinstance(o, str) and o.strip()))
    except Exception as e:
        raise PublishGateError(f"{manifest.get('item_id')}: game system {sid!r} cannot be read from "
                               f"config/ttrpg_systems.yaml ({type(e).__name__}: {e})") from None
    return {"id": sid, "disclaimer": disclaimer, "owners": owners, "catalog": catalog}


def _system_problems(listing: dict, found, system: dict | None, settings: dict) -> list[str]:
    """Game-system names in the copy need that system's disclaimer; other systems, and any
    system in alt texts or file names, are refused. core.compliance.system_mentions also
    finds spellings the blocklist does not ("DnD5e")."""
    problems = []
    named = set()
    catalog = system["catalog"] if system else None
    detected_copy = set(system_mentions(_copy_texts(listing), catalog))
    detected_other = set(system_mentions(_other_texts(listing), catalog))
    own = system["id"] if system else None
    others = sorted(detected_copy - {own}) if own else sorted(detected_copy)
    if others:
        problems.append(f"the copy names game systems it may not name: {', '.join(others)}")
    if detected_other:
        problems.append(f"alt texts or file names name a game system ({', '.join(sorted(detected_other))}); "
                        "only the title, tags and description of a ttrpg listing may")
    if found.allowed or (own and own in detected_copy):
        named.add(own)
    if system and system.get("owners"):  # the owner only in the description (its disclaimer)
        owners = Blocklist([], [], {o: own for o in system["owners"]})
        title_tags = [(listing.get("title") or "").strip(), *[str(t).strip() for t in listing.get("tags") or []]]
        in_title_tags = owners.check(*title_tags).blocked
        if in_title_tags:
            problems.append(f"the title or tags name the trademark owner ({', '.join(in_title_tags)}); only the "
                            "description's disclaimer may; re-list the item")
    if named and system:
        squash = lambda t: " ".join(str(t).split()).casefold()
        if not system["disclaimer"] or squash(system["disclaimer"]) not in squash(listing.get("description") or ""):
            problems.append(f"the copy names game system {own!r} but the description lacks its disclaimer "
                            "(config/ttrpg_systems.yaml); re-list the item")
    return problems


def _medical_problems(listing: dict, settings: dict) -> list[str]:
    """No medical or therapeutic claims in anything sent to Etsy (CLAUDE.md), with the
    listing stage's own word list and safe phrases (listing.rules.medical), so a
    hand-edited listing.json cannot carry one."""
    from listing import listing_config, rules  # lazy: only the gate needs the copy rules

    cfg = listing_config(settings)
    texts = [*_copy_texts(listing), *map(str, listing.get("alt_texts") or [])]
    words = sorted({w for t in texts for line in str(t).splitlines() for w in rules.medical(line, cfg)})
    if words:
        return [f"the listing uses medical or therapeutic wording ({', '.join(words)}); listings never make "
                "medical claims, re-list the item"]
    return []


def _review_gate(item_id: str, item_dir: Path, manifest: dict, listing: dict, qa_report: dict | None,
                 flagged, settings: dict, review_dir) -> tuple[str | None, bool]:
    """(problem, waiting) from the review queue. `waiting` is True when an approval would
    clear the problem; a rejection is final. An approval covers only the content QA
    checked when it was queued: when qa_report.json's content_digest differs from the
    entry's, the item waits for a new approval (QA reruns reopen such entries too)."""
    review_dir = Path(review_dir) if review_dir else review_dir_for(item_dir, settings)
    entry_path = review_entry_path(item_id, review_dir)
    entry = _load(entry_path, item_id) if entry_path.exists() else {}
    status = entry.get("status")
    needs_review = bool(manifest.get("needs_review") or listing.get("needs_review")
                        or (qa_report or {}).get("needs_review") or entry.get("needs_review")
                        or flagged or (listing.get("compliance") or {}).get("flagged"))
    if status == "rejected":
        return "rejected in the review queue", False
    hint = f"`{_approve_hint(item_id, item_dir, review_dir, settings)}`"
    if needs_review and status != "approved":
        return f"needs review (review status: {status or 'no entry'}); approve with {hint}", True
    digest = (qa_report or {}).get("content_digest")
    if needs_review and digest and entry.get("content_digest") != digest:
        rerun = _with_out(f"python -m qa --item {item_dir.resolve().name}", item_dir, settings)
        return (f"the approval was given for other files or photos than QA last checked (content_digest "
                f"differs); rerun `{rerun}` and approve again with {hint}"), True
    return None, False


def review_wait(item_dir: str | Path, *, settings: dict | None = None,
                review_dir: str | Path | None = None) -> str | None:
    """Why check_ready() would refuse this item for review-queue reasons alone (waiting for
    an approval, or rejected), else None. The pipeline asks this before publishing, so it
    uses the publisher's own rule. Raises PublishGateError for an unreadable item file."""
    item_dir = Path(item_dir)
    settings = settings or load_settings()
    manifest = _load(item_dir / "manifest.json", item_dir.name)
    item_id = str(manifest.get("item_id") or item_dir.name)
    if not valid_item_id(item_id):
        raise PublishGateError(f"invalid item id {item_id!r} in {item_dir / 'manifest.json'}")
    listing = _load(item_dir / "listing.json", item_id) if (item_dir / "listing.json").exists() else {}
    qa_report = _load(item_dir / "qa_report.json", item_id) if (item_dir / "qa_report.json").exists() else None
    try:
        system = _game_system(manifest, settings)
    except PublishGateError:
        system = None  # check_ready reports it; review terms do not depend on it
    blocklist = Blocklist.load(section(settings, "compliance", {}).get("trademark_blocklist"), health_path=section(settings, "compliance", {}).get("health_terms"),
                               catalog=system["catalog"] if system else None)
    allow = (system["id"],) if system else ()
    problem, _ = _review_gate(item_id, item_dir, manifest, listing, qa_report,
                              _listing_check(listing, blocklist, allow).flagged, settings, review_dir)
    return problem


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
    if not valid_item_id(item_id):  # it names the review entry file
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
        problems.insert(0, "listing.json missing; run the listing stage")
        raise PublishGateError(f"{item_id}: " + "; ".join(problems), problems)
    listing = _load(listing_path, item_id)
    # The listing stage records its dry-run mode twice: top-level dry_run and writer.dry_run.
    if (listing.get("dry_run") or (listing.get("writer") or {}).get("dry_run")) and not simulated:
        problems.append("listing.json was written in dry-run mode (template text)")
    shop_key = listing.get("shop_id") or manifest.get("shop_id")
    if manifest.get("shop_id") and shop_key != manifest["shop_id"]:
        problems.append(f"listing shop {shop_key!r} differs from manifest shop {manifest['shop_id']!r}")
    try:
        shop = get_shop(settings, shop_key)
    except KeyError as e:
        raise PublishGateError(f"{item_id}: {e.args[0]}") from None
    shop_name = str(shop.get("name") or "").strip()
    # listing.json holds the shop name it was written with (in the disclosure), so a name
    # fixed after listing needs the copy rewritten. `python -m listing --item` rewrites a
    # 'listed' item; the default run only lists qa_passed ones.
    relist = _with_out(f"python -m listing --item {item_dir.resolve().name}", item_dir, settings)
    if is_placeholder_shop_name(shop) and not simulated:
        problems.append("shop name in config/settings.yaml is still the placeholder (the AI "
                        f"disclosure would show it); set it, then re-list with `{relist}`")

    title = (listing.get("title") or "").strip()
    description = (listing.get("description") or "").strip()
    tags = [str(t).strip() for t in listing.get("tags") or []]
    alt_texts = list(listing.get("alt_texts") or [])
    if not simulated:  # the copy may predate a fixed shop name
        texts = [t.casefold() for t in (title, description, *tags, *map(str, alt_texts))]
        shown = [p for p in PLACEHOLDER_SHOP_NAMES if p and any(p.casefold() in t for t in texts)]
        if shown:
            problems.append(f"listing text still shows the placeholder shop name {', '.join(shown)}; "
                            f"set the name in config/settings.yaml, then re-list with `{relist}`")

    images = [str(x) for x in listing.get("image_paths") or []]
    files = [str(x) for x in listing.get("file_paths") or []]

    comp = section(settings, "compliance", {})
    # A ttrpg item made for a game system may name it in title, tags and description only.
    try:
        system = _game_system(manifest, settings)
    except PublishGateError as e:  # nothing allowed then: a copy naming the system is blocked too
        problems.append(str(e).split(": ", 1)[-1])
        system = None
    allow = (system["id"],) if system else ()
    blocklist = Blocklist.load(comp.get("trademark_blocklist"), health_path=comp.get("health_terms"), catalog=system["catalog"] if system else None)
    # File names are sent to Etsy too; buyers see the download's name.
    found = _listing_check(listing, blocklist, allow)
    blocked = sorted(set(found.blocked) | set((listing.get("compliance") or {}).get("blocked") or []))
    if blocked:
        problems.append(f"blocked trademark terms: {', '.join(blocked)}")
    problems += _system_problems(listing, found, system, settings)
    problems += _medical_problems(listing, settings)
    # The copy is AI-written even when the designs are procedural (activity sheets), so the
    # listing stage discloses on every listing by default; the gate follows the same rule
    # and never relies on the listing's own claim alone.
    writer = listing.get("writer") or {}
    must_disclose = bool(manifest.get("ai_assisted", True) or listing.get("ai_disclosure_included")
                         or writer.get("kind") == "model" or listing.get("model")
                         or section(settings, "listing", {}).get("always_disclose_ai", True))
    if must_disclose and not has_ai_disclosure(description, shop_name, comp.get("ai_disclosure")):
        problems.append(f"the description lacks the AI disclosure (config/ai_disclosure.txt with shop "
                        f"name {shop_name!r}); re-list with `{relist}`")

    review_problem, waiting = _review_gate(item_id, item_dir, manifest, listing, qa_report, found.flagged,
                                           settings, review_dir)
    if review_problem:
        problems.append(review_problem)

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
    max_title, max_tags, max_tag = _text_limits(settings)
    if not title or len(title) > max_title:
        problems.append(f"title must be 1-{max_title} characters (has {len(title)})")
    problems += _title_problems(title)
    if not description:
        problems.append("description is empty")
    if len(tags) > max_tags or len({t.lower() for t in tags}) != len(tags):
        problems.append(f"tags must be at most {max_tags} and unique")
    for t in tags:
        if not t or len(t) > max_tag:
            problems.append(f"tag {t!r} must be 1-{max_tag} characters")
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
        cls = ReviewPending if waiting and problems == [review_problem] else PublishGateError
        raise cls(f"{item_id}: " + "; ".join(problems), problems)

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
        same = client.own_listing_titles().get(title_key(plan.fields["title"]))
        if same:
            raise PublishError(
                f"{plan.item_id}: our shop already has {same['state']} listing {same['listing_id']} with the "
                f"title {plan.fields['title']!r}. An earlier run whose publish.json is not here (for example "
                "another CI checkout) probably drafted this item already. Check Shop Manager; delete that "
                "listing or re-list this item with a different title, then rerun")
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
