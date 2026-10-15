"""CLI: python -m publisher auth | publish | discard | taxonomy | own-stats.

    auth       [--shop KEY] [--paste] [--cert PEM --key PEM] [--timeout S]
    publish    [--item ID] [--out DIR] [--review-dir DIR] [--dry-run]
    discard    --item ID [--out DIR]
    taxonomy   [--search TEXT] [--json] [--dry-run]
    own-stats  [--shop KEY] [--out FILE] [--dry-run]

Exit codes: 0 ok, 1 some items not drafted, 2 setup problem (secrets, tokens, safety).
"""
from __future__ import annotations

import argparse
import json
import logging
import ssl
import sys
from pathlib import Path

from core.config import ROOT, MissingSecretError, load_settings, require_env, resolve
from core.items import item_dir as default_item_dir
from core.items import output_root, stage_out_root, valid_item_id
from publisher import auth as oauth
from publisher import own_stats, taxonomy
from publisher.client import EtsyAPIError, EtsyClient
from publisher.defaults import load_cfg
from publisher.fake import FakeEtsyClient
from publisher.publish import (
    PublishError,
    PublishGateError,
    discard_draft,
    publish_draft,
    run_all,
)
from publisher.safety import SafetyViolation


def _item_dir(item: str, out: str | None, settings: dict) -> Path:
    p = Path(item)
    if p.is_dir() and (p / "manifest.json").exists():
        return p
    if not valid_item_id(item):
        raise PublishGateError(f"invalid item id {item!r}")
    d = default_item_dir(item, Path(out) if out else output_root(settings))
    if not (d / "manifest.json").exists():
        raise PublishGateError(f"no item {item} in {d.parent}")
    return d


