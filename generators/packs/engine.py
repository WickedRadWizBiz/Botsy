"""generate_packs(): one brief -> one item per planned pack (docs/PACKS.md section 3).

    generate_packs(brief, *, plugin, brief_file=None, shop_id="main", out_root=None, date=None,
                   dry_run=False, client=None, settings=None, text_client=None, sizes=None,
                   state=None) -> list[Path]

For each PackSpec from plan(), built master first, then derived packs (no paid work), then
fresh packs (new pages):
- master / fresh: the pack's design set (<out_root>/sets/<set_id>/) is built once, from the
  plugin's plan_pages() (and build_pages() for image niches: one paid image per page, with
  spare subjects for refusals), then reused by reruns; a derived pack curates its pages from
  its master set (generators/packs/curate.py);
- the item (output/<item_id>/) is built in a scratch folder from the chosen pages and swapped
  in when complete (generators.shared.swap_in drops the old QA report, mockups, listing and
  approval); an item with a real Etsy draft is never touched and a dry run never replaces a
  real item or a real (paid) design set;
- after the item is written, the set records which item used which pages (used_in, items)
  and the pack state advances its size's counter (generators/packs/state.py).
A pack that fails stops the brief; the packs built before it stay, and the exception carries
their folders as `pack_dirs` so the pipeline can still run them through QA.

Plugin API (generators/<niche>/__init__.py; required):
    PAGE_TYPES
    plan_pages(ctx, n, *, seed, settings) -> list[dict]   n distinct {"page_id", "type", "subject", "spec"}
    render_pages(pages, out_pdf, *, page_size, settings, ctx) -> int   one PDF, returns its page count
    preview(pages, out_dir, *, settings, ctx, limit=6) -> list[Path]   PNG previews
Optional hooks the engine uses when present:
    NICHE (default: the module name), AI_ASSISTED (bool), USES_SUBJECTS (ctx["subjects"] is filled by
    packs.subjects()), FILE_LABEL, default_pack_size(settings), max_pack_size(settings),
    files_per_pack(page_sizes) -> int (Etsy allows 5 files; checked before anything is paid for),
    brief_review_reasons(brief, settings), spare_pages(settings), build_pages(candidates, n, set_build, *,
    settings, ctx, client, blocklist, item_id, dry_run) -> {"pages", "skipped", "model"},
    resolve_image_client(client, *, dry_run, settings), item_assets(pages, build) -> list[dict],
    write_files(pages, build, *, slug, sizes, settings, ctx) -> list[dict], manifest_previews(build, files,
    pages, *, settings, ctx), pack_fields(pages, files, *, settings, ctx, set_doc) -> dict,
    printed_text(pages, ctx[, settings=]) (called with the run's settings when it takes them),
    review_reasons(pages, *, settings) -> list[str].
Pages handed to render_pages / write_files / item_assets carry "asset_files": {name: absolute path}
for their set's asset files. ctx: {"theme", "niche", "audience", "keywords", "style", "system",
"item_seed"} plus "page_sizes", "activity_types", "difficulty", "set_kind", "used_subjects" (the
theme's subjects in other design sets: a fresh set avoids them), "subjects" and, while an item
is written, "pack": {"size", "source", "n"}.
"""
from __future__ import annotations

import inspect
import logging
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path

from PIL import Image

from core.compliance import Blocklist, ComplianceResult, iter_strings, system_mentions
from core.config import load_settings, today
from core.items import output_root, slugify
from core.render import pdf_info, render_pdf_pages
from generators.packs import sets as sets_mod
from generators.packs.curate import curate
from generators.packs.model import PackSpec, config, item_id_for, set_id_for, theme_key
from generators.packs.plan import brief_pack_sizes, brief_system, plan
from generators.packs.state import PackState
from generators.packs.subjects import plan_subjects
from generators.shared import (
    ETSY_MAX_FILE_BYTES, ETSY_MAX_FILE_MB, ETSY_MAX_FILES, as_list, brief_price, check_brief, file_name,
    find_shop, item_date, keep_existing, page_sizes_for, recorded_brief_file, swap_in,
)

