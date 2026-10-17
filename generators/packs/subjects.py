"""Subject planning for coherent themed packs (docs/PACKS.md section 3).

    subjects(theme, niche, n, *, settings, client=None, dry_run=False) -> list[str]

n distinct subjects for one theme ("summer" -> a beach ball, a sandcastle, an ice cream
cone, a sunflower, a sailboat, ...). In a real run the text model (models.text.model,
claude-opus-5-5) proposes them with structured JSON output; the request holds only our own
theme name, the niche, a fixed audience phrase and the rules (no brands, characters or
franchises; kid-friendly for kids), plus our own subjects to avoid repeating. Every answer
is checked against the trademark blocklist (game-system "!" names count as blocked here:
designs never carry them), deduplicated, and topped up from the built-in subject tables
(generators/imaging/subjects.py). Dry runs use only the tables, and so does a run without
ANTHROPIC_API_KEY, with packs.subjects.use_model false, or when the model fails or declines.

Spending (docs/SERVER.md section 10): core.spend.check_budget("text") before the request
(BudgetExceeded and LedgerUnavailable propagate: the cap stops paid work), then one ledger
line per model that ran (stage "generate").

API shape as in listing/model.py (anthropic 1.14): messages.create(..., output_config=
{"format": {"type": "json_schema", "schema": ...}, "effort": "low"}), or beta.messages.create(
betas=["server-side-fallback-2026-07-01"], fallbacks="default", ...) with the refusal fallback.
Claude Opus 5.5 always thinks; thinking tokens count toward max_tokens and as output tokens.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from core import spend
from core.compliance import Blocklist, find_urls, normalize
from core.config import MissingSecretError, require_env
from generators.imaging.prompts import audience_phrase, clean_text
from generators.imaging.subjects import subject_pool
from generators.packs.model import config

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"  # pairs with fallbacks="default"
DEFAULT_MODEL = "claude-opus-5-5"

try:  # the SDK's request failures (status, connection, timeout) after its own retries
    from anthropic import APIError
    API_ERRORS: tuple = (APIError,)
except ImportError:  # pragma: no cover - anthropic is in requirements.txt
    API_ERRORS = ()

SCHEMA = {
    "type": "object",
    "properties": {"subjects": {"type": "array", "items": {"type": "string"},
                                "description": "short descriptions of one thing to draw each, all different"}},
    "required": ["subjects"],
    "additionalProperties": False,
}

NICHE_HINTS = {
    "coloring_pages": "coloring pages: one subject per page, drawn as black and white line art with roomy "
                      "spaces to color",
    "stickers": "die-cut stickers: one subject per sticker that still reads well small, flat colors",
    "activity_sheets": "children's activity pages (mazes, word searches, tracing, dot-to-dot) about the subject",
    "stationery": "small decorative line-art accents for printable planner pages",
    "ttrpg": "small decorative line-art accents for printable tabletop role-playing game sheets",
}

SYSTEM = """You plan the subjects for one themed pack of our shop's own original printable designs. \
You receive JSON with our theme, the kind of product, the audience, how many subjects we need and \
subjects we already used.

Return JSON {{"subjects": [...]}} with exactly the number of subjects asked for.

