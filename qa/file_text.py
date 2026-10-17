"""Blocklist check of the text a buyer sees inside the delivered files.

That is the words printed on PDF pages (titles, word lists, footers), the PDF document
properties a viewer shows (title, subject, keywords, author) and the file names inside ZIP
archives. Game-system names are never printed, in any niche (blocklist `!` terms and the
spellings core.compliance.system_mentions finds). The generators check what they print;
this is QA's own look at the finished files, so a slip in a generator cannot reach a
listing (CLAUDE.md: no trademarks in designs).
"""
from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath

from core.compliance import Blocklist, find_urls
from qa.checks import WARNING, Item, result, system_mentions

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


def check_file_text(item: Item, blocklist: Blocklist) -> list[dict]:
    """Blocked term in a file -> error; review term or URL -> warning (a URL printed in a
    product is not forbidden, but a person should see where it points)."""
    blocked, flagged, unread, checked = [], [], [], 0
    for e in item.entries("files", "pdf", "zip"):
        rel = e["path"]
        try:
            texts = pdf_text(item.path(e)) if e["kind"] == "pdf" else zip_names(item.path(e))
        except Exception:  # damaged file: pdf_opens / zip_valid already report it
            unread.append(rel)
            continue
        checked += 1
        r = blocklist.check(*texts)  # game-system (!) terms are blocked: never printed
        hits = r.blocked + [f"game system {s}" for s in system_mentions(texts)]
        if hits:
            blocked.append(f"{rel}: {', '.join(hits)}")
        urls = find_urls(texts)
        if r.flagged or urls:
            flagged.append(f"{rel}: " + ", ".join(r.flagged + [f"URL {u}" for u in urls[:3]]))
    scope = f"text of {checked} PDF/ZIP files" + (f" ({len(unread)} unreadable)" if unread else "")
    return [
        result("compliance_file_text", not blocked,
               f"blocked terms in {'; '.join(blocked)}" if blocked else f"no blocked terms in the {scope}"),
        result("compliance_file_review_terms", not flagged,
               f"review terms or URLs in {'; '.join(flagged)}" if flagged else f"no review terms or URLs in the {scope}",
               WARNING),
    ]
