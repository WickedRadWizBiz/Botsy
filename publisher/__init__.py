"""Publisher stage: Etsy Open API v3 drafts for OUR OWN shop only. Never activates.

    publish_draft(item_dir, *, client=None, dry_run=False, settings=None, review_dir=None) -> dict
    review_wait(item_dir, *, settings=None, review_dir=None) -> str | None
    run_all(out_root=None, *, client=None, dry_run=False, settings=None, review_dir=None) -> list[dict]
    discard_draft(item_dir, *, client=None, settings=None) -> dict

Writes <item>/publish.json (docs/CONTRACTS.md section 5) and sets the manifest status to
"drafted". Every request passes the safety layer in publisher/safety.py. CLI:
python -m publisher auth | publish | discard | taxonomy | own-stats.
"""
from publisher.auth import AuthError, AuthRequired
from publisher.client import EtsyAPIError, EtsyClient, RateLimited
from publisher.defaults import DEFAULTS
from publisher.fake import FakeEtsyClient, FakeEtsySession
from publisher.publish import (
    PublishError,
    PublishGateError,
    ReviewPending,
    check_ready,
    discard_draft,
    owned_listing_ids,
    publish_draft,
    review_wait,
    run_all,
)
from publisher.safety import SafetyViolation

__all__ = [
    "DEFAULTS", "AuthError", "AuthRequired", "EtsyAPIError", "EtsyClient", "FakeEtsyClient",
    "FakeEtsySession", "PublishError", "PublishGateError", "RateLimited", "ReviewPending", "SafetyViolation",
    "check_ready", "discard_draft", "owned_listing_ids", "publish_draft", "review_wait", "run_all",
]
