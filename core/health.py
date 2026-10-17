"""The health claims rule: result, proof and authority claims our copy never makes.

CLAUDE.md: no medical or therapeutic claims. User decisions 2026-10-16/17 (docs/HEALTH_TERMS.md,
"Decided 2026-10-17"): medical TOPICS are blocked through config/health_terms.txt (loaded by
core.compliance.Blocklist); lifestyle topics (stress, sleep, mood, anxiety journals, food
diaries, ADHD planners, ...) are allowed when the copy describes the layout and the look.
What is never allowed, on any topic, is a promise of a health result, and that is what this
module finds:

1. Result claims in word order: an effect word, optionally "your/the/my/...", then a
   condition or symptom target ("reduces anxiety", "relieves migraine pain", "manage your
   ADHD symptoms", "improves sleep", "helps with ADHD"). Word order matters: "ADHD planner to
   manage your day" and "mood tracker to manage your month" name no target and pass.
2. Noun-form claims (NOUN_CLAIMS): "anxiety relief", "pain relief", "mood booster",
   "anti-anxiety", "for depression", "lose weight"; not "for anxiety": an anxiety journal
   is an allowed topic. Research cuts these from theme names
   (research/defaults.py medical_terms) and the listing rule refuses them.
3. Proof and authority claims (PROOF_CLAIMS): "clinically proven", "doctor recommended",
   "therapist approved", "FDA ...".

Allowed, because it describes the layout, the look or the topic: "stress relief coloring
pages" (user, 2026-10-17: "stress relief" is a descriptive term), "relaxing", "calm",
"calming ocean scenes", "anxiety journal with daily check-in pages", "food diary", "rate
your stress from 1 to 5", "helps you focus on your top 3 tasks".

claim_problems(text) returns the claim phrases found (normalized, lowercase); it is used by
listing.rules.medical, the stationery renderer and QA's printed-text check.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from core.compliance import Blocklist, normalize, read_terms
from core.config import CONFIG_DIR, resolve

# Effect words: each matches as written, with -s, -ed/-d and -ing. Extra spellings listed
# after the base ("combatting"). "calm" needs a determiner ("calm your nerves"): bare "calm
# mood" or "calming stress" would otherwise catch adjectives the catalog uses.
EFFECT_WORDS: tuple[str, ...] = (
    "relieve", "reduce", "lower", "ease", "soothe", "alleviate", "manage", "control", "fight", "beat",
    "overcome", "combat", "prevent", "regulate", "banish", "improve", "boost", "sharpen", "enhance",
    "treat", "cure", "heal", "fix", "eliminate", "stop", "help with", "help manage", "help reduce",
    "help relieve", "help ease", "help control",
)
_EXTRA_FORMS = ("combatting", "combatted", "controlling", "controlled", "stopping", "stopped", "fought",
                "overcame", "beaten")
_DETERMINER_ONLY = ("calm",)   # only "calm(s/ing) your/the/... <target>"

DETERMINERS: tuple[str, ...] = ("your", "the", "my", "their", "our", "his", "her", "any", "all", "daily",
                                "chronic", "severe", "bad", "racing", "big")

# Condition and symptom targets. "weight" is not one on purpose ("improve your weight
# lifting" is not a claim); "lose weight" is a fixed phrase below.
TARGETS: tuple[str, ...] = (
    "stress", "anxiety", "worry", "worries", "tension", "nerves", "panic", "overwhelm", "burnout",
    "depression", "pain", "aches", "symptom", "symptoms", "insomnia", "sleep", "blood pressure",
    "blood sugar", "cortisol", "cravings", "mood", "moods", "emotions", "mental health", "wellbeing",
    "well being", "adhd", "brain fog", "migraine",
    "migraines", "headache", "headaches", "fatigue", "anger", "condition", "conditions", "illness",
    "disease", "disorder", "disorders", "trauma", "grief", "ptsd", "ocd", "autism", "dyslexia",
    "diabetes", "cholesterol", "inflammation", "nausea",
)
# Cognitive targets count only after a determiner: "improve your focus" is a claim, while
# "a matching game to improve memory and concentration" is ordinary kids' activity copy.
DETERMINER_TARGETS: tuple[str, ...] = ("focus", "attention", "concentration", "memory")
# A target may carry one of these after it ("ADHD symptoms", "migraine pain", "stress levels").
_TAILS: tuple[str, ...] = ("symptoms", "symptom", "pain", "levels", "level", "attacks", "attack", "issues",
                           "problems", "disorder", "disorders", "flare ups", "flares")

# Noun-form and fixed-phrase claims. Research cuts these from theme names and keywords
# (research/defaults.py medical_terms) and listing.rules.medical refuses them. "stress
# relief" is NOT here (allowed, user 2026-10-17); "relieves stress" is still a result claim.
NOUN_CLAIMS: tuple[str, ...] = (
    "anxiety relief", "pain relief", "symptom relief", "symptoms relief", "relieves symptoms",
    "relieve symptoms", "stress reliever", "stress buster", "mood booster", "mood stabilizer",
    "anxiety reducer", "stress reduction", "anxiety reduction", "anti-anxiety", "anti-stress",
    "anti-depressant", "antidepressant", "for depression", "for insomnia", "for pain",
    "for symptoms", "for migraines", "lose weight", "burn fat", "fat burning", "weight loss results",
)
# Proof and authority claims.
PROOF_CLAIMS: tuple[str, ...] = (
    "clinically proven", "clinically tested", "scientifically proven", "proven to", "medically proven",
    "doctor recommended", "doctor approved", "doctor designed", "therapist approved", "therapist designed",
    "therapist recommended", "psychologist designed", "psychologist approved", "psychologist recommended",
    "evidence-based", "research-backed", "science-backed", "fda approved", "fda cleared", "fda registered",
    "fda",
)


def _forms(word: str) -> list[str]:
    """word, words, worded, wording (the e dropped before -ing/-ed)."""
    head, _, tail = word.rpartition(" ")
    stem = tail
    out = [stem, stem + "s"]
    out += [stem + "d", stem[:-1] + "ing"] if stem.endswith("e") else [stem + "ed", stem + "ing"]
    return [f"{head} {f}" if head else f for f in out]


def _verb_forms(words: tuple[str, ...]) -> list[str]:
    forms: list[str] = []
    for w in words:
        if " " in w:  # "help with": inflect the first word, keep the particle
            first, rest = w.split(" ", 1)
            forms += [f"{f} {rest}" for f in _forms(first)]
        else:
            forms += _forms(w)
    return forms


_GAP = r"[\s\-_]{1,3}"  # a line break inside a claim still joins it


def _alt(words) -> str:
    bodies = sorted({_GAP.join(map(re.escape, w.split())) for w in words}, key=lambda b: (-len(b), b))
    return "(?:" + "|".join(bodies) + ")"


def _claim_regex() -> re.Pattern:
    effects = _alt([*_verb_forms(EFFECT_WORDS), *_EXTRA_FORMS])
    det_only = _alt(_verb_forms(_DETERMINER_ONLY))
    det = _alt(DETERMINERS)
    target = _alt(TARGETS)
    tail = _alt(_TAILS)
    det_target = _alt(DETERMINER_TARGETS)
    body = (rf"(?:(?:{effects}(?:{_GAP}{det}){{0,2}}|{det_only}(?:{_GAP}{det}){{1,2}}){_GAP}{target}"
            rf"|(?:{effects}|{det_only})(?:{_GAP}{det}){{1,2}}{_GAP}{det_target})(?:{_GAP}{tail})?")
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])")


CLAIM_RE: re.Pattern = _claim_regex()


@lru_cache(maxsize=1)
def _phrase_list() -> Blocklist:
    return Blocklist([*NOUN_CLAIMS, *PROOF_CLAIMS])


def research_cut_terms() -> list[str]:
    """The noun-form and proof claims research cuts from theme names and keywords, with the
    plural spellings research's literal phrase matching needs."""
    out: list[str] = []
    for t in (*NOUN_CLAIMS, *PROOF_CLAIMS):
        out.append(t)
        if t.endswith(("reliever", "buster", "booster", "stabilizer", "reducer")):
            out.append(t + "s")
    return list(dict.fromkeys(out))


