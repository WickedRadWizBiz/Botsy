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


# ---------------------------------------------------------------- Botsy templates

TEMPLATES = columns.Path(__file__).resolve().parents[2] / "data" / "templates"


def test_template_files_have_the_fixed_content():
    etsy = (TEMPLATES / "etsy-insights-template.csv").read_text(encoding="utf-8").splitlines()
    assert etsy == ["# botsy-template: etsy_insights v1", "keyword,searches,competition,checked_on"]
    kdp = (TEMPLATES / "kdp-manual-template.csv").read_text(encoding="utf-8").splitlines()
    assert kdp == ["# botsy-template: kdp_manual v1",
                   "search_term,page_type,position,title,author,price,ratings_count,bsr,"
                   "print_length,publication_date,dimensions"]


@pytest.mark.parametrize("first_line", [
    "# botsy-template: etsy_insights v1",
    "# botsy-template: etsy_insights v1,,,",          # Excel pads the row with empty cells
    '"# botsy-template: etsy_insights v1",,,',        # or quotes it
    "﻿# botsy-template: etsy_insights v1",       # UTF-8 BOM
    "#botsy-template:etsy_insights",
])
def test_template_marker_names_the_tool(first_line):
    assert columns.template_kind(first_line + "\n") == "etsy_insights"
    assert detect_tool(["keyword", "searches", "competition", "checked_on"], "x.csv",
                       first_line + "\n") == "botsy_etsy_insights"
    assert columns.template_kind("Report generated 2026-10-14\n") is None


def test_etsy_insights_template_reads_as_keyword_data_with_bom_and_trailing_commas(tmp_path):
    """What Excel writes after the user types into the template and saves it as CSV."""
    p = tmp_path / "etsy-insights-week-42.csv"
    p.write_bytes(("﻿# botsy-template: etsy_insights v1,,,\r\n"
                   "keyword,searches,competition,checked_on\r\n"
                   "frog stickers,\"1,234\",\"5,600\",2026-10-17\r\n"
                   "frog clipart,800,2100,2026-10-17\r\n"
                   ",,,\r\n").encode("utf-8"))
    e = load_export(p)
    assert e.tool == "botsy_etsy_insights" and e.shape == "keyword"
    assert e.mapping["keyword"] == "keyword" and e.mapping["search_volume"] == "searches"
    assert e.mapping["competition"] == "competition" and "checked_on" not in e.mapping.values()
    assert e.frame["keyword"].tolist() == ["frog stickers", "frog clipart"]
    assert e.frame["search_volume"].tolist() == [1234.0, 800.0]
    assert e.frame["competition"].tolist() == [5600.0, 2100.0]   # Search Results, never demand
    assert columns.sniff_tool(p) == "botsy_etsy_insights"


def test_blank_etsy_template_still_names_its_tool(tmp_path):
    p = tmp_path / "etsy-insights-template.csv"
    p.write_bytes((TEMPLATES / "etsy-insights-template.csv").read_bytes())
    e = load_export(p)
    assert e.tool == "botsy_etsy_insights" and e.shape == "keyword" and e.frame.empty


def test_kdp_manual_template_is_a_kdp_tool(tmp_path):
    p = tmp_path / "kdp-coloring-oct.csv"
    p.write_bytes(((TEMPLATES / "kdp-manual-template.csv").read_text(encoding="utf-8").replace(
        "\n", ",,\r\n", 1) + "sudoku large print,search,1,Big Sudoku Book,A Writer,$7.99,120,"
        "\"#12,345 in Books\",120 pages,2025-03-01,8.5 x 0.3 x 11 inches\r\n").encode("utf-8"))
    e = load_export(p)
    assert e.tool == "botsy_kdp_manual" and columns.is_kdp_tool(e.tool)
    assert columns.sniff_tool(p) == "botsy_kdp_manual"


# ---------------------------------------------------------------- KDP / Amazon exports

