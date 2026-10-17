"""Header aliasing, tool/shape detection, number parsing and CSV reading."""
import math

import pytest

from research import columns
from research.columns import detect_shape, detect_tool, load_export, map_columns, parse_number

from .conftest import write_csv


def test_header_normalization_ignores_case_space_punctuation():
    assert {columns.norm_header(h) for h in ("Avg. Searches", " avg searches ", "AVG_SEARCHES",
                                              "Avg-Searches", "﻿Avg. Searches")} == {"avgsearches"}


def test_erank_keyword_tool_headers():
    headers = ["Keywords", "Search Trend", "Avg. Searches", "Avg. Clicks", "Avg. CTR",
               "Etsy Competition", "KD", "Tag Occurrences", "Chars.", "Google Searches"]
    m = map_columns(headers)
    assert m == {"keyword": "Keywords", "search_volume": "Avg. Searches", "clicks": "Avg. Clicks",
                 "ctr": "Avg. CTR", "competition": "Etsy Competition"}
    assert detect_tool(headers) == "erank"
    assert detect_shape(m) == "keyword"


def test_erank_top_listings_headers():
    headers = ["Rank", "Listing", "Age (Days)", "Views", "Daily Views", "Est. Sales", "Price",
               "Est. Revenue", "Hearts", "Tags"]
    m = map_columns(headers)
    assert m["title"] == "Listing" and m["listing_age"] == "Age (Days)"
    assert m["views"] == "Views" and m["sales"] == "Est. Sales" and m["favorites"] == "Hearts"
    assert m["revenue"] == "Est. Revenue" and m["tags"] == "Tags"
    assert detect_tool(headers) == "erank" and detect_shape(m) == "listing"


def test_everbee_style_headers_with_variants():
    headers = ["Product Name", "Shop Name", "Price ($)", "Total Sales", "Est. Mo. Sales",
               "Mo. Revenue", "# of Reviews", "Favourites", "Listing Age (Months)", "Shop Age",
               "Shop Sales", "Category", "Tags", "Listing URL", "Thumbnail", "Listing ID"]
    m = map_columns(headers)
    assert m["title"] == "Product Name"
    assert m["shop_name"] == "Shop Name"
    assert m["price"] == "Price ($)"
    assert m["sales"] == "Est. Mo. Sales"          # monthly beats lifetime
    assert m["revenue"] == "Mo. Revenue"
    assert m["reviews"] == "# of Reviews"          # fallback rule
    assert m["favorites"] == "Favourites"
    assert m["listing_age"] == "Listing Age (Months)"
    assert m["url"] == "Listing URL" and m["image"] == "Thumbnail" and m["listing_id"] == "Listing ID"
    assert "Shop Age" not in m.values() and "Shop Sales" not in m.values()
    assert detect_tool(headers) == "everbee"
    assert detect_shape(m) == "listing"


def test_tool_detection_by_filename_and_unknown():
    assert detect_tool(["Title", "Price"], "EverBee-export.csv") == "everbee"
    assert detect_tool(["Title", "Price"], "x.csv", "Exported from eRank") == "erank"
    assert detect_tool(["Title", "Price"], "x.csv") == "unknown"
    assert detect_shape({"price": "Price"}) is None


def test_column_overrides_win_and_unknown_column_raises():
    m = map_columns(["Term", "Searches (US)", "Avg. Searches"], {"search_volume": ["Searches (US)"]})
    assert m["search_volume"] == "Searches (US)" and m["keyword"] == "Term"
    with pytest.raises(ValueError, match="unknown column"):
        map_columns(["x"], {"nonsense": "x"})


@pytest.mark.parametrize("raw, expected", [
    ("$4.99", 4.99), ("1,234", 1234.0), ("1.2k", 1200.0), ("12%", 12.0), ("3.4M", 3.4e6),
    ("4,99 €", 4.99), ("1.234,56", 1234.56), ("USD 12.50", 12.5), ("1,000 - 5,000", 3000.0),
    ("<20", 20.0), ("10k+", 10000.0), ("1 234", 1234.0), (7, 7.0), ("  42 ", 42.0),
    ("N/A", None), ("", None), ("   ", None), ("Unknown", None), ("-", None), ("abc", None),
    (None, None), (float("nan"), None),
])
def test_parse_number(raw, expected):
    got = parse_number(raw)
    if expected is None:
        assert got is None
    else:
        assert math.isclose(got, expected)


@pytest.mark.parametrize("raw, header, days", [
    ("120", "Age (Days)", 120.0), ("3 months", "Listing Age", 91.2), ("1y 2mo", "Age", 425.8),
    ("2", "Listing Age (Months)", 60.8), ("N/A", "Age", None),
])
def test_parse_age_days(raw, header, days):
    assert columns.parse_age_days(raw, header) == days


def test_load_export_latin1_semicolon_preamble(tmp_path):
    p = write_csv(tmp_path / "x.csv", ["Listing Title", "Shop", "Price", "Hearts"],
                  [["Café Animals Coloring Pages", "ShöpOne", "4,99 €".replace("€", "EUR"), "1.2k"]],
                  encoding="latin-1", delimiter=";",
                  preamble=["Report generated 2026-10-14", "Search: animals", ""])
    e = load_export(p)
    assert e.shape == "listing"
    row = e.frame.iloc[0]
    assert row["title"] == "Café Animals Coloring Pages"
    assert row["price"] == pytest.approx(4.99) and row["favorites"] == 1200
    assert row["shop_name"] == "ShöpOne"


def test_load_export_utf8_bom_and_junk_rows(tmp_path):
    p = tmp_path / "erank.csv"
    p.write_bytes("﻿Keyword,Avg. Searches,Etsy Competition\n"
                  "frog stickers,\"1,234\",\"5,600\"\n,,\nTotal,9,9\n".encode("utf-8"))
    e = load_export(p)
    assert e.tool == "erank" and e.shape == "keyword"
    assert e.frame["keyword"].tolist() == ["frog stickers"]
    assert e.frame["search_volume"].tolist() == [1234.0]
    assert e.frame["competition"].tolist() == [5600.0]


def test_load_export_utf16(tmp_path):
    p = tmp_path / "u16.csv"
    p.write_bytes("Title\tPrice\nFrog Stickers\t$3.00\n".encode("utf-16"))
    e = load_export(p)
    assert e.frame["title"].tolist() == ["Frog Stickers"] and e.frame["price"].tolist() == [3.0]


def test_search_results_is_competition_not_search_volume():
    """Etsy's Marketplace Insights shows 'Searches' (demand) next to 'Search Results'
    (competing listings). The second must never be read as demand, alone or together."""
    assert map_columns(["Keyword", "Search Results"]) == {"keyword": "Keyword",
                                                          "competition": "Search Results"}
    m = map_columns(["Keyword", "Searches", "Search Results"])
    assert m["search_volume"] == "Searches" and m["competition"] == "Search Results"
    assert map_columns(["Keyword", "Results Count"])["competition"] == "Results Count"