log = logging.getLogger(__name__)

ITEM_SUBDIRS = ("files", "previews", "assets")
PREVIEW_DPI = 150
_BUILD_RANK = {"master": 0, "derived": 1, "fresh": 2}


class PackPlanError(ValueError):
    """A plugin planned pages the engine cannot use (too few, repeated ids, blocked text)."""


@dataclass
class _Job:
    brief: dict
    plugin: object
    niche: str
    theme: str
    brief_id: str
    brief_file: object
    shop_id: str
    shop: dict
    date: str
    dry_run: bool
    settings: dict
    root: Path
    state: PackState
    blocklist: Blocklist
    reasons: list[str]
    ctx: dict
    all_sizes: list[int]
    client: object = None
    text_client: object = None
    specs: list[PackSpec] = field(default_factory=list)
    _image_client: object = None

    def image_client(self):
        if self._image_client is None:
            hook = getattr(self.plugin, "resolve_image_client", None)
            self._image_client = hook(self.client, dry_run=self.dry_run, settings=self.settings) if hook \
                else self.client
        return self._image_client

    def item_id(self, spec: PackSpec) -> str:
        return item_id_for(self.date, self.niche, self.theme, self.brief_id, spec)


def plugin_niche(plugin) -> str:
    return getattr(plugin, "NICHE", None) or plugin.__name__.rsplit(".", 1)[-1]


def generate_packs(brief: dict, *, plugin, brief_file=None, shop_id: str = "main", out_root=None,
                   date: str | None = None, dry_run: bool = False, client=None, settings: dict | None = None,
                   text_client=None, sizes=None, state: PackState | None = None) -> list[Path]:
    """Build every planned pack of one brief (only `sizes` when given). Returns the item dirs
    in build order, master first. Errors: ValueError (brief or plan refused), ImageCapExceeded /
    ImageGenerationError (image niches), core.spend.BudgetExceeded / LedgerUnavailable."""
    settings = settings or load_settings()
    niche = plugin_niche(plugin)
    checked = check_brief(brief, niche, settings, brief_file)          # before anything is planned or paid for
    shop = find_shop(settings, shop_id)
    date = item_date(date or today())
    root = Path(out_root) if out_root else output_root(settings)
    all_sizes = brief_pack_sizes(brief, settings, plugin)
    page_sizes = page_sizes_for(brief, settings)
    hook = getattr(plugin, "files_per_pack", None)
    n_files = int(hook(page_sizes)) if hook else len(page_sizes)
    if n_files > ETSY_MAX_FILES:
        raise ValueError(f"{n_files} digital files per pack exceed Etsy's limit of {ETSY_MAX_FILES} per listing")
    reasons = list(checked.review_reasons)
    hook = getattr(plugin, "brief_review_reasons", None)
    if hook:
        reasons += hook(brief, settings)
    if state is None:
        state = PackState.load(settings, out_root=root, shop_id=shop_id, date=date, dry_run=dry_run)
    elif state.date is None:
        state.date = date
    job = _Job(brief, plugin, niche, checked.theme, str(brief.get("brief_id") or ""), brief_file, shop_id, shop,
               date, bool(dry_run), settings, root, state, checked.blocklist, reasons,
               base_ctx(brief, niche, checked.theme, page_sizes=page_sizes), all_sizes, client, text_client)
    job.specs = sorted(plan(brief, settings, state, sizes=sizes, plugin=plugin),
                       key=lambda s: (_BUILD_RANK.get(s.source, 3), s.size))
    # Before anything is paid for: a dry run must not replace any real item of this plan.
    skips = {spec: keep_existing(root / job.item_id(spec), job.item_id(spec), job.dry_run) for spec in job.specs}
    dirs: list[Path] = []
    for spec in job.specs:
        try:
            dirs.append(root / job.item_id(spec) if skips[spec] else _make_pack(job, spec))
        except Exception as exc:
            if dirs:
                try:
                    exc.pack_dirs = list(dirs)          # the packs made before this one stay usable
                except AttributeError:
                    pass
            raise
    return dirs


