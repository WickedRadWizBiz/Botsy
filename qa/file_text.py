"""Blocklist and health-claims check of the text a buyer sees inside the delivered files.

That is the words printed on PDF pages (titles, word lists, footers), the PDF document
properties a viewer shows (title, subject, keywords, author) and the file names inside ZIP
archives. Game-system names are never printed, in any niche (blocklist `!` terms and the
spellings core.compliance.system_mentions finds). Medical topics (config/health_terms.txt)
arrive through the blocklist: a medical term is an error, a borderline one a warning (review).
Medical wording and health claims (docs/HEALTH_TERMS.md "Decided 2026-10-17") are checked
per niche: coloring pages, stickers and activity sheets with listing.rules.medical; stationery
and ttrpg with each generator's own text_problems, so TTRPG game words ("Medicine (Wis)",
"Sanity", "Major Wound") keep printing. The generators check what they print; this is QA's
own look at the finished files, so a slip in a generator cannot reach a listing (CLAUDE.md:
no trademarks in designs, no medical claims).
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path, PurePosixPath

from core.compliance import Blocklist, find_urls
from qa.checks import WARNING, Item, result, system_mentions

log = logging.getLogger("qa.file_text")

# Listing claim words that are ordinary words on a kids' page: "TREAT" in a Halloween word
# search is candy. A real claim ("treats anxiety") is still a core.health claim.
PRINTED_OK_WORDS = frozenset({"treat", "treats"})

# text_problems lines QA already reports through its own blocklist and system checks.
_OWN_CHECKS = ("printed text contains blocked terms", "printed text names game systems")

META_KEYS = ("Title", "Subject", "Keywords", "Author")  # Creator/Producer name the tools, not the product


def pdf_text(path: str | Path) -> list[str]:
    """Document properties and the extracted text of every page."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        meta = doc.get_metadata_dict(skip_empty=True)
        texts = [str(meta[k]) for k in META_KEYS if meta.get(k)]
        for i in range(len(doc)):
            page = doc[i]
            textpage = page.get_textpage()
            try:
                texts.append(textpage.get_text_range())
            finally:
                textpage.close()
                page.close()
    finally:
        doc.close()
    return texts


def zip_names(path: str | Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        return [PurePosixPath(i.filename.replace("\\", "/")).name for i in z.infolist() if not i.is_dir()]


def printed_claims(texts: list[str], niche: str, settings: dict) -> list[str]:
    """Medical wording and health claims in printed text, by niche: stationery and ttrpg
    through their generator's text_problems (its medical and claims lines only; blocked
    terms and game systems are QA's own checks), every other niche through
    listing.rules.medical over each line. [] when clean."""
    lines = [ln.strip() for t in texts if t for ln in str(t).splitlines() if ln.strip()]
    if not lines:
        return []
    if niche in ("stationery", "ttrpg"):
        try:
            if niche == "stationery":
                from generators.stationery.render import text_problems
            else:
                from generators.ttrpg.render import text_problems
        except Exception as e:  # the generator cannot be imported: the listing rule decides
            log.warning("%s text_problems unavailable (%s: %s); using the listing rule", niche, type(e).__name__, e)
        else:
            return [p for p in text_problems(lines, settings) if not p.startswith(_OWN_CHECKS)]
    from listing import listing_config
    from listing.rules import medical

    cfg = dict(listing_config(settings))
    cfg["medical_terms"] = [t for t in cfg.get("medical_terms") or () if t.casefold() not in PRINTED_OK_WORDS]
    words = sorted({w for ln in lines for w in medical(ln, cfg)})
    return [f"printed text uses medical wording or health claims {words}"] if words else []


def check_file_text(item: Item, blocklist: Blocklist) -> list[dict]:
    """Blocked term (trademark or medical topic) in a file -> error; medical wording or a
    health claim in printed text -> error; review term (borderline health topic included) or
    URL -> warning (a URL printed in a product is not forbidden, but a person should see
    where it points)."""
    blocked, claims, flagged, unread, checked = [], [], [], [], 0
    niche = str(item.manifest.get("niche") or "")
    for e in item.entries("files", "pdf", "zip"):
        rel = e["path"]
        try:
            texts = pdf_text(item.path(e)) if e["kind"] == "pdf" else zip_names(item.path(e))
        except Exception:  # damaged file: pdf_opens / zip_valid already report it
            unread.append(rel)
            continue
        checked += 1
        r = blocklist.check(*texts)  # game-system (!) terms are blocked: never printed
        hits = blocklist.labels(r.blocked) + [f"game system {s}" for s in system_mentions(texts)]
        if hits:
            blocked.append(f"{rel}: {', '.join(hits)}")
        problems = printed_claims(texts, niche, item.settings)
        if problems:
            claims.append(f"{rel}: {'; '.join(problems)}")
        urls = find_urls(texts)
        if r.flagged or urls:
            flagged.append(f"{rel}: " + ", ".join(blocklist.labels(r.flagged) + [f"URL {u}" for u in urls[:3]]))
    scope = f"text of {checked} PDF/ZIP files" + (f" ({len(unread)} unreadable)" if unread else "")
    return [
        result("compliance_file_text", not blocked,
               f"blocked terms in {'; '.join(blocked)}" if blocked else f"no blocked terms in the {scope}"),
        result("compliance_file_claims", not claims,
               f"health claims in {'; '.join(claims)}" if claims else f"no medical wording or health claims in the {scope}"),
        result("compliance_file_review_terms", not flagged,
               f"review terms or URLs in {'; '.join(flagged)}" if flagged else f"no review terms or URLs in the {scope}",
               WARNING),
    ]
