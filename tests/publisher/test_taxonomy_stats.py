"""Seller taxonomy listing and our own shop's stats CSV."""
from __future__ import annotations

import csv
import io

from publisher import __main__ as cli
from publisher import own_stats, taxonomy
from publisher.fake import FAKE_TAXONOMY, FakeEtsyClient, FakeEtsySession

from .conftest import SHOP_ID


def test_flatten_and_search_taxonomy():
    rows = taxonomy.flatten(FAKE_TAXONOMY)
    assert rows[0] == {"id": 90000001, "path": "Dry Run Paper Goods", "level": 0, "leaf": False}
    assert {"id": 90000003, "path": "Dry Run Paper Goods > Stickers", "level": 1, "leaf": True} in rows
    assert [r["id"] for r in taxonomy.search(rows, "paper STICK")] == [90000003]
    assert len(taxonomy.search(rows, None)) == 4


def test_taxonomy_needs_no_oauth(client, session):
    rows = taxonomy.list_nodes(client, "coloring")
    assert [r["id"] for r in rows] == [90000002]
    assert [(r["operation"], r["has_bearer"]) for r in session.requests] == [("getSellerTaxonomyNodes", False)]


def test_cli_taxonomy_dry_run(capsys):
    assert cli.main(["taxonomy", "--dry-run", "--search", "sheets"]) == 0
    out = capsys.readouterr().out
    assert "90000004  Dry Run Paper Goods > Activity Sheets" in out and "Stickers" not in out


def test_own_stats_pages_through_our_shop_only(settings, tmp_path):
    session = FakeEtsySession(SHOP_ID, demo_listings=False)
    for i in range(150):
        session.listings[i + 1] = {"listing_id": i + 1, "state": "active", "title": f"Our listing {i}",
                                   "views": i, "num_favorers": 1, "quantity": 999, "tags": ["a", "b"],
                                   "url": "https://www.etsy.com/listing/1/x",
                                   "price": {"amount": 350, "divisor": 100, "currency_code": "USD"},
                                   "original_creation_timestamp": 1_600_000_000}
    session.listings[999] = {"listing_id": 999, "state": "draft", "title": "a draft"}
    c = FakeEtsyClient(shop_id=SHOP_ID, settings=settings, session=session)
    path, n = own_stats.write_own_stats(c, tmp_path / "research_inputs" / "own_shop_stats.csv")
    assert n == 150
    calls = session.calls("getListingsByShop")
    assert [(r["params"]["limit"], r["params"]["offset"]) for r in calls] == [(100, 0), (100, 100)]
    assert all(r["path"] == f"/v3/application/shops/{SHOP_ID}/listings" and "state" not in r["params"]
               for r in calls)
    rows = list(csv.DictReader(io.StringIO(path.read_text())))
    assert list(rows[0]) == own_stats.COLUMNS
    assert rows[0]["title"] == "Our listing 0" and rows[0]["price"] == "3.50" and rows[0]["tags"] == "a, b"
    assert rows[5]["views"] == "5" and rows[5]["favorites"] == "1" and int(rows[0]["age_days"]) > 1000
    text = path.read_text()
    assert "etsy.com" not in text and "a draft" not in text  # no URLs, only active listings


def test_cli_own_stats_dry_run_writes_nothing(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["own-stats", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(",".join(own_stats.COLUMNS)) and "Dry run sample listing 1" in out
    assert list(tmp_path.iterdir()) == []


def test_own_stats_unescapes_etsy_html_entities():
    rows = own_stats.to_rows([{"title": "Kids&#39; Farm &amp; Barn Pages", "tags": ["kids&#39; pages"],
                               "price": {"amount": 400, "divisor": 100, "currency_code": "USD"}}], now=0)
    assert rows[0]["title"] == "Kids' Farm & Barn Pages" and rows[0]["tags"] == "kids' pages"


def test_cli_unknown_shop_is_a_setup_error(capsys):
    assert cli.main(["own-stats", "--shop", "nope"]) == 2
    assert "unknown shop id 'nope'" in capsys.readouterr().err
