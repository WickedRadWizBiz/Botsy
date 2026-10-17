"""Listing copy from the text model: Anthropic Messages API with structured JSON output.

The request carries only our product facts (listing/facts.py). The response is
constrained to SCHEMA with `output_config.format`, then checked by listing.rules. When a
draft breaks a rule we ask again with the list of problems (`max_retries` times); after
that the caller repairs the best draft deterministically.

API shape verified against the installed anthropic 1.14.0 SDK: `messages.create(...,
output_config={"format": {"type": "json_schema", "schema": ...}, "effort": ...})`, and
`beta.messages.create(..., betas=[...], fallbacks="default")` for the server-side refusal
fallback. Claude Opus 5.5 always thinks (no `thinking` param needed or allowed off), and
thinking tokens count toward `max_tokens`, which is why `effort` defaults to "low": this
is short, rule-bound copy and the code checks every rule anyway.

Spending (docs/SERVER.md section 10): every request is preceded by core.spend.check_budget
("text"), whose BudgetExceeded propagates (no retry, no template copy), and followed by
core.spend.record() of `response.usage` (output_tokens includes thinking tokens): one line
per model that ran, so a declined attempt before a fallback is counted too.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from core import spend
from core.compliance import Blocklist
from listing import rules
from listing.errors import ListingComplianceError

log = logging.getLogger("listing.model")

try:  # the SDK's request failures (status, connection, timeout) after its own retries
    from anthropic import APIError
    API_ERRORS: tuple = (APIError,)
except ImportError:  # pragma: no cover - anthropic is in requirements.txt
    API_ERRORS = ()

FALLBACK_BETA = "server-side-fallback-2026-07-01"  # pairs with fallbacks="default"

_STR = {"type": "string"}
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Etsy listing title"},
        "tags": {"type": "array", "items": _STR, "description": "Etsy search tags, lowercase"},
        "hook": {"type": "string", "description": "one or two opening sentences of the description"},
        "about": {"type": "string", "description": "two to four sentences about the designs"},
        "suggested_uses": {"type": "array", "items": _STR, "description": "short lines, ways to use it"},
        "alt_texts": {"type": "array", "items": _STR, "description": "one per image, same order"},
    },
    "required": ["title", "tags", "hook", "about", "suggested_uses", "alt_texts"],
    "additionalProperties": False,
}

SYSTEM = """You write Etsy listing copy for digital downloads that our shop designed and made. \
You receive JSON facts about one of our products. Write from those facts only: never invent \
counts, sizes, file types or contents.

Return JSON with these fields:
- title: at most {title_max} characters, ideally under 15 words. Start with the main phrase a \
shopper would search for, then the most useful details (who it is for, how many, format). It must \
start with a letter or number and use only letters, numbers, spaces and ordinary punctuation; each \
of % : & + at most once; no emoji and no trademark or copyright symbols.
- tags: exactly {tags_count} different search phrases, lowercase, each at most {tag_max} characters \
including spaces, using only letters, numbers, spaces, hyphens and apostrophes. Prefer multi-word \
phrases a shopper would type. Cover the theme, the product type, the audience and occasions. Do not \
use the whole title as a tag.
- hook: one or two sentences (at most {hook_max} characters) that open the description.
- about: two to four sentences (at most {about_max} characters) about the designs and who they suit.
- suggested_uses: {uses_min} to {uses_max} short lines (each at most {use_max} characters) with ways \
to use the product at home.
- alt_texts: exactly one entry per item in "images", in the same order, each one or two plain \
sentences (at most {alt_max} characters) describing what that photo shows, for a shopper using a \
screen reader. Return an empty list when there are no images.

Rules:
- Never name or hint at brands, companies, trademarked or licensed characters, franchises, films, \
TV shows, games, toys, apps or celebrities, and never write "in the style of".
- No URLs, email addresses, social media handles or shop names.
- Do not say the designs are handmade, hand-drawn, hand-painted or made without AI, or that the product \
is official, licensed or endorsed. Do not offer commercial use or resale rights.
- No medical or therapeutic claims: never use the words treat, cure, heal, diagnose, therapy, \
therapeutic, clinically, medical, prescription or symptom relief (or their other forms). "ADHD-friendly" \
and "for ADHD brains" may describe a layout; never say a product helps a condition.
- When the facts list "page_types", the pack mixes those kinds of pages: name only page types listed \
there, and never write the title as if every page were one kind (not "Character Sheet, 20 Pages" for \
a pack with 2 character sheets).
- Do not mention prices, discounts or shipping, and do not write download steps, terms of use or an \
AI disclosure: those are added separately.
- Plain US English. No emoji, markdown or HTML."""


@dataclass
class ModelResult:
    draft: dict | None          # the valid draft, or the one with the fewest problems
    errors: list[str]           # problems left in `draft` (empty when it passed)
    attempts: int = 0
    model: str | None = None    # model that served the response `draft` came from
    history: list[list[str]] = field(default_factory=list)  # problems per attempt


GAME_SYSTEM_RULE = """

