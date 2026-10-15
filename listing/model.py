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
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

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
- Do not say the designs are handmade, hand-drawn, hand-painted or made without AI. Do not offer \
commercial use or resale rights.
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


def system_prompt(cfg: dict) -> str:
    uses = cfg["suggested_uses"]
    return SYSTEM.format(title_max=cfg["title_max_chars"], tags_count=cfg["tags_count"],
                         tag_max=cfg["tag_max_chars"], hook_max=cfg["hook_max_chars"],
                         about_max=cfg["about_max_chars"], uses_min=uses["min"], uses_max=uses["max"],
                         use_max=uses["max_chars"], alt_max=cfg["alt_text_max_chars"])


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


def write(client, facts: dict, cfg: dict, *, model: str, max_tokens: int, blocklist: Blocklist) -> ModelResult:
    """Ask for a draft, re-asking with the problems found, up to 1 + max_retries calls.
    A blocked term in any draft stops at once with ListingComplianceError: we never
    rewrite our way around a trademark hit; a person looks at the item instead."""
    # Each retry is a fresh single-turn request carrying the previous draft and its
    # problems, rather than a replayed conversation: no assistant turns (or thinking
    # blocks) are sent back, so requests stay independent and simple to fake in tests.
    n_images = len(facts["images"])
    result = ModelResult(draft=None, errors=["no usable response from the model"])
    feedback = None
    for attempt in range(1, 2 + int(cfg["max_retries"])):
        response = call(client, cfg, model=model, max_tokens=int(max_tokens), system=system_prompt(cfg),
                        messages=[{"role": "user", "content": user_message(facts, feedback)}])
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
            found = rules.check_each(blocklist, rules.texts(draft))
            if found.blocked:
                raise ListingComplianceError(
                    f"model draft contains blocked terms: {', '.join(found.blocked)}", found.blocked)
            errors = rules.validate(draft, n_images, cfg)
            if result.draft is None or len(errors) < len(result.errors):
                result.draft, result.errors, result.model = draft, errors, served
        result.history.append(errors)
        if draft is not None and not errors:
            break
        log.info("listing draft attempt %d has %d problems: %s", attempt, len(errors), "; ".join(errors[:5]))
        feedback = (draft, errors)
    return result
