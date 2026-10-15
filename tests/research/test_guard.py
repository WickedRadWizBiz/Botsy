"""validate_brief_doc raises on every compliance violation."""
import copy

import pytest

from research.guard import BriefComplianceError, Guard, validate_brief_doc

TITLE = "Farm Animals Coloring Pages for Kids, Printable PDF, Set 3"
SHOPS = ["LittleLearnersCo"]


def clean_doc():
    return {
        "schema": "brief/v1", "niche": "coloring_pages", "created": "2026-10-15", "shop_id": "main",
        "sources": [{"tool": "erank", "file": "erank-keywords.csv", "rows": 40}],
        "listing_count": 40, "min_listings_per_pattern": 10,
        "patterns": {"themes": [{"name": "farm animals", "listing_count": 23}],
                     "formats": [], "page_counts": [], "price_bands": [], "keyword_clusters": []},
        "briefs": [{"brief_id": "coloring_pages-2026-10-15-01", "theme": "farm animals",
                    "keywords": ["farm animal coloring pages"], "style_notes": "bold outlines",
                    "evidence": {"listing_count": 23, "score": 0.8},
                    "compliance": {"blocked": [], "flagged": []}, "needs_review": False}],
    }


def check(doc, blocklist):
    validate_brief_doc(doc, SHOPS, [TITLE], blocklist=blocklist)


def test_clean_doc_passes(blocklist):
    check(clean_doc(), blocklist)


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["briefs"][0]["keywords"].append("see www.example.com/x"), "url"),
    (lambda d: d["briefs"][0]["keywords"].append("little learners co farm"), "shop name"),
    (lambda d: d["briefs"][0]["keywords"].append("littlelearnersco"), "shop name"),
    (lambda d: d["briefs"][0].update(style_notes=f"like {TITLE.lower()}"), "verbatim"),
    (lambda d: d["patterns"]["themes"][0].update(listing_count=9), "listing_count"),
    (lambda d: d["briefs"][0]["evidence"].update(listing_count=9), "listing_count"),
    (lambda d: d.update(min_listings_per_pattern=5), "min_listings_per_pattern"),
    (lambda d: d["briefs"][0].update(theme="paw patrol pups"), "blocked"),
    (lambda d: d["briefs"][0].update(shop_name="x"), "forbidden field"),
    (lambda d: d["briefs"][0]["keywords"].append("il_570xN.123.jpg"), "image"),
    (lambda d: d["briefs"][0]["keywords"].append("listing 1234567890"), "listing id"),
])
def test_violations_raise(blocklist, mutate, message):
    doc = copy.deepcopy(clean_doc())
    mutate(doc)
    with pytest.raises(BriefComplianceError, match=message):
        check(doc, blocklist)


def test_short_titles_are_not_treated_as_copy(blocklist):
    g = Guard([], ["Farm Animals"], blocklist)
    assert g.ok("farm animals")
    g = Guard([], [TITLE], blocklist)
    assert not g.ok(f"x {TITLE} y") and g.ok("farm animals coloring pages")
