"""End-to-end coloring and sticker generation in dry-run mode, checked against
docs/CONTRACTS.md section 2. Offline: FakeImageClient, flood-fill background removal."""
import io
import json
import zipfile

import numpy as np
import pytest
from PIL import Image

import generators
from core.render import page_size_points, pdf_info
from generators import coloring_pages, stickers
from generators.imaging.client import FakeImageClient, ImageCapExceeded, ImageGenerationError
from generators.imaging.prompts import BlockedPromptError

REQUIRED = {"schema", "item_id", "niche", "shop_id", "brief_id", "brief_file", "theme", "keywords", "audience",
            "price_usd", "created", "ai_assisted", "needs_review", "generator", "prompts", "files", "previews",
            "assets", "status"}
KINDS = {"pdf", "png", "png_transparent", "zip", "svg"}


def check_contract(d, niche, settings):
    m = json.loads((d / "manifest.json").read_text())
    assert REQUIRED <= set(m)
    assert m["schema"] == "manifest/v1" and m["niche"] == niche and m["status"] == "generated"
    assert m["item_id"] == d.name and m["item_id"].startswith(f"2026-10-15-{niche}-")
    assert m["ai_assisted"] is True
    assert set(m["generator"]) >= {"name", "model", "dry_run"} and m["generator"]["name"] == niche
    assert m["prompts"] and all(isinstance(p, str) for p in m["prompts"])
    for entry in m["files"] + m["previews"] + m["assets"]:
        assert not entry["path"].startswith("/") and (d / entry["path"]).is_file()
    for entry in m["files"] + m["assets"]:
        assert entry["kind"] in KINDS
    for f in m["files"]:
        if f["kind"] == "pdf":
            info = pdf_info(d / f["path"])
            w, h = page_size_points(f["page_size"], settings)
            assert info["pages"] == f["pages"]
            assert all(abs(pw - w) < 0.01 and abs(ph - h) < 0.01 for pw, ph in info["sizes_pt"])
    for p in m["previews"]:
        assert isinstance(p["page"], int) and Image.open(d / p["path"]).mode == "RGB"
    return m


# --- coloring pages ------------------------------------------------------------------------

def test_coloring_pack_matches_contract(tmp_path, settings, coloring_brief):
    fake = FakeImageClient("coloring", long_side_px=480)
    (d,) = coloring_pages.generate(coloring_brief, brief_file="data/briefs/coloring_pages-2026-10-15.json",
                                   out_root=tmp_path, date="2026-10-15", dry_run=True, client=fake,
                                   settings=settings)
    assert d.parent == tmp_path
    m = check_contract(d, "coloring_pages", settings)
    assert m["brief_file"] == "data/briefs/coloring_pages-2026-10-15.json"
    assert m["generator"] == {"name": "coloring_pages", "model": "fake-image", "dry_run": True}
    assert m["needs_review"] is False and m["price_usd"] == 4.0
    assert [f["page_size"] for f in m["files"]] == ["us_letter", "a4"]
    assert all(f["kind"] == "pdf" and f["pages"] == 3 and f["dpi"] == 300 for f in m["files"])
    assert len(m["previews"]) == 3 and [p["page"] for p in m["previews"]] == [1, 2, 3]
    assert sorted(a["kind"] for a in m["assets"]) == ["png"] * 3 + ["svg"] * 3
    assert m["prompts"] == [c["prompt"] for c in fake.calls]
    assert len({c["subject"] for c in m["pack"]["contents"]}) == 3
    assert {c["aspect_ratio"] for c in fake.calls} == {"3:4"} and {c["image_size"] for c in fake.calls} == {"4K"}
    for a in m["assets"]:
        if a["kind"] == "png":
            assert set(np.unique(np.asarray(Image.open(d / a["path"])))) <= {0, 255}


