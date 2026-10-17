"""Health-term policy (docs/HEALTH_TERMS.md "Decided 2026-10-17"): medical topics blocked,
borderline topics flagged as health topics, lifestyle topics clean, the claims rule, the
printed-string sweep, and research theme cutting."""
import pytest
import yaml

from core import health
from core.compliance import Blocklist, iter_strings, normalize
from core.config import CONFIG_DIR, load_settings
from generators.stationery.render import text_problems
from listing import listing_config
from listing.rules import medical
from research.defaults import DEFAULTS as RESEARCH, load_config
from research.pipeline import niche_text_rules

HEALTH_FILE = CONFIG_DIR / "health_terms.txt"


@pytest.fixture(scope="module")
def bl():
    return Blocklist.load()


@pytest.fixture(scope="module")
def settings():
    return load_settings()


@pytest.fixture(scope="module")
def cfg(settings):
    return listing_config(settings)


# ------------------------------------------------------------------ the file and the loader

def test_health_file_is_loaded_next_to_the_trademark_file(bl):
    assert "Disney" in {t for t, _ in bl.hard}              # the trademark file still loads
    assert "blood pressure" in bl.health and bl.is_health("blood pressure")
    assert not bl.is_health("Disney") and bl.label("Disney") == "Disney"
    assert bl.label("blood pressure") == "blood pressure (health topic)"
    assert bl.labels(["Disney", "period tracker"]) == ["Disney", "period tracker (health topic)"]
    assert bl.health_terms == bl.health            # the alias service/searchlist.py reads


def test_health_file_floor():
    """Core medical terms stay in config/health_terms.txt (CLAUDE.md: ask before loosening)."""
    hard = {normalize(t) for t, _ in Blocklist.load(health_path=False).hard}
    full = Blocklist.load()
    medical_hard = {normalize(t) for t, _ in full.hard} - hard
    for term in ("blood pressure", "blood sugar", "medication", "symptom", "cbt", "migraine", "insulin",
                 "syringe", "pink ribbon", "depression", "eating disorder", "fertility"):
        assert term in medical_hard, term
    review = {normalize(t) for t, _ in full.review if full.is_health(t)}
    for term in ("period tracker", "pregnancy", "weight loss", "sobriety", "grief", "autism", "burnout"):
        assert term in review, term


def test_health_file_has_no_system_lines_and_one_kind_per_term():
    kinds: dict[str, set[str]] = {}
    for line in (l.strip() for l in HEALTH_FILE.read_text(encoding="utf-8").splitlines()):
        if line and not line.startswith("#"):
            assert not line.startswith("!"), line
            kinds.setdefault(normalize(line.lstrip("?").strip()), set()).add(line[0] if line[0] == "?" else "")
    assert not {k: v for k, v in kinds.items() if len(v) > 1}


def test_loader_without_health_file_and_with_explicit_path(tmp_path):
    base = Blocklist.load(health_path=False)
    assert base.check("Blood Pressure Log").ok and not base.health
    path = tmp_path / "h.txt"
    path.write_text("# test\nfoo bar\n?baz\n", encoding="utf-8")
    bl = Blocklist.load(health_path=path)
    r = bl.check("a foo-bar baz page")
    assert r.blocked == ["foo bar"] and r.flagged == ["baz"] and bl.health == {"foo bar", "baz"}
    path.write_text("!oops\n", encoding="utf-8")
    with pytest.raises(ValueError, match="game-system"):
        Blocklist.load(health_path=path)
    with pytest.raises(OSError):
        Blocklist.load(health_path=tmp_path / "missing.txt")


def test_settings_health_path_reaches_the_stationery_renderer_and_the_listing_config(tmp_path, settings):
    path = tmp_path / "h.txt"
    path.write_text("zorblax log\n?quuxle\n", encoding="utf-8")
    custom = {**settings, "compliance": {**(settings.get("compliance") or {}), "health_terms": str(path)}}
    problems = text_problems(["Zorblax Log", "Quuxle"], custom)
    assert problems == ["printed text contains blocked terms ['zorblax log (health topic)']"]
    assert text_problems(["Blood Pressure Log"], custom) == []        # the custom file replaces the default one
    assert text_problems(["Blood Pressure Log"], settings) != []
    assert listing_config(custom)["health_terms"] == ["zorblax log"]


def test_a_term_in_both_files_keeps_the_trademark_kind(tmp_path):
    tm = tmp_path / "tm.txt"
    tm.write_text("?Peter Rabbit\n", encoding="utf-8")
    h = tmp_path / "h.txt"
    h.write_text("peter rabbit\n", encoding="utf-8")
    bl = Blocklist.load(tm, health_path=h)
    assert bl.check("Peter Rabbit").flagged == ["Peter Rabbit"] and not bl.check("Peter Rabbit").blocked
    assert not bl.health