_TOPICS: dict[tuple, list[str]] = {}


def medical_topics(path: str | Path | None = None) -> list[str]:
    """The medical (hard) topics of config/health_terms.txt (`path`: settings
    compliance.health_terms), read again when the file changes; [] when the default file is
    missing. The listing stage treats these like its claim words (listing.rules.medical), so
    a model draft naming one is repaired instead of stopping as a trademark hit."""
    p = resolve(path) if path else CONFIG_DIR / "health_terms.txt"
    try:
        st = p.stat()
    except OSError:
        if path:
            raise
        return []
    key = (str(p), st.st_mtime_ns, st.st_size)
    if key not in _TOPICS:
        _TOPICS.clear()
        _TOPICS[key] = read_terms(p)[0]
    return list(_TOPICS[key])


def claim_problems(text: str) -> list[str]:
    """The health claims in `text`: result claims in word order ("reduces anxiety"),
    noun-form claims ("anxiety relief") and proof claims ("clinically proven"). Sorted,
    normalized phrases; [] when the text makes none."""
    if not text:
        return []
    norm = normalize(text)
    found = {re.sub(r"[\-_\s]+", " ", m.group(0)).strip() for m in CLAIM_RE.finditer(norm)}
    found.update(normalize(t).replace("-", " ") for t in _phrase_list().check(norm).blocked)
    return sorted(found)


def is_claim(text: str) -> bool:
    return bool(claim_problems(text))
