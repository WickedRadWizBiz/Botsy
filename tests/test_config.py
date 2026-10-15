"""Phase 0 smoke tests: config files exist and hold the compliance guardrails."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"


def load_settings():
    return yaml.safe_load((CONFIG / "settings.yaml").read_text(encoding="utf-8"))


def test_settings_core_keys():
    s = load_settings()
    for key in ("shops", "niches", "models", "print", "research", "compliance", "listing"):
        assert key in s, key
    assert s["shops"], "at least one shop"
    for shop in s["shops"]:
        assert set(shop["niches"]) <= set(s["niches"])


def test_compliance_guardrails():
    s = load_settings()
    assert s["compliance"]["publisher_mode"] == "draft_only"
    assert s["research"]["min_listings_per_pattern"] >= 10
    assert s["listing"]["tags_count"] == 13
    assert s["listing"]["tag_max_chars"] == 20
    assert s["listing"]["title_max_chars"] == 140


def test_page_sizes():
    sizes = load_settings()["print"]["page_sizes"]
    assert {"us_letter", "a4"} <= set(sizes)


def test_blocklist_seeded():
    lines = (CONFIG / "trademark_blocklist.txt").read_text(encoding="utf-8").splitlines()
    terms = [l.strip() for l in lines if l.strip() and not l.startswith("#")]
    assert len(terms) >= 50
    assert len({t.lower() for t in terms}) == len(terms), "duplicate terms"


def test_ai_disclosure_has_shop_placeholder():
    text = (CONFIG / "ai_disclosure.txt").read_text(encoding="utf-8")
    assert "{shop_name}" in text and "AI" in text


def test_env_example_has_no_values_for_secrets():
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if any(s in k for s in ("KEY", "SECRET", "KEYSTRING")):
                assert v == "", f"{k} should be blank in .env.example"