def base_ctx(brief: dict, niche: str, theme: str, *, page_sizes=None) -> dict:
    """What a plugin plans and renders from: our own brief fields only."""
    style = brief.get("style_notes")
    return {"theme": theme, "niche": niche, "audience": str(brief.get("audience") or ""),
            "keywords": as_list(brief.get("keywords")), "style": style if isinstance(style, str) else "",
            "system": brief_system(brief), "item_seed": "", "page_sizes": list(page_sizes or []),
            "activity_types": as_list(brief.get("activity_types")), "difficulty": brief.get("difficulty"),
            "used_subjects": []}


# --- one pack ------------------------------------------------------------------------------

def _make_pack(job: _Job, spec: PackSpec) -> Path:
    if spec.is_fresh:
        set_doc = _fresh_set(job, spec)
        pages = list(set_doc["pages"])
    else:
        set_doc = sets_mod.load_set(job.root, spec.set_id)
        if not _derivable(job, set_doc, spec.size):
            log.warning("%s: master set %s is missing or incomplete; making a fresh %d-page pack instead",
                        job.theme, spec.set_id, spec.size)
            spec = replace(spec, source="fresh", new_set=True, reason="master set missing",
                           set_id=set_id_for(job.date, job.niche, job.theme, job.brief_id, spec.size, "fresh", spec.n))
            d = job.root / job.item_id(spec)
            if keep_existing(d, d.name, job.dry_run):
                return d
            return _make_pack(job, spec)
        item_id = job.item_id(spec)
        seed = f"{spec.set_id}|{spec.size}|{spec.n}"
        pages = curate(set_doc, spec.size, size=spec.size, seed=seed, exclude_item=item_id,
                       min_quality=float(config(job.settings).get("min_quality", 0.5)))
    item_id = job.item_id(spec)
    d = job.root / item_id
    _write_item(job, spec, item_id, d, set_doc, pages)
    sets_mod.record_item(set_doc, item_id, page_ids=[p["page_id"] for p in pages], size=spec.size,
                         source=spec.source, created=job.date)
    sets_mod.save_set(job.root, set_doc)
    job.state.record_success(job.niche, job.brief_id, spec, item_id, job.date)
    job.state.save()
    log.info("generated %s (%d pages, %s from %s)", item_id, len(pages), spec.source, spec.set_id)
    return d


def _same_kind_of_run(job: _Job, doc: dict) -> bool:
    """A real pack is never made from dry-run (fake) art, nor a dry-run pack from paid art.
    Procedural sets are the same either way."""
    return not doc.get("ai_assisted") or bool(doc.get("dry_run")) == job.dry_run


def _derivable(job: _Job, doc: dict | None, size: int) -> bool:
    return (doc is not None and doc.get("kind") == "master" and doc.get("niche") == job.niche
            and len(doc.get("pages") or []) > size and sets_mod.is_complete(job.root, doc)
            and _same_kind_of_run(job, doc))


def _reusable(job: _Job, doc: dict | None, spec: PackSpec) -> bool:
    return (doc is not None and doc.get("kind") == spec.source and doc.get("niche") == job.niche
            and (doc.get("theme_key") or theme_key(doc.get("theme"))) == theme_key(job.theme)
            and sets_mod.is_complete(job.root, doc, spec.size) and _same_kind_of_run(job, doc))