def test_book_headers_are_never_taken_as_erank_etsy_data(tmp_path):
    """Title/Price/Reviews/Est. Sales next to BSR used to read as an eRank listing file."""
    headers = ["Title", "Price", "Reviews", "Est. Sales", "BSR"]
    assert detect_tool(headers) == "kdp_unknown"
    p = write_csv(tmp_path / "export.csv", headers,
                  [["Big Sudoku Book", "$7.99", "120", "300", "12,345"]])
    e = load_export(p)
    assert e.tool == "kdp_unknown" and columns.is_kdp_tool(e.tool)
    assert columns.sniff_tool(p) == "kdp_unknown"


@pytest.mark.parametrize("headers", [
    ["Title", "Author", "Price", "Best Sellers Rank"],
    ["Title", "ASIN", "Price"],
    ["Title", "Author", "Print Length", "Publication Date"],
    ["Title", "Author", "Dimensions", "Price"],                 # two weak book headers
    ["Product", "Sales Rank", "Page Count", "Trim Size"],
])
def test_kdp_headers(headers):
    assert columns.kdp_headers(headers) and detect_tool(headers) == "kdp_unknown"


def test_a_single_weak_book_header_is_not_enough():
    assert not columns.kdp_headers(["Title", "Price", "Dimensions"])
    assert detect_tool(["Product Name", "Shop Name", "Mo. Sales", "Dimensions"]) == "everbee"


def test_bookbeam_by_name_and_amazon_links_in_rows(tmp_path):
    assert detect_tool(["Title", "Price", "Reviews"], "BookBeam Export (2).csv") == "bookbeam"
    assert detect_tool(["Title", "Price", "Reviews"], "", "bookbeam.io research\n") == "bookbeam"
    p = write_csv(tmp_path / "books.csv", ["Title", "Price", "Reviews", "Link"],
                  [["Big Sudoku Book", "$7.99", "120", "https://www.amazon.com/dp/B0TEST1234"]])
    e = load_export(p)
    assert e.tool == "kdp_unknown"
    assert detect_tool(["Title", "Price"], "kdp-coloring-oct.csv") == "kdp_unknown"
    # "amazon" in a file name proves nothing: a sticker pack can be about the Amazon rainforest.
    assert detect_tool(["Title", "Price"], "amazon list.csv") == "unknown"
    assert not columns.is_kdp_tool(detect_tool(["Title", "Price", "Reviews", "Est. Sales", "Shop"],
                                               "amazon rainforest stickers.csv"))
    # An Etsy export keeps its tool even with such a name; etsy links are not amazon links.
    assert detect_tool(["Product Name", "Mo. Sales", "Shop Name"], "amazon-ish.csv") == "everbee"
    assert detect_tool(["Product Name", "Mo. Sales", "Shop Name"], "kdp-ish.csv") == "everbee"
    assert detect_tool(["Keyword", "Searches"], "", "", "x,https://www.etsy.com/listing/1/a\n") == "unknown"


def test_sniff_tool_on_unreadable_or_missing_file(tmp_path):
    assert columns.sniff_tool(tmp_path / "missing.csv") == "unknown"
    assert columns.sniff_tool(tmp_path / "missing.csv", "everbee") == "everbee"


# ---------------------------------------------------------------- eRank lifetime figures

ERANK_TOP = ["Rank", "Listing", "Age (Days)", "Views", "Daily Views", "Est. Sales", "Price",
             "Est. Revenue", "Hearts", "Tags"]


def test_erank_lifetime_sales_become_monthly_estimates(tmp_path):
    p = write_csv(tmp_path / "erank top listings.csv", ERANK_TOP, [
        ["1", "Frog Coloring Pages", "304", "9000", "30", "100", "$4.00", "$400.00", "50", "frog"],
        ["2", "Owl Coloring Pages", "10", "900", "90", "20", "$4.00", "$80.00", "5", "owl"],     # younger than a month
        ["3", "Cat Coloring Pages", "", "900", "9", "20", "$4.00", "$80.00", "5", "cat"],       # no age: no monthly figure
    ])
    e = load_export(p)
    assert e.tool == "erank" and e.estimated_monthly == ("sales", "revenue")
    assert e.frame["sales"].tolist()[:2] == pytest.approx([10.0, 20.0])
    assert e.frame["revenue"].tolist()[:2] == pytest.approx([40.0, 80.0])
    # A lifetime figure cannot sit in a column labelled monthly: without an age it is missing.
    assert e.frame["sales"].isna().tolist() == [False, False, True]
    assert e.frame["revenue"].isna().tolist() == [False, False, True]
    assert e.lifetime["sales"].tolist() == [100.0, 20.0, 20.0]       # the exported values stay available
    assert e.lifetime["revenue"].tolist() == [400.0, 80.0, 80.0]
    assert e.sales_basis.startswith("estimated monthly")