Rules:
- Each subject is a short, concrete description of one thing to draw, a few words long (for \
example "a sandcastle with a flag"). Every subject fits the theme and is clearly different from \
every other subject and from the ones already used.
- Original, generic subjects only. Never name or hint at brands, companies, trademarked or \
licensed characters, franchises, films, TV shows, books, video games, tabletop role-playing games \
or their official settings, creatures or characters, toys, apps, logos or celebrities, and never \
write "in the style of".
- No words, letters or numbers drawn in the design, and no URLs.
- {audience_rule}
- Plain US English, lowercase, no emoji."""

KIDS_RULE = ("The audience is kids: every subject is friendly, gentle and age-appropriate "
             "(nothing scary, violent, unsafe or grown-up).")
GROWN_RULE = "Keep every subject friendly and suitable for all ages."

_LEADING_ARTICLE = re.compile(r"^(?:a|an|the)\s+")


@dataclass
class SubjectPlan:
    subjects: list[str]
    source: str = "tables"                 # "model", "model+tables" or "tables"
    model: str | None = None
    dropped: list[str] = field(default_factory=list)   # model answers left out (blocked, review terms, repeats)


def subject_key(subject: str) -> str:
    """How two subjects are compared: normalized, without a leading article or trailing punctuation."""
    text = normalize(str(subject)).strip().rstrip(".!,;:")
    return _LEADING_ARTICLE.sub("", text).strip()


def subjects(theme: str, niche: str, n: int, *, settings: dict, client=None, dry_run: bool = False,
             audience=None, keywords=(), avoid=(), item_id: str | None = None,
             blocklist: Blocklist | None = None) -> list[str]:
    """n distinct subjects for the theme (see module docstring)."""
    return plan_subjects(theme, niche, n, settings=settings, client=client, dry_run=dry_run, audience=audience,
                         keywords=keywords, avoid=avoid, item_id=item_id, blocklist=blocklist).subjects


def plan_subjects(theme: str, niche: str, n: int, *, settings: dict, client=None, dry_run: bool = False,
                  audience=None, keywords=(), avoid=(), item_id: str | None = None,
                  blocklist: Blocklist | None = None, minimum: int | None = None) -> SubjectPlan:
    """Up to n distinct subjects; fewer (but at least `minimum`, default n) when the model and
    the tables run out. ValueError below `minimum`."""
    if n < 1:
        raise ValueError(f"need at least one subject, not {n}")
    minimum = n if minimum is None else max(1, min(int(minimum), n))
    theme = clean_text(theme, 80)
    if not theme:
        raise ValueError("no theme to plan subjects for")
    blocklist = blocklist or Blocklist.load((settings.get("compliance") or {}).get("trademark_blocklist"), health_path=(settings.get("compliance") or {}).get("health_terms"))
    cfg = config(settings)["subjects"]
    max_chars = int(cfg.get("max_chars", 80))
    taken = {subject_key(a) for a in avoid if isinstance(a, str)}
    out: list[str] = []
    plan = SubjectPlan(out)

    def add(subject, from_model: bool) -> None:
        text = clean_text(subject, 400).strip().rstrip(".!,;:")
        key = subject_key(text)
        reason = None
        if not key or len(text) > max_chars:
            reason = "empty or too long"
        elif key in taken:
            reason = "repeat"
        elif find_urls(text):
            reason = "URL"
        else:
            found = blocklist.check(text)
            if found.blocked or found.flagged:
                reason = "blocklist"
        if reason:
            if from_model:
                plan.dropped.append(f"{text[:60]} ({reason})")
            return
        taken.add(key)
        out.append(text)

    model_used = False
    if not dry_run and cfg.get("use_model", True):
        answers = _ask_model(theme, niche, n + int(cfg.get("extra", 6)), settings=settings, client=client,
                             audience=audience, avoid=sorted(taken)[:60], item_id=item_id, plan=plan)
        if answers is not None:
            model_used = True
            for s in answers:
                if len(out) < n:
                    add(s, True)
    from_model = len(out)
    if len(out) < n:
        for s in subject_pool(theme, [clean_text(k, 80) for k in keywords if isinstance(k, str)]):
            if len(out) >= n:
                break
            add(s, False)
    if len(out) < minimum:
        raise ValueError(f"theme {theme!r} has only {len(out)} distinct subjects; {minimum} were asked for")
    plan.source = ("model" if from_model == len(out) else "model+tables" if from_model else "tables") \
        if model_used else "tables"
    if plan.dropped:
        log.info("subjects for %r: left out %d model answer(s): %s", theme, len(plan.dropped), plan.dropped[:5])
    return plan


def _make_client():
    import anthropic

    return anthropic.Anthropic(api_key=require_env("ANTHROPIC_API_KEY"))


def _ask_model(theme: str, niche: str, count: int, *, settings: dict, client, audience, avoid, item_id,
               plan: SubjectPlan) -> list[str] | None:
    """The model's subjects, or None when the tables must do (no key, API error, refusal,
    unreadable answer). BudgetExceeded / LedgerUnavailable propagate."""
    text_cfg = (settings.get("models") or {}).get("text") or {}
    model = str(text_cfg.get("model") or DEFAULT_MODEL)
    cfg = config(settings)["subjects"]
    if client is None:
        try:
            client = _make_client()
        except MissingSecretError as e:
            log.warning("subjects: %s; using the built-in subject tables", e)
            return None
    who = audience_phrase(audience)
    system = SYSTEM.format(audience_rule=KIDS_RULE if who.startswith("kids") else GROWN_RULE)
    user = json.dumps({"theme": theme, "product": NICHE_HINTS.get(niche, niche.replace("_", " ")),
                       "audience": who, "count": count, "already_used": list(avoid)}, indent=2)
    output_config = {"format": {"type": "json_schema", "schema": SCHEMA}}
    if cfg.get("effort"):
        output_config["effort"] = cfg["effort"]
    params = dict(model=model, max_tokens=int(cfg.get("max_tokens") or 8000), system=system,
                  messages=[{"role": "user", "content": user}], output_config=output_config)
    spend.check_budget("text", settings=settings)                 # before the paid request
    try:
        if cfg.get("refusal_fallback", True):
            response = client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params)
        else:
            response = client.messages.create(**params)
    except API_ERRORS as e:
        log.warning("subjects: the text model request failed (%s); using the built-in subject tables",
                    type(e).__name__)
        return None
    _record_usage(response, model, settings=settings, item_id=item_id)
    plan.model = getattr(response, "model", None) or model
    if getattr(response, "stop_reason", None) == "refusal":
        log.warning("subjects: the model declined the request; using the built-in subject tables")
        return None
    try:
        data = json.loads(_response_text(response))
        answers = data["subjects"]
        if not isinstance(answers, list):
            raise TypeError("subjects is not a list")
    except (ValueError, TypeError, KeyError) as e:
        log.warning("subjects: unreadable model answer (%s); using the built-in subject tables", e)
        return None
    return [a for a in answers if isinstance(a, str)]


def _response_text(response) -> str:
    """Text after the last `fallback` block (text before it came from a model that declined)."""
    parts: list[str] = []
    for block in getattr(response, "content", None) or []:
        kind = getattr(block, "type", None)
        if kind == "fallback":
            parts = []
        elif kind == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts)


def _tokens(usage, name: str) -> int:
    value = getattr(usage, name, None)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _input(usage) -> int:
    return sum(_tokens(usage, k) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def _record_usage(response, model: str, *, settings: dict, item_id: str | None) -> None:
    """One spend line per model that ran (usage.iterations with a fallback). Never raises:
    the answer is paid for either way."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    try:
        per_model: dict[str, list[int]] = {}
        for it in getattr(usage, "iterations", None) or []:
            name = getattr(it, "model", None)
            name = name if isinstance(name, str) and name.strip() else model
            counts = per_model.setdefault(name, [0, 0])
            counts[0] += _input(it)
            counts[1] += _tokens(it, "output_tokens")
        top = (_input(usage), _tokens(usage, "output_tokens"))
        if not per_model or sum(c[0] for c in per_model.values()) < top[0] \
                or sum(c[1] for c in per_model.values()) < top[1]:
            per_model = {getattr(response, "model", None) or model: list(top)}
    except Exception:  # an odd usage shape must not lose the paid answer
        log.exception("could not read the token usage of the subjects request for the spend ledger")
        return
    for name, (inp, outp) in per_model.items():
        try:
            spend.record("anthropic", name, "text", stage="generate", item_id=item_id, input_tokens=inp,
                         output_tokens=outp, settings=settings)
        except Exception:
            log.exception("could not add the subjects request to the spend ledger")