def test_coloring_dry_run_caps_pages_and_uses_fake_client(tmp_path, settings, coloring_brief):
    coloring_brief["page_count"] = 20
    settings["coloring_pages"] = {"dry_run_max_pages": 2}
    (d,) = coloring_pages.generate(coloring_brief, out_root=tmp_path, date="2026-10-15", dry_run=True,
                                   settings=settings)
    m = json.loads((d / "manifest.json").read_text())
    assert m["files"][0]["pages"] == 2
    assert m["pack"]["requested_pages"] == 20 and m["pack"]["dry_run_capped"] is True
    assert m["generator"]["model"] == "fake-image"


def test_coloring_lines_print_at_min_weight(tmp_path, settings, coloring_brief):
    coloring_brief["page_count"] = 1
    (d,) = coloring_pages.generate(coloring_brief, out_root=tmp_path, date="2026-10-15", dry_run=True,
                                   settings=settings)
    m = json.loads((d / "manifest.json").read_text())
    page = m["pack"]["contents"][0]
    a4_box_w = page_size_points("a4", settings)[0] - 72        # 0.5in margins
    assert page["min_line_px"] * a4_box_w / page["size_px"][0] >= settings["print"]["min_line_weight_pt"] - 1e-6


def test_rerun_is_reproducible_and_replaces_files(tmp_path, settings, coloring_brief):
    args = dict(out_root=tmp_path, date="2026-10-15", dry_run=True, settings=settings)
    (d,) = coloring_pages.generate(coloring_brief, **args)
    first = (d / "files/farm-animals-coloring-pages-us_letter.pdf").read_bytes()
    (d / "previews/stale.png").write_bytes(b"x")
    coloring_pages.generate(coloring_brief, **args)
    assert (d / "files/farm-animals-coloring-pages-us_letter.pdf").read_bytes() == first
    assert not (d / "previews/stale.png").exists()


def test_blocked_brief_never_reaches_the_model(tmp_path, settings, coloring_brief):
    fake = FakeImageClient("coloring")
    with pytest.raises(ValueError, match="blocked"):
        coloring_pages.generate({**coloring_brief, "theme": "Paw Patrol pups"}, out_root=tmp_path,
                                dry_run=True, client=fake, settings=settings)
    assert fake.calls == [] and not any(tmp_path.iterdir())


def test_blocked_prompt_raises_before_the_model(tmp_path, settings, coloring_brief, monkeypatch):
    """Even if a blocked term slipped past the brief check (e.g. via the subject table),
    the prompt guard refuses before the client is called."""
    from generators.imaging import prompts
    monkeypatch.setattr(prompts, "subject_pool", lambda theme, keywords: ["a Pikachu", "a cow", "a pig"])
    fake = FakeImageClient("coloring")
    with pytest.raises(BlockedPromptError):
        coloring_pages.generate(coloring_brief, out_root=tmp_path, dry_run=True, client=fake, settings=settings)
    assert fake.calls == []


def test_flagged_term_sets_needs_review(tmp_path, settings, coloring_brief):
    coloring_brief["style_notes"] = "gentle storybook look like Peter Rabbit"
    (d,) = coloring_pages.generate(coloring_brief, out_root=tmp_path, date="2026-10-15", dry_run=True,
                                   settings=settings)
    m = json.loads((d / "manifest.json").read_text())
    assert m["needs_review"] is True and any("Peter Rabbit" in r for r in m["review_reasons"])


class RefusesFirst(FakeImageClient):
    """Fake that refuses the first prompt, like a model's safety filter would."""

    def generate(self, prompt, *, aspect_ratio, image_size):
        if not self.calls:
            self.calls.append({"prompt": prompt, "aspect_ratio": aspect_ratio, "image_size": image_size})
            raise ImageGenerationError("model returned no image (finish_reason=IMAGE_SAFETY)")
        return super().generate(prompt, aspect_ratio=aspect_ratio, image_size=image_size)


def test_refused_subject_is_replaced_by_a_spare(tmp_path, settings, coloring_brief):
    coloring_brief["page_count"] = 2
    (d,) = coloring_pages.generate(coloring_brief, out_root=tmp_path, date="2026-10-15", dry_run=True,
                                   client=RefusesFirst("coloring", long_side_px=400), settings=settings)
    m = json.loads((d / "manifest.json").read_text())
    assert m["files"][0]["pages"] == 2
    assert m["pack"]["skipped"][0]["subject"] == "a friendly cow"
    assert [c["subject"] for c in m["pack"]["contents"]] == ["a happy pig in a mud puddle",
                                                             "a chicken with her chicks"]


