"""curate(): a derived pack's pages out of a master set (docs/PACKS.md section 3)."""
from collections import Counter

import pytest

from generators.packs import curate
from generators.packs import sets as sets_mod

TYPES = ("maze", "word_search", "tracing", "dot_to_dot")


def master(n=20, types=TYPES, quality=None):
    doc = sets_mod.new_set_doc("S", niche="activity_sheets", theme="farm", theme_key="farm", shop_id="main",
                               brief_id="b", created="2026-10-15", kind="master", size=n, dry_run=False, model=None,
                               ai_assisted=False)
    doc["pages"] = [sets_mod.page_entry({"page_id": f"S-p{i:02d}", "type": types[i % len(types)],
                                         "subject": f"subject {i}",
                                         "quality": (quality or {}).get(i, 1.0), "spec": {}}) for i in range(n)]
    return doc


def ids(pages):
    return [p["page_id"] for p in pages]


def use(doc, item_id, pages, size, source="derived"):
    sets_mod.record_item(doc, item_id, page_ids=ids(pages), size=size, source=source, created="2026-10-15")


def test_curation_is_deterministic_and_keeps_the_set_order():
    doc = master()
    a = curate(doc, 5, seed="x")
    assert ids(a) == ids(curate(master(), 5, seed="x"))
    order = [p["page_id"] for p in doc["pages"]]
    assert ids(a) == sorted(ids(a), key=order.index)
    assert len({tuple(ids(curate(doc, 5, seed=s))) for s in "abcdefgh"}) > 1   # the seed breaks ties


def test_curation_round_robins_across_page_types_and_subjects():
    doc = master(20)
    picked = curate(doc, 8, seed="x")
    assert Counter(p["type"] for p in picked) == Counter({t: 2 for t in TYPES})
    assert len({p["subject"] for p in picked}) == 8
    # With repeated subjects, different ones come first.
    doc = master(12)
    for p in doc["pages"]:
        p["subject"] = "cow" if p["page_id"] < "S-p06" else p["subject"]
    picked = curate(doc, 4, seed="x")
    assert sum(p["subject"] == "cow" for p in picked) <= 1


def test_derived_packs_of_the_same_size_avoid_each_others_pages():
    doc = master(20)
    first = curate(doc, 5, size=5, seed="a", exclude_item="item-1")
    use(doc, "item-1", first, 5)
    second = curate(doc, 5, size=5, seed="b", exclude_item="item-2")
    assert not set(ids(first)) & set(ids(second))
    # The master's own use does not count; a derived pack of another size is avoided next.
    use(doc, "item-m", doc["pages"], 20, source="master")
    assert ids(curate(doc, 5, size=5, seed="b", exclude_item="item-2")) == ids(second)
    ten = curate(doc, 10, size=10, seed="c", exclude_item="item-10")
    assert not set(ids(ten)) & set(ids(first))
    use(doc, "item-10", ten, 10)
    third = curate(doc, 5, size=5, seed="b", exclude_item="item-2")
    assert not set(ids(third)) & (set(ids(first)) | set(ids(ten)))
    # A rebuild of the same item picks the same pages (its own earlier use does not count).
    assert ids(curate(doc, 5, size=5, seed="a", exclude_item="item-1")) == ids(first)


def test_pages_are_reused_only_when_no_unused_ones_are_left():
    doc = master(8)
    first = curate(doc, 5, size=5, seed="a", exclude_item="i1")
    use(doc, "i1", first, 5)
    second = curate(doc, 5, size=5, seed="b", exclude_item="i2")
    assert set(ids(doc["pages"])) - set(ids(first)) <= set(ids(second))   # all 3 unused pages, then 2 reused


def test_low_quality_pages_come_last():
    doc = master(8, types=("coloring_page",), quality={0: 0.2, 1: 0.3, 2: 0.9})
    assert ids(curate(doc, 6, seed="x")) == [f"S-p{i:02d}" for i in range(2, 8)]
    assert ids(curate(doc, 1, seed="x")) != ["S-p02"]                    # 0.9 loses to a 1.0 page
    assert set(ids(curate(doc, 8, seed="x"))) == set(ids(doc["pages"]))   # all of them when needed


def test_curation_refuses_more_pages_than_the_set_has():
    with pytest.raises(ValueError):
        curate(master(4), 5)


def test_pages_that_fit_the_theme_come_before_avoiding_other_sizes():
    """A dinosaur pack's dot-to-dot is the dinosaur, in the 5- and the 10-page pack alike;
    among pages that fit equally, a derived pack of another size is still avoided."""
    doc = master(8, types=("dot_to_dot",))
    for p in doc["pages"]:
        p["fit"] = 1.0 if p["page_id"] in ("S-p03", "S-p05") else 0.6
    five = curate(doc, 1, size=5, seed="a", exclude_item="i5")
    assert ids(five)[0] in ("S-p03", "S-p05")
    use(doc, "i5", five, 5)
    ten = curate(doc, 2, size=10, seed="b", exclude_item="i10")
    assert ids(ten) == ["S-p03", "S-p05"]                              # both on-theme pictures
    use(doc, "i10", ten, 10)
    assert ids(curate(doc, 3, size=3, seed="c"))[:2] == ["S-p03", "S-p05"]