def test_monthly_columns_and_other_tools_are_not_converted(tmp_path):
    p = write_csv(tmp_path / "erank.csv", ["Listing", "Age (Days)", "Mo. Sales", "Est. Revenue"],
                  [["Frog Coloring Pages", "304", "100", "$400.00"]])
    e = load_export(p)
    assert e.tool == "erank" and e.estimated_monthly == ("revenue",)
    assert e.frame["sales"].tolist() == [100.0] and e.frame["revenue"].tolist() == pytest.approx([40.0])
    p2 = write_csv(tmp_path / "everbee.csv", ["Product Name", "Shop Name", "Listing Age", "Total Sales"],
                   [["Frog Coloring Pages", "ShopA", "304 days", "100"]])
    e2 = load_export(p2)
    assert e2.tool == "everbee" and e2.estimated_monthly == () and e2.lifetime == {}
    assert e2.frame["sales"].tolist() == [100.0] and e2.sales_basis == "as exported"
    # No age column: nothing to convert with.
    p3 = write_csv(tmp_path / "erank2.csv", ["Listing", "Est. Sales", "Hearts", "Daily Views"],
                   [["Frog Coloring Pages", "100", "5", "3"]])
    assert load_export(p3).estimated_monthly == ()


# ---------------------------------------------------------------- review fixes (2026-10-17)

def test_a_forced_source_tool_never_turns_a_book_file_into_etsy_data(tmp_path):
    """research.source_tool: erank used to skip detection, so a BSR file became 'erank'."""
    from research.columns import sniff_tool

    p = write_csv(tmp_path / "books.csv", ["Title", "Price", "Reviews", "Est. Sales", "BSR"],
                  [["Dragon Coloring Book", "$7.99", "120", "300", "12000"]])
    assert load_export(p, source_tool="erank").tool == "kdp_unknown"
    assert sniff_tool(p, "erank") == "kdp_unknown"
    etsy = write_csv(tmp_path / "plain.csv", ["Listing", "Est. Sales", "Hearts"],
                     [["Frog Coloring Pages", "10", "5"]])
    assert load_export(etsy, source_tool="everbee").tool == "everbee"   # the setting still wins
    assert sniff_tool(etsy, "everbee") == "everbee"


def test_personal_columns_name_order_and_customer_fields():
    from research.columns import personal_columns

    amazon_orders = ["Website", "Order ID", "Order Date", "Unit Price", "ASIN", "Product Name",
                     "Shipping Address", "Billing Address", "Payment Instrument Type"]
    assert personal_columns(amazon_orders) == ["Order ID", "Shipping Address", "Billing Address",
                                               "Payment Instrument Type"]
    etsy_sold = ["Sale Date", "Item Name", "Buyer", "Quantity", "Price", "Ship Address1", "Ship Zipcode"]
    assert personal_columns(etsy_sold) == ["Buyer", "Ship Address1", "Ship Zipcode"]
    for research in (ERANK_TOP, ["keyword", "searches", "competition", "checked_on"],
                     ["Product Name", "Shop Name", "Mo. Sales", "Mo. Revenue", "Price", "Listing Age"],
                     ["Title", "Author", "BSR", "Print Length", "Publication Date", "Dimensions"]):
        assert personal_columns(research) == [], research


def test_export_keeps_its_header_row(tmp_path):
    p = write_csv(tmp_path / "erank.csv", ["Listing", "Age (Days)", "Est. Sales"],
                  [["Frog Coloring Pages", "304", "100"]])
    assert load_export(p).headers == ["Listing", "Age (Days)", "Est. Sales"]