def test_too_many_failures_fail_the_item(tmp_path, settings, coloring_brief):
    settings["coloring_pages"] = {"max_failures": 0}
    with pytest.raises(ImageGenerationError):
        coloring_pages.generate(coloring_brief, out_root=tmp_path, date="2026-10-15", dry_run=True,
                                client=RefusesFirst("coloring", long_side_px=400), settings=settings)
    assert not list(tmp_path.glob("*/manifest.json"))


def test_image_cap_stops_before_spending(tmp_path, settings, coloring_brief):
    fake = FakeImageClient("coloring", max_images=2)
    with pytest.raises(ImageCapExceeded):
        coloring_pages.generate(coloring_brief, out_root=tmp_path, dry_run=True, client=fake, settings=settings)
    assert fake.calls == []


def test_drafted_item_is_left_alone(tmp_path, settings, coloring_brief):
    (d,) = coloring_pages.generate(coloring_brief, out_root=tmp_path, date="2026-10-15", dry_run=True,
                                   settings=settings)
    (d / "publish.json").write_text("{}")
    fake = FakeImageClient("coloring")
    assert coloring_pages.generate(coloring_brief, out_root=tmp_path, date="2026-10-15", dry_run=True,
                                   client=fake, settings=settings) == [d]
    assert fake.calls == []


def test_single_page_size_when_not_delivering_both(tmp_path, settings, coloring_brief):
    settings["print"]["deliver_both_sizes"] = False
    coloring_brief["page_count"] = 1
    (d,) = coloring_pages.generate(coloring_brief, out_root=tmp_path, date="2026-10-15", dry_run=True,
                                   settings=settings)
    m = json.loads((d / "manifest.json").read_text())
    assert [f["page_size"] for f in m["files"]] == ["us_letter"]


# --- stickers ------------------------------------------------------------------------------