def _fresh_set(job: _Job, spec: PackSpec) -> dict:
    """The master or fresh pack's own design set: the existing one when it is complete and
    from a run of the same kind (a rerun pays for nothing twice), else new pages."""
    doc = sets_mod.load_set(job.root, spec.set_id)
    if doc is not None and doc.get("ai_assisted") and doc.get("dry_run") is False and job.dry_run:
        raise ValueError(f"design set {spec.set_id} holds real (paid) art; not replacing it with dry-run art "
                         "(dry runs belong in their own --out, e.g. output/dry-run)")
    if _reusable(job, doc, spec):
        return doc
    plugin, settings, n = job.plugin, job.settings, spec.size
    hook = getattr(plugin, "spare_pages", None)
    spares = max(0, int(hook(settings))) if hook else 0
    used = _used_subjects(job, spec.set_id)
    pctx = dict(job.ctx, item_seed=spec.set_id, set_kind=spec.source, used_subjects=used)
    subjects_source = None
    master_item = job.item_id(spec)
    count = n + spares
    if getattr(plugin, "USES_SUBJECTS", False):
        # Spare subjects replace refused images; a theme with fewer subjects gets fewer spares.
        kw = dict(settings=settings, client=job.text_client, dry_run=job.dry_run, audience=job.ctx["audience"],
                  keywords=job.ctx["keywords"], item_id=master_item, blocklist=job.blocklist, minimum=n)
        try:
            sp = plan_subjects(job.theme, job.niche, count, avoid=used, **kw)
        except ValueError:
            if not used:
                raise
            # Not enough new subjects: new art of a subject used before is still a new page.
            sp = plan_subjects(job.theme, job.niche, count, **kw)
        pctx["subjects"] = sp.subjects
        subjects_source = sp.source
        count = len(sp.subjects)
    candidates = _checked_pages(job, plugin.plan_pages(pctx, count, seed=spec.set_id, settings=settings),
                                n, spec.set_id)
    model = None
    skipped: list = []
    with sets_mod.building(job.root, spec.set_id) as sb:
        build = getattr(plugin, "build_pages", None)
        if build:
            result = build(candidates, n, sb, settings=settings, ctx=pctx, client=job.image_client(),
                           blocklist=job.blocklist, item_id=master_item, dry_run=job.dry_run)
            pages, skipped, model = result["pages"], list(result.get("skipped") or []), result.get("model")
        else:
            pages = [dict(p, quality=float(p.get("quality", 1.0))) for p in candidates[:n]]
        if len(pages) != n:
            raise PackPlanError(f"{job.niche} built {len(pages)} pages for a {n}-page set")
        ai = bool(getattr(plugin, "AI_ASSISTED", False)) or any(p.get("ai_assisted") for p in pages)
        doc = sets_mod.new_set_doc(spec.set_id, niche=job.niche, theme=job.theme, theme_key=theme_key(job.theme),
                                   shop_id=job.shop_id, brief_id=job.brief_id or None, created=job.date,
                                   kind=spec.source, size=n, dry_run=job.dry_run, model=model, ai_assisted=ai,
                                   system=job.ctx["system"])
        doc["pages"] = [sets_mod.page_entry({k: v for k, v in p.items() if k != "asset_files"}) for p in pages]
        doc["skipped"] = skipped
        if subjects_source:
            doc["subjects_source"] = subjects_source
        sets_mod.commit(job.root, sb, doc)
    job.state.sets(reload=True)
    return doc


def _used_subjects(job: _Job, own_set: str) -> list[str]:
    """Subjects of the theme's other design sets (a fresh pack brings new subjects)."""
    key = theme_key(job.theme)
    out = []
    for doc in job.state.sets():
        if doc.get("set_id") != own_set and doc.get("niche") == job.niche and doc.get("shop_id") == job.shop_id \
                and (doc.get("theme_key") or theme_key(doc.get("theme"))) == key:
            out += [p.get("subject") for p in doc.get("pages") or [] if isinstance(p.get("subject"), str)]
    return list(dict.fromkeys(out))