# ------------------------------------------------------------------ medical, borderline, lifestyle

MEDICAL = [
    "Blood Pressure Log Book", "Blood Sugar Tracker", "Diabetes Log", "Medication Tracker", "Pill Log",
    "Symptom Diary", "Migraine Journal", "Chronic Pain Tracker", "CBT Worksheet", "DBT Skills Workbook",
    "Fertility Tracker", "Kick Count Log", "Eating Disorder Recovery Journal", "ADHD Medication Tracker",
    "Insomnia Sleep Diary", "Pink Ribbon Stickers", "Syringe Sticker Sheet", "Awareness Ribbon Clipart",
    "Alzheimer's Caregiver Log", "GLP-1 Shot Tracker", "glp1 journal", "Pill Organizer Labels",
    "Depression Journal", "Panic Attack Log", "Vaccination Record", "Blood-pressure chart", "meds tracker",
    "Cute Pills Stickers", "Pill Sticker Sheet", "Medicine Bottle Clipart",
]
BORDERLINE = [
    "Period Tracker", "Pregnancy Journal", "Weight Loss Tracker", "Calorie Log", "Sobriety Tracker",
    "Grief Journal", "Dopamine Menu", "Autism Visual Schedule", "Menstrual Cycle Planner", "Burnout Journal",
    "Mental Health Awareness Stickers", "Dyslexia Reading Tracker",
]
LIFESTYLE = [
    "Meditation Journal", "Mindfulness Journal", "Stress Journal with Daily Check-In Pages", "Stress Log",
    "Stress Relief Coloring Pages", "Workout Log", "Exercise Log", "Fitness Planner", "Yoga Journal",
    "Running Log", "Gym Log", "Gratitude Journal", "Habit Tracker", "Water Intake Tracker", "Mood Tracker",
    "Sleep Log", "Self-Care Planner", "Food Diary", "Food Journal", "Food Log", "Meal Planner",
    "Anxiety Journal", "Mental Health Journal", "Mental Health Check-In Pages", "ADHD Daily Planner",
    "ADHD Planner", "Neurodivergent Planner", "Digital Detox Challenge", "Relaxing Mandala Coloring Pages",
    "Workout Log with Heart Rate", "Healthy Habits Tracker", "Wellness Planner", "Skincare Routine",
    "Worry Journal", "Feelings Chart for Kids", "Calm-Down Corner Poster", "Breathwork Log",
]
GUARDS = [
    "Cancer Zodiac Stickers", "Contractions Worksheet", "Pill Bug Coloring Page", "Brush Stroke Practice",
    "Pay Period Budget", "Class Schedule, Period 1-8", "Planner Refills", "No Pain No Gain Workout Log",
    "Sugar Cookie Coloring Page", "Spring Fever Stickers", "Trick or Treat Coloring Pages", "Doctor Pretend Play",
    "Nurse and Vet Community Helpers", "Medicine (Wis)", "First Aid", "Health", "Sanity", "Major Wound",
    "Psychology", "Dying", "Injuries and Scars", "Temperature Log for Baking", "Cycle Count Sheet",
    "Recovery Run Log", "Pressure Washing Checklist",
]


@pytest.mark.parametrize("text", MEDICAL)
def test_medical_topics_are_hard_blocked_health_topics(bl, text):
    r = bl.check(text)
    assert r.blocked, text
    assert all(bl.is_health(t) for t in r.blocked), (text, r.blocked)
    assert all("(health topic)" in label for label in bl.labels(r.blocked))


@pytest.mark.parametrize("text", BORDERLINE)
def test_borderline_topics_are_flagged_for_review_as_health_topics(bl, text):
    r = bl.check(text)
    assert r.ok and r.needs_review, (text, r)
    assert all(bl.is_health(t) for t in r.flagged), (text, r.flagged)
    assert all(label.endswith("(health topic)") for label in bl.labels(r.flagged))


@pytest.mark.parametrize("text", LIFESTYLE + GUARDS)
def test_lifestyle_topics_and_false_positive_guards_are_clean(bl, cfg, settings, text):
    r = bl.check(text)
    assert r.blocked == [] and r.flagged == [], (text, r)
    assert health.claim_problems(text) == [], text
    if text not in ("Medicine (Wis)", "First Aid"):  # TTRPG game words: the ttrpg renderer, not the listing rule
        assert medical(text, cfg) == [], text
    if "Treat" not in text:  # the stationery renderer keeps its stricter "treat" (no safe phrases on its pages)
        assert text_problems([text], settings) == [], text


