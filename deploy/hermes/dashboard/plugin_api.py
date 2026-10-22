"""Backend routes of the Botsy dashboard tab (Hermes v0.21.7 dashboard plugin API).

Hermes mounts this module's `router` under /api/plugins/botsy/ (manifest.json "api"), behind the
dashboard's own checks: the Host header must match the bound address and every /api/ request
must carry the per-start session token the dashboard writes into its page
(hermes_cli/web_server.py auth_middleware); a user plugin is imported only when it is listed in
plugins.enabled. On top of that, every route here refuses unless the dashboard is bound to
127.0.0.1 with no public URL and the request comes from the server itself (botsy_tab.bind_refusal),
and the module refuses to load at all when the command line asks for another --host.

Routes (the only ones; tests/deploy/test_dashboard_tab.py checks the list):
    GET  /overview          every section of the tab (botsy-read)
    GET  /thumb?item_id=..  one of our own product images as a data: URL (view_item_images)
    POST /pause             botsy-act pause       {"reason_code"?: one of PAUSE_REASONS}
    POST /lower_cap         botsy-act lower_cap   {"cap_usd": number below the cap in force}
The two POSTs also need the header X-Botsy-Tab: 1 (a cross-site form cannot set it). There is no
route that approves, resumes, raises the cap, plans, reviews or edits settings.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

_HERE = Path(__file__).resolve().parent
_NAME = "botsy_dashboard_tab"


def _load_tab():
    mod = sys.modules.get(_NAME)
    if mod is not None and getattr(mod, "__file__", None) == str(_HERE / "botsy_tab.py"):
        return mod
    spec = importlib.util.spec_from_file_location(_NAME, _HERE / "botsy_tab.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


tab = _load_tab()
tab.refuse_public_argv(sys.argv)            # `hermes dashboard --host 0.0.0.0`: this tab does not load

router = APIRouter()


def _refusal(request: Request) -> JSONResponse | None:
    st = request.app.state
    reason = tab.bind_refusal(getattr(st, "bound_host", None), getattr(st, "auth_required", True),
                              getattr(st, "trusted_public_hosts", frozenset()),
                              request.client.host if request.client else None)
    if reason:
        return JSONResponse({"ok": False, "error": reason, "explain": tab.REFUSAL_TEXT[reason]}, status_code=403)
    return None


def _action_refusal(request: Request) -> JSONResponse | None:
    refused = _refusal(request)
    if refused is not None:
        return refused
    if request.headers.get("x-botsy-tab") != "1":
        return JSONResponse({"ok": False, "error": "missing_header", "explain": tab.REFUSAL_TEXT["missing_header"]},
                            status_code=400)
    return None


@router.get("/overview")
def overview(request: Request):
    return _refusal(request) or tab.overview()


@router.get("/thumb")
def thumb(request: Request, item_id: str = ""):
    return _refusal(request) or tab.thumbnail(item_id)


@router.post("/pause")
def pause(request: Request, body: dict = Body(default_factory=dict)):
    return _action_refusal(request) or tab.act("pause", body)


@router.post("/lower_cap")
def lower_cap(request: Request, body: dict = Body(default_factory=dict)):
    return _action_refusal(request) or tab.act("lower_cap", body)