def _checked_pages(job: _Job, pages, n: int, set_id: str) -> list[dict]:
    """The plugin's page specs, with page ids made unique across sets (prefixed with the set
    id) and every string checked against the blocklist before anything is paid for."""
    if not isinstance(pages, list) or not all(isinstance(p, dict) and p.get("page_id") for p in pages):
        raise PackPlanError(f"{job.niche}.plan_pages must return page dicts with a page_id")
    if len(pages) < n:
        raise PackPlanError(f"{job.niche}.plan_pages gave {len(pages)} pages; {n} were asked for")
    out = []
    for p in pages:
        pid = str(p["page_id"])
        out.append(dict(p, page_id=pid if pid.startswith(set_id) else f"{set_id}-{pid}"))
    ids = [p["page_id"] for p in out]
    if len(set(ids)) != len(ids):
        raise PackPlanError(f"{job.niche}.plan_pages repeated page ids")
    found = job.blocklist.check(*iter_strings([{k: v for k, v in p.items() if k != "page_id"} for p in out]))
    if found.blocked:
        raise PackPlanError(f"planned pages contain blocked terms {found.blocked}; nothing was generated")
    return out


# --- the item ------------------------------------------------------------------------------

def _with_assets(job: _Job, set_doc: dict, pages: list[dict]) -> list[dict]:
    base = sets_mod.set_dir(job.root, set_doc["set_id"])
    out = []
    for p in pages:
        asset = p.get("asset")
        files = {k: str(base / v) for k, v in asset.items() if isinstance(v, str)} if isinstance(asset, dict) \
            else ({"file": str(base / asset)} if isinstance(asset, str) else {})
        out.append(dict(p, asset_files=files))
    return out


def _write_item(job: _Job, spec: PackSpec, item_id: str, d: Path, set_doc: dict, pages: list[dict]) -> None:
    plugin, settings = job.plugin, job.settings
    ictx = dict(job.ctx, item_seed=item_id, set_kind=set_doc.get("kind"),
                pack={"size": spec.size, "source": spec.source, "n": spec.n})
    pages = _with_assets(job, set_doc, pages)
    reasons = list(job.reasons)
    prompts = [p["spec"]["prompt"] for p in pages if isinstance(p.get("spec"), dict)
               and isinstance(p["spec"].get("prompt"), str)]
    reasons += _text_reasons(job, pages, ictx, prompts)
    hook = getattr(plugin, "review_reasons", None)
    if hook:
        reasons += hook(pages, settings=settings)
    price = brief_price(job.brief, job.shop, job.niche, reasons)
    sizes = list(job.ctx["page_sizes"])
    slug = slugify(job.theme)
    dpi = int(settings["print"].get("dpi", 300))

    d.parent.mkdir(parents=True, exist_ok=True)
    build = Path(tempfile.mkdtemp(prefix=f".build-{item_id}-", dir=d.parent))
    try:
        for sub in ITEM_SUBDIRS:
            (build / sub).mkdir()
        hook = getattr(plugin, "item_assets", None)
        assets = hook(pages, build) if hook else []
        hook = getattr(plugin, "write_files", None)
        if hook:
            files = hook(pages, build, slug=slug, sizes=sizes, settings=settings, ctx=ictx)
        else:
            label = getattr(plugin, "FILE_LABEL", job.niche.replace("_", "-"))
            names = {s: "files/" + file_name(slug, f"-{label}-{spec.size}-pages-{s}.pdf") for s in sizes}
            files = []
            for s in sizes:
                plugin.render_pages(pages, build / names[s], page_size=s, settings=settings, ctx=ictx)
                files.append({"path": names[s], "kind": "pdf", "page_size": s,
                              "pages": pdf_info(build / names[s])["pages"], "dpi": dpi})
        previews = _previews(job, build, files, pages, ictx)
        ai = bool(set_doc.get("ai_assisted") or getattr(plugin, "AI_ASSISTED", False))
        reasons += oversize_reasons(build, files)
        generator = {"name": job.niche, "dry_run": job.dry_run}
        if ai:
            generator = {"name": job.niche, "model": set_doc.get("model") or "unknown", "dry_run": job.dry_run}
        manifest = {
            "schema": "manifest/v1",
            "item_id": item_id,
            "niche": job.niche,
            "shop_id": job.shop_id,
            "brief_id": job.brief.get("brief_id"),
            "brief_file": recorded_brief_file(job.brief_file),
            "theme": job.theme,
            "keywords": as_list(job.brief.get("keywords")),
            "audience": job.brief.get("audience"),
            "price_usd": price,
            "created": job.date,
            "ai_assisted": ai,
            "needs_review": bool(reasons),
            "generator": generator,
            "prompts": prompts,
            "files": files,
            "previews": previews,
            "assets": assets,
            "status": "generated",
        }
        if job.niche == "ttrpg":
            manifest["system"] = job.ctx["system"]
        hook = getattr(plugin, "pack_fields", None)
        fields = hook(pages, files, settings=settings, ctx=ictx, set_doc=set_doc) if hook else {"pages": len(pages)}
        types = [p.get("type") for p in pages]
        manifest["pack"] = {
            **fields,
            "size": spec.size,
            "source": spec.source,
            "n": spec.n,
            "set_id": spec.set_id,
            "theme": job.theme,
            "page_ids": [p["page_id"] for p in pages],
            "page_types": types,
            "type_counts": dict(Counter(types)),
            "related": _related(job, spec, item_id, set_doc),
        }
        manifest["review_reasons"] = reasons
        swap_in(build, d, ITEM_SUBDIRS, manifest)
    finally:
        shutil.rmtree(build, ignore_errors=True)