# ------------------------------------------------------------------ the claims rule

CLAIMS_BLOCKED = [
    "reduces anxiety", "relieves migraine pain", "treats insomnia", "manage your ADHD symptoms", "clinically proven",
    "cures stress", "relieves stress", "helps manage stress", "eases tension", "improves sleep", "boosts your mood",
    "improve your focus", "anti-anxiety journal", "lowers blood pressure", "Manage your ADHD with this planner",
    "mood booster stickers", "reduce stress journal", "calm your nerves", "helps with ADHD", "regulate your emotions",
    "lose weight", "doctor recommended", "FDA approved", "anxiety relief", "pain relief", "stress relievers",
    "reducing anxiety", "fights fatigue", "anti anxiety", "therapist approved", "relieves symptoms",
    "prevents burnout", "heals your trauma", "scientifically proven", "evidence-based",
]
CLAIMS_ALLOWED = [
    "stress relief coloring pages", "relaxing", "calm", "ADHD planner to manage your day",
    "mood tracker to manage your month", "anxiety journal with daily check-in pages", "food diary",
    "rate your stress from 1 to 5", "calm, detailed patterns", "manage your week", "reduce clutter",
    "helps you focus on your top 3 tasks", "Workout log with sets, reps and weight columns",
    "improve your weight lifting", "calming ocean scenes", "stress management journal",
    "Rate your mood and energy each day and spot the patterns", "Sleep log: bedtime, wake time and a notes box",
    "ADHD-friendly daily planner: big boxes, visual time blocks", "stop procrastinating", "mindful coloring",
    "track your sleep and your water", "for anxious minds, a gentle check-in page",
]


@pytest.mark.parametrize("text", CLAIMS_BLOCKED)
def test_claims_are_blocked_everywhere(text, cfg, settings):
    assert health.claim_problems(text), text
    assert medical(text, cfg), text
    assert any("health claims" in p or "medical wording" in p for p in text_problems([text], settings)), text


@pytest.mark.parametrize("text", CLAIMS_ALLOWED)
def test_layout_and_look_copy_is_allowed(text, cfg, settings):
    assert health.claim_problems(text) == [], text
    assert medical(text, cfg) == [], text
    assert text_problems([text], settings) == [], text


def test_claim_phrases_are_reported_once_and_in_word_order(cfg):
    assert medical("Helps treat anxiety", cfg) == ["treat"]
    assert medical("reduces anxiety", cfg) == ["reduces anxiety"]
    assert medical("clinically proven", cfg) == ["clinically"]
    assert medical("anti-anxiety", cfg) == ["anti-anxiety"]
    assert health.claim_problems("Manage your ADHD symptoms today") == ["manage your adhd symptoms"]
    # "anxiety relief" is a noun claim, "stress relief" is not; "relief" alone is nothing
    assert health.claim_problems("anxiety relief and stress relief") == ["anxiety relief"]


def test_medical_topics_count_as_medical_wording_in_listing_copy(cfg):
    """A model draft naming a medical topic is repaired, not stopped as a trademark."""
    assert "blood pressure" in cfg["health_terms"] and "symptom" in cfg["health_terms"]
    assert medical("a blood pressure log page", cfg) == ["blood pressure"]
    assert medical("a mood tracker page", cfg) == []


def test_research_cut_terms_are_claim_words_in_every_stage(cfg):
    cut = health.research_cut_terms()
    assert {"anxiety relief", "pain relief", "mood booster", "mood boosters", "anti-anxiety", "clinically proven",
            "lose weight"} <= set(cut)
    assert "stress relief" not in cut and "anxiety" not in cut and "mental health" not in cut
    assert set(cut) <= set(RESEARCH["medical_terms"])
    assert [t for t in cut if not medical(t, cfg)] == []


# ------------------------------------------------------------------ printed-string sweep

def test_no_health_term_or_claim_inside_the_ttrpg_registry_or_the_stationery_catalog(bl):
    from generators.stationery.catalog import CATALOG, TITLE_OVERRIDES

    strings = list(iter_strings(yaml.safe_load((CONFIG_DIR / "ttrpg_systems.yaml").read_text(encoding="utf-8"))))
    strings += list(iter_strings([[v.__dict__ for v in vs] for vs in CATALOG.values()]))
    strings += list(iter_strings([list(d.values()) for d in TITLE_OVERRIDES.values()]))
    assert len(strings) > 1000
    joined = " | ".join(strings).lower()
    for word in ("medicine", "sanity", "wound", "health", "psychology", "first aid"):  # the sweep is real
        assert word in joined, word
    r = bl.check(*strings)
    assert [t for t in r.blocked + r.flagged if bl.is_health(t)] == []
    assert [s for s in strings if health.claim_problems(s)] == []