Game system exception (this product only): the facts name the tabletop game system the sheets are \
made for ("game_system", with the allowed spellings in "game_system_names"). You may name that system, \
using only those spellings, as a compatibility reference in the title (for example "... for {label}"), \
the tags, the hook and the about text. Never name it in alt_texts. Never name the system's publisher or \
trademark owner (the disclaimer added separately names it), and never name any other game, edition, \
publisher, setting, place, creature or character from any game. Never suggest the product is official, \
licensed or endorsed: it is our own unofficial design."""


def system_prompt(cfg: dict, facts: dict | None = None) -> str:
    uses = cfg["suggested_uses"]
    text = SYSTEM.format(title_max=cfg["title_max_chars"], tags_count=cfg["tags_count"],
                         tag_max=cfg["tag_max_chars"], hook_max=cfg["hook_max_chars"],
                         about_max=cfg["about_max_chars"], uses_min=uses["min"], uses_max=uses["max"],
                         use_max=uses["max_chars"], alt_max=cfg["alt_text_max_chars"])
    if facts and facts.get("game_system"):
        text += GAME_SYSTEM_RULE.format(label=facts["game_system"])
    return text


def user_message(facts: dict, feedback: tuple[dict | None, list[str]] | None = None) -> str:
    msg = "Product facts:\n" + json.dumps(facts, indent=2, ensure_ascii=False) + "\n\nWrite the listing."
    if feedback:
        previous, errors = feedback
        msg += "\n\nYour previous draft failed these checks:\n" + "\n".join(f"- {e}" for e in errors)
        if previous is not None:
            msg += "\n\nPrevious draft:\n" + json.dumps(previous, indent=2, ensure_ascii=False)
        msg += "\n\nReturn a complete, corrected listing that passes every check."
    return msg


def call(client, cfg: dict, **params):
    """One Messages API request, with the server-side refusal fallback when enabled."""
    output_config = {"format": {"type": "json_schema", "schema": SCHEMA}}
    if cfg.get("effort"):
        output_config["effort"] = cfg["effort"]
    params["output_config"] = output_config
    if cfg.get("refusal_fallback"):
        return client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params)
    return client.messages.create(**params)


def response_text(response) -> str:
    """The JSON text of a response. With a fallback, text before a `fallback` block came
    from the model that declined, so only the text after the last one counts."""
    parts: list[str] = []
    for block in getattr(response, "content", None) or []:
        kind = getattr(block, "type", None)
        if kind == "fallback":
            parts = []
        elif kind == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts)


def record_usage(response, model: str, *, settings: dict | None = None, item_id: str | None = None) -> None:
    """Add one answered request to the spend ledger: one line per model that ran (normally
    one), each priced at that model's rates. A response without `usage` (the test fakes)
    records nothing. Never raises: the call is already paid for, and dropping its draft over
    a ledger problem would only pay again."""
    try:
        attempts = billed_attempts(response, model)
    except Exception:  # an odd usage shape must not lose the paid draft either
        log.exception("could not read the token usage of the listing request for the spend ledger")
        return
    for served, input_tokens, output_tokens in attempts:
        try:
            spend.record("anthropic", served, "text", stage="listing", item_id=item_id, input_tokens=input_tokens,
                         output_tokens=output_tokens, settings=settings)
        except Exception:  # never lose a paid draft over the ledger (see docstring)
            log.exception("could not add the listing request to the spend ledger")


