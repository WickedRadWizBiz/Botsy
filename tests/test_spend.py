"""core/spend.py: cost math, the append-only ledger, month grouping, the cap and warn states.
Every ledger lives in tmp_path."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

import pytest

from core import spend
from core.config import ROOT
from core.spend import BudgetExceeded

OCT = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh_warnings():
    for state in (spend._warned, spend._unwritable, spend._writable):
        state.clear()
    yield
    for state in (spend._warned, spend._unwritable, spend._writable):
        state.clear()


@pytest.fixture
def ledger(tmp_path):
    return tmp_path / "spend" / "ledger.jsonl"


@pytest.fixture
def settings(ledger) -> dict:
    return {"spend": {"ledger_file": str(ledger), "monthly_cap_usd": None, "warn_at": 0.8}}


def lines(path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def text(settings, cost_tokens=(0, 0), **kw):
    """A text call with these (input, output) tokens."""
    kw.setdefault("now", OCT)
    return spend.record("anthropic", "claude-opus-5-5", "text", stage="listing", input_tokens=cost_tokens[0],
                        output_tokens=cost_tokens[1], settings=settings, **kw)


def spend_dollars(settings, dollars: float, *, now=OCT, **kw):
    """Exactly `dollars` of Opus input (4 dollars per million tokens)."""
    tokens = round(dollars / 4 * 1_000_000)
    return text(settings, (tokens, 0), now=now, **kw)


# --- cost math ---------------------------------------------------------------------------------

def test_text_cost_per_million_tokens(settings):
    assert spend.estimate_cost("anthropic", "claude-opus-5-5", input_tokens=1_000_000, output_tokens=1_000_000,
                               settings=settings) == 24.0
    assert spend.estimate_cost("anthropic", "claude-opus-5-5", input_tokens=1234, output_tokens=567,
                               settings=settings) == pytest.approx(0.016276)
    assert spend.estimate_cost("anthropic", "claude-opus-5", input_tokens=1_000_000, output_tokens=1_000_000,
                               settings=settings) == 30.0


@pytest.mark.parametrize("size,price", [("1K", 0.0336), ("2K", 0.0504), ("4K", 0.1134), ("2k", 0.0504)])
def test_image_cost_per_image_size(settings, size, price):
    assert spend.estimate_cost("google", "gemini-nano-banana-2.1", images=1, image_size=size,
                               settings=settings) == pytest.approx(price)
    assert spend.estimate_cost("google", "gemini-nano-banana-2.1", images=3, image_size=size,
                               settings=settings) == pytest.approx(3 * price)


def test_image_cost_adds_prompt_and_thinking_tokens(settings):
    cost = spend.estimate_cost("google", "gemini-nano-banana-2.1", images=1, image_size="4K", input_tokens=100,
                               output_tokens=50, settings=settings)
    assert cost == pytest.approx(0.1134 + 100 * 1.5 / 1e6 + 50 * 7.5 / 1e6)


def test_unknown_model_or_size_uses_the_highest_listed_price(settings, caplog):
    caplog.set_level(logging.WARNING, logger="core.spend")
    cost = spend.estimate_cost("anthropic", "claude-new-model", input_tokens=1_000_000, settings=settings)
    assert cost == 10.0                                            # the highest listed: Fable 5.1
    # "claude-opus-5-7" is a new model, not a snapshot of Opus 5: highest price, not $5
    assert spend.estimate_cost("anthropic", "claude-opus-5-7", input_tokens=1_000_000, settings=settings) == 10.0
    assert spend.estimate_cost("google", "gemini-nano-banana-2.1", images=1, image_size="8K",
                               settings=settings) == pytest.approx(0.1134)
    assert spend.estimate_cost("google", "gemini-other", images=1, image_size="2K",
                               settings=settings) == pytest.approx(0.0504)
    text_ = caplog.text
    assert "claude-new-model" in text_ and "'8K'" in text_ and "gemini-other" in text_


@pytest.mark.parametrize("model,rate", [("claude-opus-5-5-20261001", 4.0), ("claude-opus-5-5@20261001", 4.0),
                                        ("claude-opus-5-20260601", 5.0), ("claude-sonnet-5-5", 2.0)])
def test_a_dated_snapshot_uses_its_models_price(settings, caplog, model, rate):
    caplog.set_level(logging.WARNING, logger="core.spend")
    assert spend.estimate_cost("anthropic", model, input_tokens=1_000_000, settings=settings) == rate
    assert spend.estimate_cost("google", "models/gemini-nano-banana-2.1", images=1, image_size="1K",
                               settings=settings) == pytest.approx(0.0336)
    assert caplog.text == ""                                       # a known model: no "no price" warning


def test_unknown_provider_costs_zero_and_logs_an_error(settings, caplog):
    caplog.set_level(logging.ERROR, logger="core.spend")
    assert spend.estimate_cost("openrouter", "x", input_tokens=10**6, settings=settings) == 0.0
    assert "no prices for 'openrouter'" in caplog.text


def test_price_table_comes_from_settings(settings):
    settings["spend"]["prices"] = {"anthropic": {"claude-opus-5-5": {"input_per_mtok": 1.0, "output_per_mtok": 2.0}},
                                   "google": {"gemini-nano-banana-2.1": {"per_image": 0.5}}}
    assert spend.estimate_cost("anthropic", "claude-opus-5-5", input_tokens=10**6, output_tokens=10**6,
                               settings=settings) == 3.0
    # a flat per-image price applies to every size; unset token rates keep the defaults
    assert spend.estimate_cost("google", "gemini-nano-banana-2.1", images=2, image_size="4K",
                               settings=settings) == 1.0


def test_bad_rate_counts_as_zero_and_logs(settings, caplog):
    settings["spend"]["prices"] = {"anthropic": {"claude-opus-5-5": {"input_per_mtok": "4$", "output_per_mtok": 20}}}
    caplog.set_level(logging.ERROR, logger="core.spend")
    assert spend.estimate_cost("anthropic", "claude-opus-5-5", input_tokens=10**6, output_tokens=10**6,
                               settings=settings) == 20.0
    assert "input_per_mtok" in caplog.text


def test_placeholder_price_is_logged(settings, caplog):
    settings["spend"]["prices"] = {"google": {"gemini-x": {"per_image": {"1K": 0.1}, "placeholder": True}}}
    caplog.set_level(logging.WARNING, logger="core.spend")
    assert spend.estimate_cost("google", "gemini-x", images=1, image_size="1K", settings=settings) == 0.1
    assert "placeholder" in caplog.text


# --- ledger lines ------------------------------------------------------------------------------

def test_record_appends_one_line_with_the_contract_fields(settings, ledger):
    cost = text(settings, (1234, 567), item_id="item-1")
    image = spend.record("google", "gemini-nano-banana-2.1", "image", stage="generate", item_id="item-1", images=1,
                         image_size="2K", input_tokens=10, settings=settings, now=OCT)
    assert cost == pytest.approx(0.016276) and image == pytest.approx(0.0504 + 10 * 1.5 / 1e6)
    first, second = lines(ledger)
    assert first == {"ts": "2026-10-16T12:00:00+00:00", "month": "2026-10", "provider": "anthropic",
                     "model": "claude-opus-5-5", "kind": "text", "stage": "listing", "item_id": "item-1",
                     "input_tokens": 1234, "output_tokens": 567, "images": 0, "cost_usd": cost, "estimated": True}
    assert second["kind"] == "image" and second["images"] == 1 and second["image_size"] == "2K"
    assert second["provider"] == "google" and second["cost_usd"] == image


def test_ledger_is_append_only(settings, ledger):
    text(settings, (100, 0))
    before = ledger.read_bytes()
    text(settings, (200, 0))
    after = ledger.read_bytes()
    assert after.startswith(before) and after.count(b"\n") == 2


def test_month_comes_from_utc_time(settings, ledger):
    late_in_new_york = datetime(2026, 10, 31, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    text(settings, (1, 0), now=late_in_new_york)
    text(settings, (1, 0), now=datetime(2026, 10, 31, 23, 59))         # naive = UTC
    a, b = lines(ledger)
    assert a["month"] == "2026-11" and a["ts"] == "2026-11-01T04:30:00+00:00"
    assert b["month"] == "2026-10"


@pytest.mark.parametrize("kwargs", [{"kind": "video"}, {"input_tokens": -1}, {"images": 1.5},
                                    {"output_tokens": True}, {"stage": ""}, {"provider": None}])
def test_record_rejects_bad_arguments(settings, ledger, kwargs):
    args = {"provider": "anthropic", "model": "claude-opus-5-5", "kind": "text", "stage": "listing", **kwargs}
    with pytest.raises(ValueError):
        spend.record(args.pop("provider"), args.pop("model"), args.pop("kind"), settings=settings, **args)
    assert not ledger.exists()


# --- reading: month_to_date, entries, corrupt lines --------------------------------------------

def test_month_to_date_groups_by_provider_stage_and_item(settings):
    text(settings, (250_000, 0), item_id="a")                                        # $1.00
    text(settings, (500_000, 0), item_id="b")                                        # $2.00
    text(settings, (0, 0))                                                            # $0, no item
    for _ in range(2):
        spend.record("google", "gemini-nano-banana-2.1", "image", stage="generate", item_id="a", images=1,
                     image_size="4K", settings=settings, now=OCT)                     # 2 x $0.1134
    spend_dollars(settings, 9.0, now=datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc), item_id="a")  # September
    m = spend.month_to_date(settings, "2026-10")
    assert m["month"] == "2026-10" and m["calls"] == 5 and m["images"] == 2
    assert m["total"] == pytest.approx(3.2268)
    assert m["by_provider"] == {"anthropic": 3.0, "google": pytest.approx(0.2268)}
    assert m["by_stage"] == {"listing": 3.0, "generate": pytest.approx(0.2268)}
    assert m["by_item"] == {"b": 2.0, "a": pytest.approx(1.2268)}
    assert m["by_model"]["claude-opus-5-5"] == 3.0
    assert m["input_tokens"] == 750_000
    assert spend.month_to_date(settings, "2026-09")["total"] == 9.0
    assert spend.month_to_date(settings, "2026-08") == {"month": "2026-08", **spend.summarize([])}


def test_month_to_date_defaults_to_the_current_utc_month(settings):
    spend.record("anthropic", "claude-opus-5-5", "text", stage="listing", input_tokens=250_000, settings=settings)
    assert spend.month_to_date(settings)["month"] == spend.month_of()
    assert spend.month_to_date(settings)["total"] == 1.0


def test_missing_ledger_is_zero(settings, ledger):
    assert not ledger.exists()
    assert spend.month_to_date(settings, "2026-10")["total"] == 0.0
    assert spend.budget_state(settings, now=OCT)["mtd"] == 0.0


def test_bad_month_is_refused(settings):
    with pytest.raises(ValueError, match="YYYY-MM"):
        spend.month_to_date(settings, "2026-13")


def test_corrupt_lines_are_skipped_with_a_warning(settings, ledger, caplog):
    spend_dollars(settings, 1.0)
    with ledger.open("ab") as f:
        f.write(b"not json\n")
        f.write(b"[1, 2, 3]\n")
        f.write(b'{"ts": "2026-10-16T12:00:00+00:00", "month": "2026-10"}\n')                 # no cost
        f.write(b'{"ts": "2026-10-16T12:00:00+00:00", "cost_usd": "5"}\n')                    # cost as text
        f.write(b'{"ts": "2026-10-16T12:00:00+00:00", "cost_usd": true}\n')
        f.write(b'{"ts": "2026-10-16T12:00:00+00:00", "cost_usd": -3}\n')
        f.write(b'{"ts": "yesterday", "month": "2026-10", "cost_usd": 5}\n')
        f.write(b'\xff\xfe{"garbage\n')
        f.write(b"\n")                                                                        # blank: ignored
        f.write(b'{"ts": "2026-10-16T13:00:00Z", "cost_usd": 2, "provider": "google"}\n')    # valid, no month
    caplog.set_level(logging.WARNING, logger="core.spend")
    m = spend.month_to_date(settings, "2026-10")
    assert m["total"] == 3.0 and m["calls"] == 2
    assert "skipped 8 unreadable line(s)" in caplog.text


def test_the_same_bad_lines_are_reported_once_not_on_every_check(settings, ledger, caplog):
    """check_budget runs before every paid call; a damaged line must not flood the log."""
    settings["spend"]["monthly_cap_usd"] = 100
    spend_dollars(settings, 1.0)
    with ledger.open("ab") as f:
        f.write(b"\x00\x00\x00\n")                                # NUL tail, as after a power cut
    caplog.set_level(logging.WARNING, logger="core.spend")
    for _ in range(5):
        spend.check_budget("image", settings, now=OCT)
    assert caplog.text.count("unreadable") == 1
    with ledger.open("ab") as f:
        f.write(b"oops\n")                                         # a new bad line is reported again
    spend.check_budget("image", settings, now=OCT)
    assert caplog.text.count("unreadable") == 2
    assert spend.budget_state(settings, now=OCT)["mtd"] == 1.0


def test_a_short_write_is_an_error(settings, ledger, monkeypatch):
    """Disk full: the hooks log the OSError; the half line is repaired by the next record."""
    import io as _io

    real_write = _io.FileIO.write

    class Short(_io.FileIO):
        def write(self, b):
            return real_write(self, bytes(b)[:10])

    monkeypatch.setattr(spend, "open", lambda *a, **kw: Short(a[0], "a+"), raising=False)
    with pytest.raises(OSError, match="short write"):
        spend_dollars(settings, 1.0)
    monkeypatch.undo()
    spend_dollars(settings, 2.0)
    assert spend.month_to_date(settings, "2026-10")["total"] == 2.0


def test_a_half_written_last_line_does_not_swallow_the_next_record(settings, ledger):
    spend_dollars(settings, 1.0)
    with ledger.open("ab") as f:
        f.write(b'{"ts": "2026-10-16T12:00:00+00:00", "cost_')                                # crash mid-write
    spend_dollars(settings, 2.0)
    assert spend.month_to_date(settings, "2026-10")["total"] == 3.0
    assert ledger.read_bytes().endswith(b"\n")


def test_entries_window(settings):
    for hour in (1, 2, 3):
        spend_dollars(settings, float(hour), now=OCT.replace(hour=hour))
    rows = spend.entries(settings, since=OCT.replace(hour=2), until=OCT.replace(hour=3))
    assert [r["cost_usd"] for r in rows] == [2.0]
    naive = spend.entries(settings, since=datetime(2026, 10, 16, 2))
    assert [r["cost_usd"] for r in naive] == [2.0, 3.0]
    assert spend.summarize(spend.entries(settings))["total"] == 6.0


# --- cap and warn ------------------------------------------------------------------------------

@pytest.mark.parametrize("cap", [None, 0, 0.0, "", " "])
def test_no_cap(settings, cap):
    settings["spend"]["monthly_cap_usd"] = cap
    spend_dollars(settings, 1000.0)
    state = spend.budget_state(settings, now=OCT)
    assert state == {"month": "2026-10", "mtd": 1000.0, "cap": None, "pct": None, "warn": False, "reached": False}
    spend.check_budget("text", settings, now=OCT)
    spend.check_budget("image", settings, now=OCT)


def test_no_cap_never_reads_the_ledger(settings, ledger):
    ledger.mkdir(parents=True)                                     # a directory: reading it would fail
    spend.check_budget("image", settings, now=OCT)


@pytest.mark.parametrize("mtd,warn,reached", [(7.99, False, False), (8.0, True, False), (9.5, True, False),
                                              (10.0, True, True), (12.5, True, True)])
def test_warn_and_reached_states(settings, mtd, warn, reached):
    settings["spend"]["monthly_cap_usd"] = 10
    spend_dollars(settings, mtd)
    state = spend.budget_state(settings, now=OCT)
    assert state == {"month": "2026-10", "mtd": mtd, "cap": 10.0, "pct": round(mtd / 10, 4), "warn": warn,
                     "reached": reached}


def test_check_budget_raises_at_the_cap(settings):
    settings["spend"]["monthly_cap_usd"] = 10
    spend_dollars(settings, 9.99)
    spend.check_budget("text", settings, now=OCT)                                # under the cap: fine
    spend_dollars(settings, 0.01)
    with pytest.raises(BudgetExceeded) as info:
        spend.check_budget("image", settings, now=OCT)
    e = info.value
    assert isinstance(e, RuntimeError)
    assert (e.kind, e.month, e.mtd, e.cap) == ("image", "2026-10", 10.0, 10.0)
    assert str(e) == ("Monthly spending cap reached: $10.00 of $10.00 used in 2026-10. Paid image calls are "
                      "paused until next month, or until spend.monthly_cap_usd is raised.")


def test_cap_resets_next_month_or_when_raised(settings):
    settings["spend"]["monthly_cap_usd"] = 5
    spend_dollars(settings, 6.0, now=OCT)
    with pytest.raises(BudgetExceeded):
        spend.check_budget("text", settings, now=OCT)
    spend.check_budget("text", settings, now=datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc))
    settings["spend"]["monthly_cap_usd"] = 7
    spend.check_budget("text", settings, now=OCT)


def test_cap_written_as_text(settings):
    settings["spend"]["monthly_cap_usd"] = "$1,000"
    assert spend.budget_state(settings, now=OCT)["cap"] == 1000.0


@pytest.mark.parametrize("cap", ["twenty", -5, True, float("nan"), [10]])
def test_bad_cap_is_an_error_not_no_cap(settings, cap):
    settings["spend"]["monthly_cap_usd"] = cap
    with pytest.raises(ValueError, match="monthly_cap_usd"):
        spend.check_budget("text", settings, now=OCT)
    with pytest.raises(ValueError, match="monthly_cap_usd"):
        spend.budget_state(settings, now=OCT)


@pytest.mark.parametrize("warn_at", [0, 1.5, "high", None])
def test_bad_warn_at_falls_back_to_80_percent(settings, warn_at, caplog):
    settings["spend"].update(monthly_cap_usd=10, warn_at=warn_at)
    spend_dollars(settings, 8.0)
    caplog.set_level(logging.WARNING, logger="core.spend")
    assert spend.budget_state(settings, now=OCT)["warn"] is True
    assert "warn_at" in caplog.text


def test_an_unwritable_ledger_pauses_paid_calls_when_a_cap_is_set(settings, ledger, tmp_path):
    """The ledger path cannot be opened for appending (here: it is a folder; on a server, a
    folder owned by root). Spending could not be counted, so the cap must not silently stop
    working: the first check refuses, before anything is paid for."""
    ledger.mkdir(parents=True)
    spend.check_budget("text", settings, now=OCT)                                # no cap: nothing to protect
    settings["spend"]["monthly_cap_usd"] = 25
    with pytest.raises(spend.LedgerUnavailable, match="Paid AI calls are paused: the spending log") as info:
        spend.check_budget("image", settings, now=OCT)
    assert not isinstance(info.value, BudgetExceeded)                           # the cap is not reached


def test_a_failed_record_pauses_later_paid_calls(settings, ledger, monkeypatch):
    settings["spend"]["monthly_cap_usd"] = 25
    spend.check_budget("text", settings, now=OCT)                                # probe passes, file created
    assert ledger.exists() and ledger.read_bytes() == b""

    def full(path, line):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(spend, "_append", full)
    with pytest.raises(OSError):
        spend_dollars(settings, 1.0)                                             # the hooks log this one
    with pytest.raises(spend.LedgerUnavailable, match="No space left on device"):
        spend.check_budget("text", settings, now=OCT)
    monkeypatch.undo()
    spend_dollars(settings, 1.0)                                                 # a write works again
    spend.check_budget("text", settings, now=OCT)


def test_check_budget_rejects_unknown_kind(settings):
    with pytest.raises(ValueError):
        spend.check_budget("video", settings)


# --- prices date, report, test guard -----------------------------------------------------------

def test_stale_or_missing_prices_date_is_logged_once(settings, caplog):
    caplog.set_level(logging.WARNING, logger="core.spend")
    text(settings, (1, 0), now=datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert caplog.text == ""                                                     # shipped date is fresh
    text(settings, (1, 0), now=datetime(2027, 6, 1, tzinfo=timezone.utc))
    text(settings, (1, 0), now=datetime(2027, 6, 2, tzinfo=timezone.utc))
    assert caplog.text.count("last checked on 2026-10-16") == 1
    caplog.clear()
    settings["spend"]["prices"] = {"checked": "Oct 2026"}
    text(settings, (1, 0))
    assert "spend.prices.checked" in caplog.text


def test_prices_date_may_be_a_yaml_date(settings, caplog):
    settings["spend"]["prices"] = {"checked": date(2026, 10, 1)}
    caplog.set_level(logging.WARNING, logger="core.spend")
    text(settings, (1, 0))
    assert caplog.text == ""


def test_table_is_plain_text(settings):
    settings["spend"]["monthly_cap_usd"] = 20
    spend_dollars(settings, 2.0, item_id="2026-10-15-coloring_pages-farm-animals-1a2b")
    spend.record("google", "gemini-nano-banana-2.1", "image", stage="generate", images=1, image_size="1K",
                 settings=settings, now=OCT)
    out = spend.table(settings, "2026-10")
    assert out.startswith("Spending for 2026-10 (estimates; your provider bills are what count)\n")
    assert "Total: $2.03 of $20.00 cap (10%)" in out
    assert "Paid calls: 2 (1 image)" in out
    assert "  anthropic  $2.00" in out and "  google     $0.03" in out
    assert "2026-10-15-coloring_pages-farm-animals-1a2b  $2.00" in out
    settings["spend"]["monthly_cap_usd"] = None
    assert "(no monthly cap set)" in spend.table(settings, "2026-10")


def test_table_still_shows_spending_when_the_cap_is_not_a_number(settings):
    spend_dollars(settings, 2.0)
    settings["spend"]["monthly_cap_usd"] = "twenty"
    out = spend.table(settings, "2026-10")
    assert "Total: $2.00. The monthly cap setting is not a number (spend.monthly_cap_usd: 'twenty')" in out
    assert "  anthropic  $2.00" in out


def test_tests_never_touch_the_repo_ledger(caplog):
    real = ROOT / "data" / "spend" / "ledger.jsonl"
    before = real.read_bytes() if real.exists() else None
    settings = {"spend": {"ledger_file": "data/spend/ledger.jsonl", "monthly_cap_usd": 0.01}}
    caplog.set_level(logging.WARNING, logger="core.spend")
    spend.record("anthropic", "claude-opus-5-5", "text", stage="listing", input_tokens=10**6, settings=settings)
    assert (real.read_bytes() if real.exists() else None) == before
    assert "not writing the real spend ledger" in caplog.text
    spend.check_budget("text", settings)                                         # reads see an empty ledger


def test_a_months_read_skips_other_months_quickly(settings, ledger):
    """Lines of other months (even damaged ones) do not count and are not reported."""
    spend_dollars(settings, 1.0, now=datetime(2026, 9, 3, tzinfo=timezone.utc))
    spend_dollars(settings, 2.0)
    with ledger.open("ab") as f:
        f.write(b'{"ts": "2026-09-30T23:00:00+00:00", "month": "2026-09", "cost_usd": "bad"}\n')
        f.write(b'{"ts": "2026-10-16T13:00:00Z", "cost_usd": 0.5}\n')                 # no month field: its ts
    assert spend.month_to_date(settings, "2026-10")["total"] == 2.5
    assert spend.month_to_date(settings, "2026-09")["total"] == 1.0
    assert spend.summarize(spend.entries(settings))["total"] == 3.5
