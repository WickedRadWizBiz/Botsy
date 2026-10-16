"""Research stage: licensed research exports -> aggregated design briefs.

Entry point: research.run(niche=None, shop_id="main", inputs_dir=None,
briefs_dir=None, date=None, *, history_dir=None, stage=None) -> list[Path].
CLI: python -m research --help. Stages (discovery, then refined after 20 real Etsy
drafts): research/stages.py. Evidence window and received dates: research/window.py.
"""
from research.guard import BriefComplianceError, validate_brief_doc
from research.pipeline import NoInputsError, run

__all__ = ["run", "validate_brief_doc", "BriefComplianceError", "NoInputsError"]