def _printed_text(job: _Job, hook, pages: list[dict], ctx: dict) -> list[str]:
    """The plugin's printed_text(), given the run's settings when it takes them."""
    try:
        takes_settings = "settings" in inspect.signature(hook).parameters
    except (TypeError, ValueError):
        takes_settings = False
    return list(hook(pages, ctx, settings=job.settings) if takes_settings else hook(pages, ctx))


def _text_reasons(job: _Job, pages: list[dict], ctx: dict, prompts: list[str]) -> list[str]:
    """Blocked terms in what is printed or prompted refuse the pack; review terms mark it.
    Game-system ("!") names count as blocked here: designs never carry them, nor any other
    spelling of a system the registry knows (core.compliance.system_mentions: "DnD5e", "PF2e")."""
    reasons = []
    hook = getattr(job.plugin, "printed_text", None)
    printed = _printed_text(job, hook, pages, ctx) if hook else list(iter_strings(
        [{k: v for k, v in p.items() if k not in ("page_id", "asset", "asset_files", "used_in")} for p in pages]))
    found = job.blocklist.check(*printed)
    if found.blocked:
        raise ValueError(f"generated pages contain blocked terms {found.blocked}")
    named = system_mentions(printed)
    if named:
        raise ValueError(f"generated pages name game systems {named}; designs never do")
    if found.flagged:
        reasons.append(f"printed pages mention review terms {found.flagged}")
    check = job.blocklist.check(*prompts) if prompts else ComplianceResult()
    if check.blocked:
        raise ValueError(f"prompts contain blocked terms {check.blocked}")
    named = system_mentions(prompts) if prompts else []
    if named:
        raise ValueError(f"prompts name game systems {named}")
    if check.flagged:
        reasons.append(f"prompts mention review terms {check.flagged}")
    return reasons


def preview_limit(settings: dict) -> int:
    return max(1, int(config(settings).get("preview_pages") or 8))