def test_sticker_pack_matches_contract(tmp_path, settings, sticker_brief):
    fake = FakeImageClient("sticker", long_side_px=480)
    (d,) = stickers.generate(sticker_brief, out_root=tmp_path, date="2026-10-15", dry_run=True, client=fake,
                             settings=settings)
    m = check_contract(d, "stickers", settings)
    assert len(m["files"]) <= 5
    zips = [f for f in m["files"] if f["kind"] == "zip"]
    pdfs = [f for f in m["files"] if f["kind"] == "pdf"]
    assert len(zips) == 1 and [f["page_size"] for f in pdfs] == ["us_letter", "a4"]
    assert all(f["pages"] == 1 for f in pdfs)                       # 4 stickers fit one 3x4 sheet
    with zipfile.ZipFile(d / zips[0]["path"]) as z:
        names = z.namelist()
        assert names == [f"kawaii-food-sticker-{i:02d}.png" for i in range(1, 5)]
        assert all(Image.open(z.open(n)).mode == "RGBA" for n in names)
    assets = m["assets"]
    assert len(assets) == 4 and {a["kind"] for a in assets} == {"png_transparent"}
    for a in assets:
        img = Image.open(d / a["path"])
        alpha = np.asarray(img.getchannel("A"))
        assert img.mode == "RGBA" and round(img.info["dpi"][0]) == 300
        assert alpha[0, 0] == 0 and alpha[-1, -1] == 0                # transparent corners
        assert alpha[alpha.shape[0] // 2, alpha.shape[1] // 2] == 255  # opaque subject
    assert m["previews"][0] == {"path": "previews/sheet-01.png", "page": 1}
    assert [p.get("sticker") for p in m["previews"][1:]] == [1, 2, 3, 4]
    assert m["pack"]["background_removal"] == "floodfill"
    assert all("transparent" not in p for p in m["prompts"])          # models paint fake checkerboards
    assert {c["aspect_ratio"] for c in fake.calls} == {"1:1"} and {c["image_size"] for c in fake.calls} == {"2K"}


def test_sticker_sheets_paginate(tmp_path, settings, sticker_brief):
    sticker_brief["page_count"] = 5
    settings["stickers"]["sheet"] = {"cols": 2, "rows": 2, "gap_in": 0.25}
    (d,) = stickers.generate(sticker_brief, out_root=tmp_path, date="2026-10-15", dry_run=True, settings=settings)
    m = json.loads((d / "manifest.json").read_text())
    assert [f["pages"] for f in m["files"] if f["kind"] == "pdf"] == [2, 2]
    assert [p["page"] for p in m["previews"] if "sticker" not in p] == [1, 2]


def test_sticker_default_count_and_dry_run_cap(tmp_path, settings, sticker_brief):
    del sticker_brief["page_count"]
    (d,) = stickers.generate(sticker_brief, out_root=tmp_path, date="2026-10-15", dry_run=True, settings=settings)
    m = json.loads((d / "manifest.json").read_text())
    assert m["pack"]["requested_stickers"] == 12
    assert m["pack"]["stickers"] == stickers.DEFAULTS["dry_run_max_stickers"]


def test_wrong_niche_is_rejected(tmp_path, settings, coloring_brief):
    with pytest.raises(ValueError, match="niche"):
        stickers.generate(coloring_brief, out_root=tmp_path, dry_run=True, settings=settings)


# --- dispatch and CLI ----------------------------------------------------------------------

def test_generators_dispatch(tmp_path, settings, coloring_brief, sticker_brief):
    coloring_brief["page_count"] = 1
    sticker_brief["page_count"] = 1
    (a,) = generators.generate(coloring_brief, out_root=tmp_path, date="2026-10-15", dry_run=True, settings=settings)
    (b,) = generators.generate(sticker_brief, out_root=tmp_path, date="2026-10-15", dry_run=True, settings=settings)
    assert "coloring_pages" in a.name and "stickers" in b.name


@pytest.mark.parametrize("module,niche", [("generators.coloring_pages.__main__", "coloring_pages"),
                                          ("generators.stickers.__main__", "stickers")])
def test_cli_dry_run(tmp_path, capsys, monkeypatch, coloring_brief, sticker_brief, module, niche):
    import importlib

    monkeypatch.setenv("PIPELINE_DATE", "2026-10-15")
    coloring_brief["page_count"] = 1
    sticker_brief["page_count"] = 1
    brief_file = tmp_path / "briefs.json"
    brief_file.write_text(json.dumps({"schema": "brief/v1", "shop_id": "main", "min_listings_per_pattern": 10,
                                      "briefs": [coloring_brief, sticker_brief,
                                                 {**coloring_brief, "brief_id": "x", "theme": "Pokemon"}]}))
    out = tmp_path / "out"
    rc = importlib.import_module(module).main(["--brief", str(brief_file), "--all", "--dry-run", "--out", str(out)])
    printed = capsys.readouterr()
    dirs = printed.out.split()
    if niche == "coloring_pages":
        assert rc == 1 and "blocked terms" in printed.err             # the Pokemon brief is skipped
    else:
        assert rc == 0
    assert len(dirs) == 1 and niche in dirs[0] and (out / dirs[0].split("/")[-1] / "manifest.json").exists()


# --- real client path, mocked SDK ------------------------------------------------------------

class ArtSDK:
    """Stands in for google.genai.Client: answers generate_content with fake art as PNG bytes."""

    def __init__(self, style):
        from google.genai import types

        fake = FakeImageClient(style, long_side_px=400)
        self.calls = []

        def generate_content(*, model, contents, config):
            self.calls.append({"model": model, "config": config})
            buf = io.BytesIO()
            fake.generate(contents, aspect_ratio=config.image_config.aspect_ratio,
                          image_size=config.image_config.image_size).save(buf, "PNG")
            part = types.Part(inline_data=types.Blob(data=buf.getvalue(), mime_type="image/png"))
            return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(parts=[part]))])

        self.models = type("Models", (), {"generate_content": staticmethod(generate_content)})()


