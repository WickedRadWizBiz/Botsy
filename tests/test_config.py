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


def test_gitignore_keeps_secrets_out_of_git():
    import shutil
    import subprocess

    import pytest

    if not shutil.which("git") or not (ROOT / ".git").exists():
        pytest.skip("needs a git checkout")
    secret = [".env", ".env.local", ".env.production", ".etsy_tokens.json", "..etsy_tokens.json.tmp",
              ".etsy_tokens_second.json", "etsy_tokens_second.json", "data/research_inputs/erank.csv"]
    kept = [".env.example", "config/settings.yaml", "data/briefs/stickers-2026-10-15.json"]
    def ignored(path):
        return subprocess.run(["git", "check-ignore", "--no-index", "-q", path], cwd=ROOT).returncode == 0
    assert [p for p in secret if not ignored(p)] == []
    assert [p for p in kept if ignored(p)] == []


def test_env_example_readme_references_exist():
    import re

    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    headings = [l.lstrip("#").strip() for l in readme.splitlines() if l.startswith("#")]
    for ref in re.findall(r'README "([^"]+)"', env):
        assert ref in headings, f".env.example points to README section {ref!r}"


def test_weekly_workflow_scopes_secrets_and_inputs():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "weekly.yml").read_text(encoding="utf-8"))
    job = wf["jobs"]["batch"]
    assert not any("secrets." in str(v) for v in (job.get("env") or {}).values()), "secrets in job-level env"
    secret_steps = {s["name"] for s in job["steps"] if "secrets." in str(s.get("env"))}
    assert secret_steps <= {"Run pipeline", "Save rotated Etsy refresh token"}
    inputs = wf[True]["workflow_dispatch"]["inputs"]  # PyYAML reads the key `on` as True
    assert inputs["niche"]["type"] == "choice" and inputs["max_items"]["type"] == "number"


def test_brief_age_guard_is_below_the_weekly_cadence():
    age = load_settings()["pipeline"]["max_brief_age_days"]
    assert age is None or 0 <= age < 7
