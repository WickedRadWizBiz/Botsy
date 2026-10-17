"""subjects(): coherent subjects for a theme from the text model, blocklist-checked, deduped and
topped up from the built-in tables; dry runs use the tables only (docs/PACKS.md section 3)."""
import importlib
import json
from types import SimpleNamespace

import pytest

from core import spend
from core.compliance import Blocklist
from core.spend import BudgetExceeded
from generators.imaging.subjects import subject_pool
from generators.packs import plan_subjects, subjects

subjects_mod = importlib.import_module("generators.packs.subjects")


def response(answers, *, stop="end_turn", text=None, usage=True):
    body = text if text is not None else json.dumps({"subjects": answers})
    u = SimpleNamespace(input_tokens=420, output_tokens=180, cache_creation_input_tokens=0,
                        cache_read_input_tokens=0, iterations=None) if usage else None
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=body)], stop_reason=stop,
                           model="claude-opus-5-5", usage=u)


class FakeAnthropic:
    """Records every request; answers with the queued responses (or raises them)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **params):
        self.calls.append(params)
        a = self.answers.pop(0)
        if isinstance(a, BaseException):
            raise a
        return a


@pytest.fixture
def blocklist(tmp_path):
    path = tmp_path / "blocklist.txt"
    path.write_text("# test blocklist\nPikachu\n!Dungeons & Dragons\n!DnD\n?Peter Rabbit\n", encoding="utf-8")
    return Blocklist.load(path)


@pytest.fixture
def ledger(monkeypatch):
    """core.spend calls, in order (no real ledger is read or written)."""
    calls = []
    monkeypatch.setattr(spend, "check_budget", lambda kind, settings=None: calls.append(("check", kind)))
    monkeypatch.setattr(spend, "record", lambda *a, **k: calls.append(("record", a, k)))
    return calls


def test_dry_run_uses_only_the_tables(settings, ledger):
    client = FakeAnthropic()
    got = subjects("farm animals", "coloring_pages", 12, settings=settings, client=client, dry_run=True)
    assert client.calls == [] and ledger == []
    assert got == subject_pool("farm animals")[:12] and len(set(got)) == 12


def test_model_subjects_are_checked_deduplicated_and_topped_up(settings, ledger, blocklist):
    client = FakeAnthropic(response([
        "a beach ball", "A beach ball.", "a sandcastle with a flag", "a Pikachu on a surfboard",
        "a Dungeons & Dragons dice tower", "a DnD map", "a sunhat like Peter Rabbit's", "see www.example.com",
        "an ice cream cone", "x" * 120, "", 7,
    ]))
    plan = plan_subjects("summer", "coloring_pages", 6, settings=settings, client=client, blocklist=blocklist,
                         avoid=["an ice cream cone"])
    assert plan.subjects[:2] == ["a beach ball", "a sandcastle with a flag"]
    assert len(plan.subjects) == 6 and len({s.lower() for s in plan.subjects}) == 6
    assert plan.source == "model+tables" and plan.model == "claude-opus-5-5"
    joined = " ".join(plan.subjects).lower()
    for bad in ("pikachu", "dungeons", "dnd", "peter rabbit", "example.com", "ice cream"):
        assert bad not in joined, bad                                   # "!" game-system names count as blocked
    assert len(plan.dropped) >= 6
    assert all(s in subject_pool("summer") for s in plan.subjects[2:])  # the top-up comes from the tables


def test_the_request_holds_only_our_theme_niche_audience_and_rules(settings, ledger):
    client = FakeAnthropic(response([f"a sunny thing {i}" for i in range(10)]))
    subjects("summer", "stickers", 4, settings=settings, client=client, audience="Kids ages 4-8 who love Bluey",
             keywords=["summer stickers like Bluey"], item_id="2026-10-15-stickers-summer-0000")
    (params,) = client.calls
    assert params["model"] == settings["models"]["text"]["model"] == "claude-opus-5-5"
    assert params["betas"] == ["server-side-fallback-2026-07-01"] and params["fallbacks"] == "default"
    assert params["output_config"]["format"]["type"] == "json_schema"
    assert params["output_config"]["format"]["schema"]["required"] == ["subjects"]
    assert params["output_config"]["effort"] == "low" and "thinking" not in params
    user = json.loads(params["messages"][0]["content"])
    assert set(user) == {"theme", "product", "audience", "count", "already_used"}
    assert user["theme"] == "summer" and user["audience"] == "kids ages 4-8" and user["count"] == 4 + 6
    text = json.dumps(params)
    assert "Bluey" not in text and "stickers like" not in text         # brief free text never reaches the model
    assert "brands" in params["system"] and "kids" in params["system"]
    # Spend: the budget is checked before the paid request and the request is recorded after it.
    assert ledger[0] == ("check", "text")
    (_, args, kw) = ledger[1]
    assert args == ("anthropic", "claude-opus-5-5", "text") and kw["stage"] == "generate"
    assert kw["input_tokens"] == 420 and kw["output_tokens"] == 180
    assert kw["item_id"] == "2026-10-15-stickers-summer-0000"


def test_grown_up_audiences_get_the_all_ages_rule(settings, ledger):
    client = FakeAnthropic(response([f"a leaf {i}" for i in range(10)]))
    subjects("autumn", "stationery", 3, settings=settings, client=client, audience="adults")
    assert "suitable for all ages" in client.calls[0]["system"]


def test_the_spending_cap_stops_the_request(settings, monkeypatch):
    def cap(kind, settings=None):
        raise BudgetExceeded("cap reached", kind=kind, month="2026-10", mtd=25.0, cap=25.0)

    monkeypatch.setattr(spend, "check_budget", cap)
    client = FakeAnthropic(response(["a beach ball"]))
    with pytest.raises(BudgetExceeded):
        subjects("summer", "coloring_pages", 3, settings=settings, client=client)
    assert client.calls == []


@pytest.mark.parametrize("answer", [
    response([], stop="refusal"),
    response([], text="not json"),
    response([], text='{"subjects": "a ball"}'),
    RuntimeError("connection reset"),
])
def test_a_failed_or_declined_request_falls_back_to_the_tables(settings, ledger, monkeypatch, answer):
    monkeypatch.setattr(subjects_mod, "API_ERRORS", (RuntimeError,))
    plan = plan_subjects("farm animals", "coloring_pages", 5, settings=settings, client=FakeAnthropic(answer))
    assert plan.source == "tables" and plan.subjects == subject_pool("farm animals")[:5]


def test_no_api_key_or_model_turned_off_uses_the_tables(settings, ledger):
    plan = plan_subjects("farm animals", "coloring_pages", 5, settings=settings)    # conftest: no key
    assert plan.source == "tables" and ledger == []
    settings["packs"] = {"subjects": {"use_model": False}}
    client = FakeAnthropic()
    assert subjects("farm animals", "coloring_pages", 5, settings=settings, client=client) == plan.subjects
    assert client.calls == []


def test_the_refusal_fallback_can_be_turned_off(settings, ledger):
    settings["packs"] = {"subjects": {"refusal_fallback": False}}
    client = FakeAnthropic(response(["a cow", "a pig", "a hen"]))
    assert subjects("farm animals", "coloring_pages", 3, settings=settings, client=client) == ["a cow", "a pig",
                                                                                                 "a hen"]
    assert "betas" not in client.calls[0] and "fallbacks" not in client.calls[0]


def test_too_few_distinct_subjects_is_an_error(settings, monkeypatch):
    monkeypatch.setattr(subjects_mod, "subject_pool", lambda theme, keywords=(): ["a cow", "a Cow", "the cow"])
    with pytest.raises(ValueError, match="distinct subjects"):
        subjects("cows", "coloring_pages", 2, settings=settings, dry_run=True)


def test_a_minimum_takes_fewer_subjects_when_the_tables_run_out(settings, ledger, monkeypatch):
    pool = ["a beach ball", "a sandcastle", "a sailboat"]
    monkeypatch.setattr(subjects_mod, "subject_pool", lambda theme, keywords=(): list(pool))
    plan = plan_subjects("summer", "stickers", 5, settings=settings, dry_run=True, minimum=3)
    assert plan.subjects == pool and plan.source == "tables"
    with pytest.raises(ValueError, match="3 distinct subjects; 4 were asked for"):
        plan_subjects("summer", "stickers", 5, settings=settings, dry_run=True, minimum=4)