@pytest.mark.parametrize("module,brief_name,style", [(coloring_pages, "coloring_brief", "coloring"),
                                                     (stickers, "sticker_brief", "sticker")])
def test_non_dry_run_through_gemini_client(tmp_path, settings, request, module, brief_name, style):
    from generators.imaging.client import GeminiImageClient

    brief = {**request.getfixturevalue(brief_name), "page_count": 2}
    settings["vectorize"]["stickers"]["background_removal"] = "floodfill"     # never download rembg in tests
    sdk = ArtSDK(style)
    client = GeminiImageClient.from_settings(settings, sdk_client=sdk)
    (d,) = module.generate(brief, out_root=tmp_path, date="2026-10-15", dry_run=False, client=client,
                           settings=settings)
    m = check_contract(d, module.NICHE, settings)
    assert m["generator"] == {"name": module.NICHE, "model": settings["models"]["image"]["model"], "dry_run": False}
    assert len(sdk.calls) == 2 and client.used == 2
    assert {c["model"] for c in sdk.calls} == {settings["models"]["image"]["model"]}


def test_more_files_than_etsy_allows_is_refused_up_front(tmp_path, settings, sticker_brief):
    extra = {f"size{i}": {"width_in": 5.0, "height_in": 7.0} for i in range(4)}
    settings["print"]["page_sizes"].update(extra)
    sticker_brief["page_sizes"] = ["us_letter", "a4", *extra]
    fake = FakeImageClient("sticker")
    with pytest.raises(ValueError, match="Etsy's limit"):
        stickers.generate(sticker_brief, out_root=tmp_path, dry_run=True, client=fake, settings=settings)
    assert fake.calls == []


# --- review fixes: reruns, brief validation, Etsy file limits ------------------------------

class DiesAfterFirst(FakeImageClient):
    """Fake whose network drops after the first image."""

    def generate(self, prompt, *, aspect_ratio, image_size):
        if self.calls:
            raise ConnectionError("network down")
        return super().generate(prompt, aspect_ratio=aspect_ratio, image_size=image_size)


@pytest.mark.parametrize("module,brief_name,style", [(coloring_pages, "coloring_brief", "coloring"),
                                                     (stickers, "sticker_brief", "sticker")])
def test_failed_rerun_leaves_the_previous_item_intact(tmp_path, settings, request, module, brief_name, style):
    brief = {**request.getfixturevalue(brief_name), "page_count": 2}
    args = dict(out_root=tmp_path, date="2026-10-15", dry_run=True, settings=settings)
    (d,) = module.generate(brief, **args)
    before = (d / "manifest.json").read_text()
    with pytest.raises(ConnectionError):
        module.generate(brief, client=DiesAfterFirst(style, long_side_px=300), **args)
    assert (d / "manifest.json").read_text() == before
    m = json.loads(before)
    assert all((d / e["path"]).is_file() for e in m["files"] + m["previews"] + m["assets"])
    assert [p.name for p in tmp_path.iterdir()] == [d.name]          # no scratch build folder left behind


def test_failed_first_run_leaves_nothing(tmp_path, settings, coloring_brief):
    with pytest.raises(ConnectionError):
        coloring_pages.generate(coloring_brief, out_root=tmp_path, date="2026-10-15", dry_run=True,
                                client=DiesAfterFirst("coloring", long_side_px=300), settings=settings)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("change,match", [
    ({"compliance": {"blocked": ["Bluey"], "flagged": []}}, "marked with blocked terms"),
    ({"keywords": ["farm animals", "see www.example.com/listing/1"]}, "URLs"),
])
def test_brief_marks_and_urls_are_refused(tmp_path, settings, coloring_brief, change, match):
    fake = FakeImageClient("coloring")
    with pytest.raises(ValueError, match=match):
        coloring_pages.generate({**coloring_brief, **change}, out_root=tmp_path, date="2026-10-15", dry_run=True,
                                client=fake, settings=settings)
    assert fake.calls == []