def billed_attempts(response, model: str) -> list[tuple[str, int, int]]:
    """[(model, input_tokens, output_tokens)] for one response, one entry per model that ran.

    With the server-side refusal fallback, top-level `usage` covers only the attempt that
    produced the returned message; `usage.iterations` lists every attempt (the declined one
    as a `message` entry, usually without a model, so the requested model; the fallback as
    `fallback_message`). A declined attempt is billed when it declined mid-response or in
    some categories; it is always counted here, so the estimate can only run high.
    No cache_control is sent, so the cache counts are 0; any that appear are counted at the
    full input price. output_tokens already includes thinking tokens."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return []
    served = getattr(response, "model", None) or model
    top = (_input_tokens(usage), _tokens(usage, "output_tokens"))
    per_model: dict[str, list[int]] = {}
    for it in getattr(usage, "iterations", None) or []:
        name = getattr(it, "model", None)
        name = name if isinstance(name, str) and name.strip() else model
        counts = per_model.setdefault(name, [0, 0])
        counts[0] += _input_tokens(it)
        counts[1] += _tokens(it, "output_tokens")
    # Iterations must cover at least the top-level counts; if not (a format change), trust the top level.
    totals = [sum(c[0] for c in per_model.values()), sum(c[1] for c in per_model.values())]
    if per_model and totals[0] >= top[0] and totals[1] >= top[1]:
        return [(name, i, o) for name, (i, o) in per_model.items()]
    return [(served, *top)]


def _input_tokens(usage) -> int:
    return sum(_tokens(usage, k) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def _tokens(usage, name: str) -> int:
    value = getattr(usage, name, None)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def write(client, facts: dict, cfg: dict, *, model: str, max_tokens: int, blocklist: Blocklist,
          settings: dict | None = None, item_id: str | None = None, allow_systems: tuple[str, ...] = (),
          alt_names=None, owner_names=None, absent_types=None) -> ModelResult:
    """Ask for a draft, re-asking with the problems found, up to 1 + max_retries calls.
    A blocked term in any draft stops at once with ListingComplianceError: we never
    rewrite our way around a trademark hit; a person looks at the item instead.

    `allow_systems` (a ttrpg item made for a game system) lets that system's names through
    the blocklist; `alt_names(text)` finds them, and one in an alt text is a rule problem
    the model is asked to fix (alt text never names a system). `owner_names(text)` finds
    the system's trademark owner, a rule problem in the title or tags. `absent_types(text)`
    finds page types the pack does not include, a rule problem in the title.

    `settings` and `item_id` are for the spend ledger (settings None = config/settings.yaml).
    core.spend.BudgetExceeded from the check before a request propagates as is."""
    # Each retry is a fresh single-turn request carrying the previous draft and its
    # problems, rather than a replayed conversation: no assistant turns (or thinking
    # blocks) are sent back, so requests stay independent and simple to fake in tests.
    n_images = len(facts.get("images") or [])
    result = ModelResult(draft=None, errors=["no usable response from the model"])
    feedback = None
    for attempt in range(1, 2 + int(cfg["max_retries"])):
        spend.check_budget("text", settings=settings)  # before every paid request
        response = call(client, cfg, model=model, max_tokens=int(max_tokens), system=system_prompt(cfg, facts),
                        messages=[{"role": "user", "content": user_message(facts, feedback)}])
        record_usage(response, model, settings=settings, item_id=item_id)
        result.attempts = attempt
        served = getattr(response, "model", None) or model  # with a fallback, the model that answered
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            log.warning("model declined the listing request (%s); using %s", getattr(details, "category", None)
                        or "no category", "the best earlier draft" if result.draft else "the template copy")
            result.history.append(["request declined"])
            break

        draft, errors = None, []
        try:
            draft = rules.normalize_draft(json.loads(response_text(response)))
        except (ValueError, TypeError) as e:  # JSONDecodeError and DraftShapeError are ValueErrors
            errors = [f"the response was not the requested JSON object: {e}"]
            if stop == "max_tokens":
                errors.append("the response was cut off; keep every field shorter")
        if draft is not None:
            found = blocklist.check(*rules.texts(draft), allow_systems=allow_systems)  # each field on its own
            if found.blocked:
                raise ListingComplianceError(
                    f"model draft contains blocked terms: {', '.join(found.blocked)}", found.blocked)
            errors = rules.validate(draft, n_images, cfg, alt_names=alt_names, owner_names=owner_names,
                                    absent_types=absent_types)
            if result.draft is None or len(errors) < len(result.errors):
                result.draft, result.errors, result.model = draft, errors, served
        result.history.append(errors)
        if draft is not None and not errors:
            break
        log.info("listing draft attempt %d has %d problems: %s", attempt, len(errors), "; ".join(errors[:5]))
        feedback = (draft, errors)
    return result
