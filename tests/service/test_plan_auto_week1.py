"""The repo's own schedule (user, 2026-10-21): weekly plans start by themselves from week 1,
whatever their cost; only Hold stops them."""
from core.config import load_settings
from service import limits, orders, plan


def test_repo_settings_start_plans_by_themselves_from_week_1():
    s = load_settings()
    assert s["schedule"]["approvals_before_auto"] == 0
    assert plan.approvals_needed(s) == 0


def test_repo_settings_have_no_cost_gate_on_automatic_plans():
    s = load_settings()
    assert s["schedule"]["auto_start_max_cost_share"] == 1.0
    lim = limits.load(s, path="config/limits.example.yaml")
    assert lim.ok, lim.reason
    needs, why = orders.plan_needs_approval(24.0, s, lim, approved_plans=0, cap=25.0)
    assert not needs and why is None


def test_asking_for_approval_again_still_works():
    s = load_settings()
    needs, why = orders.plan_needs_approval(5.0, s, approved_plans=0, approve_each=True, cap=25.0)
    assert needs and why