@pytest.mark.parametrize("date", ["../../escape", "2026-13-45", "20261015"])
def test_bad_dates_are_refused(tmp_path, settings, coloring_brief, date):
    with pytest.raises(ValueError, match="date"):
        coloring_pages.generate(coloring_brief, out_root=tmp_path, date=date, dry_run=True, settings=settings)
    assert list(tmp_path.iterdir()) == []


def test_unknown_shop_is_refused(tmp_path, settings, sticker_brief):
    with pytest.raises(ValueError, match="unknown shop"):
        stickers.generate(sticker_brief, shop_id="nope", out_root=tmp_path, date="2026-10-15", dry_run=True,
                          settings=settings)


def test_dry_run_never_uses_an_injected_gemini_client(tmp_path, settings, coloring_brief):
    from generators.imaging.client import GeminiImageClient

    sdk = ArtSDK("coloring")
    real = GeminiImageClient.from_settings(settings, sdk_client=sdk)
    (d,) = coloring_pages.generate({**coloring_brief, "page_count": 1}, out_root=tmp_path, date="2026-10-15",
                                   dry_run=True, client=real, settings=settings)
    assert sdk.calls == [] and real.used == 0
    assert json.loads((d / "manifest.json").read_text())["generator"]["model"] == "fake-image"


def test_string_keywords_stay_one_keyword(tmp_path, settings, coloring_brief):
    coloring_brief.update(page_count=1, keywords="farm animal coloring pages")
    (d,) = coloring_pages.generate(coloring_brief, out_root=tmp_path, date="2026-10-15", dry_run=True,
                                   settings=settings)
    assert json.loads((d / "manifest.json").read_text())["keywords"] == ["farm animal coloring pages"]


def test_sticker_zip_is_split_below_the_per_file_limit(tmp_path, settings, sticker_brief):
    sticker_brief["page_count"] = 5
    args = dict(out_root=tmp_path, date="2026-10-15", dry_run=True, settings=settings)
    (d,) = stickers.generate(sticker_brief, **args)
    biggest = max((d / a["path"]).stat().st_size for a in json.loads((d / "manifest.json").read_text())["assets"])
    settings["stickers"]["zip_max_mb"] = 2.2 * (biggest + 200) / 1e6      # room for two stickers per part
    stickers.generate(sticker_brief, **args)
    m = check_contract(d, "stickers", settings)
    zips = [f for f in m["files"] if f["kind"] == "zip"]
    assert len(zips) > 1 and len(m["files"]) <= 5
    assert [z["part"] for z in zips] == list(range(1, len(zips) + 1)) and {z["parts"] for z in zips} == {len(zips)}
    names = []
    for z in zips:
        with zipfile.ZipFile(d / z["path"]) as archive:
            names += archive.namelist()
            assert len(archive.namelist()) == z["count"]
    assert names == [f"kawaii-food-sticker-{i:02d}.png" for i in range(1, 6)]   # every sticker, in order, once
    assert m["pack"]["zip_parts"] == len(zips) == 3
    assert all((d / z["path"]).stat().st_size <= settings["stickers"]["zip_max_mb"] * 1e6 for z in zips)


def test_too_many_zip_parts_mark_the_item_for_review(tmp_path, settings, sticker_brief):
    sticker_brief["page_count"] = 5
    settings["stickers"]["zip_max_mb"] = 0.000001             # one sticker per part: 5 ZIPs + 2 PDFs
    (d,) = stickers.generate(sticker_brief, out_root=tmp_path, date="2026-10-15", dry_run=True, settings=settings)
    m = json.loads((d / "manifest.json").read_text())
    assert m["needs_review"] is True and any("Etsy allows 5 per listing" in r for r in m["review_reasons"])


class TwoBlobs(FakeImageClient):
    """Sticker art in two parts far apart, like "two cherries" drawn on opposite sides."""

    def generate(self, prompt, *, aspect_ratio, image_size):
        super().generate(prompt, aspect_ratio=aspect_ratio, image_size=image_size)
        img = Image.new("RGB", (400, 400), (255, 255, 255))
        from PIL import ImageDraw
        d = ImageDraw.Draw(img)
        d.ellipse([20, 150, 120, 250], fill=(220, 30, 30), outline=(20, 20, 20), width=4)
        d.ellipse([280, 150, 380, 250], fill=(220, 30, 30), outline=(20, 20, 20), width=4)
        return img