# ------------------------------------------------------------------ research

def test_research_cuts_claims_and_blocks_medical_rows_but_keeps_lifestyle_themes(settings, bl):
    cfg = load_config(settings)
    rules = niche_text_rules(cfg, "coloring_pages", bl)
    g = rules.grams("Anxiety Relief Mandala Coloring Pages")
    assert "mandala" in g.keys.values() and not g.blocked
    assert not [k for k in g.keys.values() if "relief" in k or "anxiety relief" in k]
    g = rules.grams("Stress Relief Mandala Coloring Pages")     # "stress relief" is a descriptive term
    assert "mandala" in g.keys.values() and not g.blocked
    g = rules.grams("Mood Booster Ocean Coloring Pages")
    assert "ocean" in g.keys.values() and "booster" not in " ".join(g.keys.values())
    rules = niche_text_rules(cfg, "stationery", bl)
    g = rules.grams("Health Planner Printable, Medication Tracker, Blood Pressure Log")
    assert g.blocked and not {"medication", "blood pressure", "blood"} & set(g.keys.values())
    g = rules.grams("Meditation Journal Printable, Stress Log, Sleep Tracker")
    assert not g.blocked and not g.flagged and "meditation" in " ".join(g.keys.values())
    g = rules.grams("Period Tracker Printable")
    assert g.flagged and not g.blocked
    assert "stress relief" not in RESEARCH["medical_terms"] and "anxiety relief" in RESEARCH["medical_terms"]
    assert "pain relief" in RESEARCH["medical_terms"]


# ------------------------------------------------------------------ review fixes (2026-10-17)

def test_a_claim_broken_over_a_line_is_still_a_claim(cfg):
    assert health.claim_problems("These pages reduces\nanxiety") == ["reduces anxiety"]
    assert medical("Our planner helps\r\nmanage your ADHD", cfg)


@pytest.mark.parametrize("text", [
    "Matching game to improve memory and concentration", "Maze puzzles that build focus and attention",
    "Spot the difference: boost attention to detail",
])
def test_kids_learning_copy_is_not_a_health_claim(text, cfg):
    assert health.claim_problems(text) == [], text
    assert medical(text, cfg) == [], text


@pytest.mark.parametrize("text", ["improve your focus", "boost your concentration", "sharpen your memory"])
def test_cognitive_promises_with_a_determiner_are_claims(text):
    assert health.claim_problems(text), text


def test_medicine_words_with_ordinary_uses_only_block_as_phrases(bl, settings):
    r = bl.check("Fuel Injection Car Stickers")
    assert not r.blocked and not r.flagged
    r = bl.check("Injection Log for weekly shots")
    assert [bl.label(t) for t in r.blocked] == ["injection log (health topic)"]
    # bare "disease" waits for review (game diseases on TTRPG sheets); named diseases and trackers block
    r = bl.check("Disease and Poison tracker")
    assert not r.blocked and bl.labels(r.flagged) == ["disease (health topic)"]
    assert "chronic disease (health topic)" in bl.labels(bl.check("Chronic Disease Journal").blocked)
    assert bl.check("Heart Disease Log").blocked


def test_term_files_saved_with_a_byte_order_mark_load_their_first_line(tmp_path):
    tm = tmp_path / "tm.txt"
    tm.write_text("Acme\n", encoding="utf-8")
    hf = tmp_path / "health.txt"
    hf.write_text("blood pressure\n?pregnancy\n", encoding="utf-8-sig")   # Notepad's UTF-8
    b = Blocklist.load(tm, health_path=hf)
    assert b.check("Blood Pressure Log").blocked == ["blood pressure"]
    assert b.is_health("blood pressure") and b.is_health("pregnancy")
    tm.write_text("# comment\nAcme\n", encoding="utf-8-sig")
    assert [t for t, _ in Blocklist.load(tm, health_path=False).hard] == ["Acme"]


def test_user_decisions_2026_10_17_awareness_review_and_doctor_stickers_clean():
    from core.compliance import Blocklist
    bl = Blocklist.load("config/trademark_blocklist.txt")
    for text in ("Mental Health Awareness Stickers", "Autism Awareness Month Planner", "Awareness Stickers Pack"):
        r = bl.check(text)
        assert not r.blocked and r.flagged, text
    for text in ("Cute Doctor Stickers", "Nurse Stethoscope Sticker Pack", "Kawaii Nurse Planner Stickers"):
        r = bl.check(text)
        assert not r.blocked and not r.flagged, text
    assert bl.check("Pink Ribbon Stickers").blocked