def _show(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def cmd_auth(args, settings) -> int:
    cfg = load_cfg(settings)
    redirect_uri = require_env("ETSY_REDIRECT_URI")
    ctx = None
    if args.cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(args.cert, args.key)
    client = EtsyClient(settings=settings, shop_key=args.shop, verify_shop=False)
    tok = oauth.authorize(client, redirect_uri, cfg["scopes"], timeout=args.timeout or cfg["auth_timeout_s"],
                          paste=args.paste, ssl_context=ctx, connect_base=cfg["connect_url"])
    me = client.get_me()
    path = getattr(client.tokens, "path", None)
    print(f"Authorized Etsy user {me.get('user_id')} (shop_id {me.get('shop_id')}); scopes: {tok.get('scope')}")
    if path:
        print(f"Tokens saved to {_show(path)} (mode 600, gitignored). Access tokens last 1 hour and "
              "refresh automatically; the refresh token lasts 90 days.")
    if str(client.shop_id) != str(me.get("shop_id")):
        print(f"Set ETSY_SHOP_ID={me.get('shop_id')} in .env (currently {client.shop_id or 'unset'}).")
    print("For CI, store the token file's refresh_token as the ETSY_REFRESH_TOKEN repository secret.")
    return 0


def cmd_publish(args, settings) -> int:
    # Like every stage CLI, a dry run defaults to pipeline.dry_run_output_dir; preview real
    # items with an explicit --out (for example --out output).
    args.out = str(stage_out_root(args.out, settings, dry_run=args.dry_run))
    if args.item:
        d = _item_dir(args.item, args.out, settings)
        try:
            rec = publish_draft(d, dry_run=args.dry_run, settings=settings, review_dir=args.review_dir)
        except (PublishGateError, PublishError, EtsyAPIError) as e:
            print(f"publisher: not drafted: {e}", file=sys.stderr)
            return 1
        results = [{"item_id": d.name, "ok": True, "listing_id": rec["listing_id"],
                    "detail": f"draft {rec['listing_id']}" + (" (dry run)" if args.dry_run else "")}]
    else:
        results = run_all(args.out, dry_run=args.dry_run, settings=settings, review_dir=args.review_dir)
    if not results:
        print("publisher: no items waiting for a draft (status 'listed').")
    for r in results:
        detail = r["detail"].removeprefix(f"{r['item_id']}: ")
        print(f"{'ok  ' if r['ok'] else 'FAIL'} {r['item_id']}: {detail}")
    if any(r["ok"] for r in results) and not args.dry_run:
        print("Drafts are inactive. Review and activate them yourself in Etsy's Shop Manager.")
    return 0 if all(r["ok"] for r in results) else 1


def cmd_discard(args, settings) -> int:
    d = _item_dir(args.item, args.out, settings)
    try:
        rec = discard_draft(d, settings=settings)
    except (PublishError, EtsyAPIError) as e:
        print(f"publisher: {e}", file=sys.stderr)
        return 1
    what = ("Deleted draft" if rec.get("deleted") else
            "Draft was already gone from Etsy; forgot" if not rec.get("dry_run") else "Forgot dry-run draft")
    print(f"{what} {rec['listing_id']} for {d.name}; record kept as {rec['archived']}. "
          "The next publish creates a fresh draft.")
    return 0


def cmd_taxonomy(args, settings) -> int:
    client = FakeEtsyClient(settings=settings) if args.dry_run else EtsyClient(settings=settings)
    rows = taxonomy.list_nodes(client, args.search)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(f"{r['id']:>10}  {r['path']}")
        print(f"{len(rows)} node(s). Put the id of the most specific fit under listing.taxonomy_ids.<niche> "
              "in config/settings.yaml.", file=sys.stderr)
    return 0


def cmd_own_stats(args, settings) -> int:
    cfg = load_cfg(settings)
    if args.dry_run:
        rows = own_stats.to_rows(own_stats.fetch_own_listings(FakeEtsyClient(settings=settings)))
        sys.stdout.write(own_stats.to_csv(rows))
        print(f"(dry run: {len(rows)} fake rows, nothing written)", file=sys.stderr)
        return 0
    path, n = own_stats.write_own_stats(EtsyClient(settings=settings, shop_key=args.shop),
                                        Path(args.out) if args.out else resolve(cfg["own_stats_file"]))
    print(f"Wrote {n} of our own listings to {_show(path)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m publisher",
                                 description="Etsy drafts for our own shop (never activates listings).")
    ap.add_argument("--settings", help="settings YAML (default: config/settings.yaml)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("auth", help="connect our Etsy shop (OAuth 2.0 + PKCE) and save tokens")
    a.add_argument("--shop", help="shop key from settings `shops` (default: the first)")
    a.add_argument("--paste", action="store_true", help="paste the redirected URL instead of listening locally")
    a.add_argument("--cert", help="PEM certificate, to listen on an https://localhost redirect URI")
    a.add_argument("--key", help="PEM private key for --cert")
    a.add_argument("--timeout", type=float, help="seconds to wait for the redirect")

    p = sub.add_parser("publish", help="create Etsy drafts for listed items")
    p.add_argument("--item", help="item id (or item folder); default: every item with status 'listed'")
    p.add_argument("--out", help="output root holding the item folders (default: settings paths.output_dir, "
                                 "or pipeline.dry_run_output_dir with --dry-run)")
    p.add_argument("--review-dir", help="review queue folder (default: settings paths.review_dir, or <out>/review)")
    p.add_argument("--dry-run", action="store_true",
                   help="fake Etsy client, no secrets; real items (e.g. --out output) are only previewed")

    d = sub.add_parser("discard", help="delete this tool's draft for an item and forget it (start over)")
    d.add_argument("--item", required=True)
    d.add_argument("--out")

    t = sub.add_parser("taxonomy", help="list seller taxonomy nodes (id + path)")
    t.add_argument("--search", help="only nodes whose path contains every word")
    t.add_argument("--json", action="store_true")
    t.add_argument("--dry-run", action="store_true", help="fake tree, no secrets")

    o = sub.add_parser("own-stats", help="write our own listings' stats CSV for the research stage")
    o.add_argument("--shop", help="shop key from settings `shops` (default: the first)")
    o.add_argument("--out", help="CSV path (default: data/research_inputs/own_shop_stats.csv)")
    o.add_argument("--dry-run", action="store_true", help="print fake rows, write nothing")

    args = ap.parse_args(argv)
    if args.cmd == "auth" and bool(args.cert) != bool(args.key):
        ap.error("--cert and --key go together")
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    settings = load_settings(args.settings) if args.settings else load_settings()
    handlers = {"auth": cmd_auth, "publish": cmd_publish, "discard": cmd_discard,
                "taxonomy": cmd_taxonomy, "own-stats": cmd_own_stats}
    try:
        return handlers[args.cmd](args, settings)
    except KeyError as e:  # get_shop(): unknown --shop
        print(f"publisher: {e.args[0] if e.args else e}", file=sys.stderr)
        return 2
    except (MissingSecretError, oauth.AuthError, SafetyViolation) as e:
        print(f"publisher: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    except (PublishGateError, PublishError, EtsyAPIError) as e:
        print(f"publisher: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