def _previews(job: _Job, build: Path, files: list[dict], pages: list[dict], ctx: dict) -> list[dict]:
    plugin, settings = job.plugin, job.settings
    hook = getattr(plugin, "manifest_previews", None)
    if hook:
        return hook(build, files, pages, settings=settings, ctx=ctx)
    out_dir = build / "previews"
    limit = preview_limit(settings)
    if hasattr(plugin, "preview"):
        paths = plugin.preview(pages, out_dir, settings=settings, ctx=ctx, limit=limit)
        out = []
        for i, p in enumerate(paths or [], 1):
            p = Path(p)
            if not p.is_absolute():
                p = out_dir / p
            try:
                rel = p.resolve().relative_to(build.resolve()).as_posix()
            except ValueError:
                continue                                  # a preview outside the item is ignored
            with Image.open(p) as im:
                rgb = im.mode != "RGB"
                if rgb:
                    im = im.convert("RGB")
                    im.save(p, "PNG")
            out.append({"path": rel, "page": i})
        if out:
            return out
    return pdf_previews(build, files, limit)


def pdf_previews(build: Path, files: list[dict], limit: int, dpi: int = PREVIEW_DPI) -> list[dict]:
    """RGB PNG renders of the first `limit` pages of the item's first PDF."""
    pdf = next((f for f in files if f.get("kind") == "pdf"), None)
    if pdf is None:
        return []
    n = min(limit, int(pdf.get("pages") or 0))
    out = []
    for i, img in enumerate(render_pdf_pages(build / pdf["path"], dpi=dpi, pages=list(range(n))), 1):
        rel = f"previews/page-{i:02d}.png"
        img.convert("RGB").save(build / rel, "PNG")
        out.append({"path": rel, "page": i})
    return out


def oversize_reasons(build: Path, files: list[dict]) -> list[str]:
    """Review reasons for files above Etsy's per-file limit or more files than it allows."""
    reasons = []
    for f in files:
        size = (build / f["path"]).stat().st_size
        if size > ETSY_MAX_FILE_BYTES:
            reasons.append(f"{f['path']} is {size / 1e6:.1f} MB; Etsy allows {ETSY_MAX_FILE_MB} MB per file")
    if len(files) > ETSY_MAX_FILES:
        reasons.append(f"{len(files)} digital files; Etsy allows {ETSY_MAX_FILES} per listing")
    return reasons


def _related(job: _Job, spec: PackSpec, item_id: str, set_doc: dict) -> list[dict]:
    """The theme's other packs a buyer may also see: this brief's other sizes (planned in
    this call, or made earlier) and, for a derived pack, the item made from its master set.
    same_set: drawn from the same design set, so pages overlap (a derived pack's pages are
    all in its master pack). A listing names the overlap from this list."""
    out: dict[str, dict] = {}
    planned = {s.size: s for s in job.specs}
    for size in job.all_sizes:
        if size == spec.size:
            continue
        other = planned.get(size)
        if other is not None:
            oid, osrc, oset = job.item_id(other), other.source, other.set_id
        else:
            known = job.state.assignment(job.niche, job.brief_id, size)
            if not known or not known.get("item_id"):
                continue                                   # not made yet (a later run makes it)
            oid, osrc, oset = known["item_id"], known.get("source"), known.get("set_id")
        out[oid] = {"size": size, "source": osrc, "item_id": oid, "same_set": _same_set(spec, osrc, oset)}
    if spec.source == "derived":
        for it in set_doc.get("items") or []:
            if it.get("source") == "master" and it.get("item_id") not in out and it.get("item_id") != item_id:
                out[it["item_id"]] = {"size": it.get("size"), "source": "master", "item_id": it["item_id"],
                                      "same_set": True}
    return sorted(out.values(), key=lambda r: (r["size"] or 0, r["item_id"]))


def _same_set(spec: PackSpec, other_source, other_set) -> bool:
    if spec.source == "fresh" or other_source == "fresh":
        return False
    return other_set == spec.set_id