def test_sticker_in_separate_pieces_is_flagged(tmp_path, settings, sticker_brief):
    sticker_brief["page_count"] = 1
    (d,) = stickers.generate(sticker_brief, out_root=tmp_path, date="2026-10-15", dry_run=True,
                             client=TwoBlobs("sticker"), settings=settings)
    m = json.loads((d / "manifest.json").read_text())
    assert m["pack"]["contents"][0]["pieces"] == 2
    assert m["needs_review"] is True and any("separate die-cut pieces" in r for r in m["review_reasons"])


def test_model_refusal_text_never_reaches_the_manifest(tmp_path, settings, coloring_brief):
    """A refusal that names a brand would otherwise make QA's blocklist check fail the item."""
    from google.genai import types

    from generators.imaging.client import GeminiImageClient

    sdk = ArtSDK("coloring")
    answer = sdk.models.generate_content
    first = {"done": False}

    def refuse_once(*, model, contents, config):
        if not first["done"]:
            first["done"] = True
            return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(
                parts=[types.Part(text="I can't draw Mickey Mouse.")]))])
        return answer(model=model, contents=contents, config=config)

    sdk.models = type("Models", (), {"generate_content": staticmethod(refuse_once)})()
    client = GeminiImageClient.from_settings(settings, sdk_client=sdk)
    (d,) = coloring_pages.generate({**coloring_brief, "page_count": 1}, out_root=tmp_path, date="2026-10-15",
                                   client=client, settings=settings)
    text = (d / "manifest.json").read_text()
    assert "Mickey" not in text and json.loads(text)["pack"]["skipped"][0]["subject"] == "a friendly cow"


def test_file_names_fit_etsy_limits(tmp_path, settings, coloring_brief, sticker_brief):
    """Etsy: at most 70 letters, digits, '.', '_' or '-' per file name, not editable later."""
    import re

    long_theme = "very long theme name about happy farm animals on a sunny summer day in the countryside"
    settings["print"]["page_sizes"]["us_half_letter_landscape"] = {"width_in": 8.5, "height_in": 5.5}
    dirs = []
    for module, brief in ((coloring_pages, coloring_brief), (stickers, sticker_brief)):
        brief.update(theme=long_theme, page_count=1, page_sizes=["us_letter", "us_half_letter_landscape"])
        dirs += module.generate(brief, out_root=tmp_path, date="2026-10-15", dry_run=True, settings=settings)
    for d in dirs:
        for f in json.loads((d / "manifest.json").read_text())["files"]:
            name = f["path"].split("/", 1)[1]
            assert len(name) <= 70 and re.fullmatch(r"[A-Za-z0-9._-]+", name), name
            assert (d / f["path"]).is_file()


def test_real_runs_use_rembg_with_the_pinned_model(tmp_path, settings, sticker_brief, monkeypatch):
    """Non-dry runs follow vectorize.stickers.background_removal (rembg), with the
    commercially usable model, not rembg's own non-commercial default. Stubbed: no download."""
    from generators.imaging import cleanup
    from generators.imaging.client import GeminiImageClient

    seen = []

    def fake_rembg(img, model=None):
        seen.append(model)
        return cleanup.remove_background_floodfill(img)

    monkeypatch.setattr(cleanup, "remove_background_rembg", fake_rembg)
    settings["vectorize"]["stickers"]["background_removal"] = "rembg"
    client = GeminiImageClient.from_settings(settings, sdk_client=ArtSDK("sticker"))
    (d,) = stickers.generate({**sticker_brief, "page_count": 1}, out_root=tmp_path, date="2026-10-15",
                             client=client, settings=settings)
    m = json.loads((d / "manifest.json").read_text())
    assert seen == ["isnet-general-use"]
    assert m["pack"]["background_removal"] == "rembg" and m["pack"]["rembg_model"] == "isnet-general-use"
