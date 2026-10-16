"""windows/botsy_helper.py: sniffing, finding, uploading, fetching, config and the lock.

All offline. sftp is a fake (see conftest.FakeSftp); every file lives in tmp_path. The
CSV rows are invented.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from tests.windows.conftest import FakeSftp

EVERBEE = ("Product Name,Shop Name,Price,Mo. Sales,Mo. Revenue,Reviews,Favorites,"
           "Listing Age,Tags,Category\r\n"
           "Farm animal coloring pages,ShopOne,3.99,120,478.8,40,300,200 days,farm,Coloring\r\n")
ERANK = ("Keywords,Avg. Searches,Avg. Clicks,Avg. CTR,Etsy Competition,Google Searches\n"
         "axolotl coloring pages,1200,800,66%,5400,900\n")
BANK = ("Transaction Date,Post Date,Description,Category,Type,Amount,Memo\n"
        "10/01/2026,10/02/2026,GROCERY STORE,Groceries,Sale,-54.20,\n")
OWN_LISTINGS = ("TITLE,DESCRIPTION,PRICE,CURRENCY_CODE,QUANTITY,TAGS,MATERIALS,IMAGE1,SKU\n"
                "My planner,Nice,4.00,USD,999,planner,,https://example.invalid/a.jpg,\n")
AMAZON_ORDERS = ("Order Date,Order ID,Title,Category,ASIN/ISBN,Purchase Price Per Unit,Quantity\n"
                 "10/01/26,111-222,Book,Paperback,123,9.99,1\n")
# Header spellings from eRank's help center ("Top Listings" tab).
ERANK_TOP = ("Listing,Age (Days),Views,Daily Views,Est. Sales,Price,Est. Revenue,Hearts,Tags\n"
             "Cute frog sticker sheet,120,3400,28,45,2.99,134.55,210,frog\n")
# The shop's own Etsy Ads download and sold-orders download (buyer names, addresses).
ETSY_ADS = ("Listing,Views,Clicks,Click rate,Orders,Revenue,Spend,ROAS\n"
            "My planner,100,4,4%,1,4.00,1.20,3.3\n")
SOLD_ORDERS = ("Sale Date,Item Name,Buyer,Quantity,Price,Coupon Code,Order ID,Ship Name,"
               "Ship Address1,Ship City,Ship Zipcode\n"
               "10/01/26,Planner,buyer1,1,4.00,,1,A Person,1 Road,Town,12345\n")


def write(path: Path, text: str | bytes, age_s: float = 3600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, str):
        path.write_text(text, encoding="utf-8", newline="")
    else:
        path.write_bytes(text)
    t = time.time() - age_s
    os.utime(path, (t, t))
    return path


# ------------------------------------------------------------------ looks_like_export

@pytest.mark.parametrize("name,data", [
    ("everbee", EVERBEE.encode("utf-8")),
    ("erank", ERANK.encode("utf-8")),
    ("bom", b"\xef\xbb\xbf" + EVERBEE.encode("utf-8")),
    ("utf16", ERANK.replace(",", "\t").encode("utf-16")),          # Excel "Unicode text"
    ("utf16le-no-bom", ERANK.encode("utf-16-le")),
    ("cp1252", ("Title,Price,Hearts\nCaf\u00e9 stickers \u2013 cute,2.50,40\n").encode("cp1252")),
    ("preamble", ("EverBee Product Analytics\nExported 2026-10-15\n\n\n" + EVERBEE).encode()),
    ("semicolon", "Title;Price;Mo. Sales\nx;1;2\n".encode()),
    ("odd-spacing", '" PRODUCT  NAME ","mo sales",price\nx,1,2\n'.encode()),
    ("keyword-only", "Keyword,Searches,Competition\nfrog stickers,500,2000\n".encode()),
    ("erank-top-listings", ERANK_TOP.encode()),
])
def test_looks_like_export_accepts_research_exports(bh, tmp_path, name, data):
    assert bh.looks_like_export(write(tmp_path / f"{name}.csv", data)) is True


@pytest.mark.parametrize("name,data", [
    ("bank", BANK.encode()),
    ("random", b"id,name,email\n1,Ann,ann@example.invalid\n"),
    ("empty", b""),
    ("binary", b"PK\x03\x04\x14\x00\x06\x00" + bytes(range(256)) * 4),   # an .xlsx renamed
    ("own-listings", OWN_LISTINGS.encode()),        # generic headers only
    ("amazon", AMAZON_ORDERS.encode()),
    ("one-known", b"Date,Description,Price\n1,2,3\n"),
    ("split-over-lines", b"Title,foo\nMo. Sales,bar\n"),
    ("header-too-late", ("note\n" * 25 + EVERBEE).encode()),
    ("words-in-a-row", b"I like Mo. Sales and Favorites a lot\n"),
    ("etsy-ads", ETSY_ADS.encode()),
    ("sold-orders", SOLD_ORDERS.encode()),
])
def test_looks_like_export_rejects_everything_else(bh, tmp_path, name, data):
    assert bh.looks_like_export(write(tmp_path / f"{name}.csv", data)) is False


def test_looks_like_export_missing_file(bh, tmp_path):
    assert bh.looks_like_export(tmp_path / "nope.csv") is False


# ------------------------------------------------------------------ find_candidates

def test_find_candidates_filters_age_state_and_names(bh, tmp_path):
    d = tmp_path / "Downloads"
    new = write(d / "new.csv", EVERBEE)
    write(d / "old.csv", ERANK, age_s=20 * 86400)
    done = write(d / "done.csv", "Title,Hearts\nx,1\n")
    write(d / "notes.txt", EVERBEE)
    write(d / ".hidden.csv", EVERBEE + "x")
    write(d / "~$locked.csv", EVERBEE + "y")
    write(d / "empty.csv", "")
    (d / "folder.csv").mkdir()
    state = {bh.sha256_file(done): {"name": "done.csv", "uploaded_at": "2026-10-01T00:00:00+00:00"}}
    found = bh.find_candidates(d, state, 14)
    assert [c.path for c in found] == [new]
    assert found[0].sha256 == bh.sha256_file(new)


def test_find_candidates_skips_redownloaded_copy(bh, tmp_path):
    d = tmp_path / "Downloads"
    first = write(d / "export.csv", EVERBEE, age_s=100)
    write(d / "export (1).csv", EVERBEE, age_s=50)
    found = bh.find_candidates(d, {}, 14)
    assert [c.path for c in found] == [first]  # same content: offered once
    state = {found[0].sha256: {"name": "export.csv", "uploaded_at": "x"}}
    assert bh.find_candidates(d, state, 14) == []


def test_find_candidates_waits_for_stable_files(bh, tmp_path):
    d = tmp_path / "Downloads"
    p = write(d / "growing.csv", EVERBEE, age_s=0)
    seen: dict = {}
    assert bh.find_candidates(d, {}, 14, seen=seen) == []          # modified < 5 s ago
    now = time.time() + 60
    with open(p, "a", encoding="utf-8") as fh:                       # still being written
        fh.write("more,rows\n")
    t = now - 30
    os.utime(p, (t, t))
    assert bh.find_candidates(d, {}, 14, seen=seen, now=now) == []  # size changed since last poll
    found = bh.find_candidates(d, {}, 14, seen=seen, now=now + 30)  # same size two polls running
    assert [c.path for c in found] == [p]


def test_find_candidates_skips_symlinks(bh, tmp_path):
    d = tmp_path / "Downloads"
    target = write(tmp_path / "elsewhere" / "export.csv", EVERBEE)
    d.mkdir()
    try:
        (d / "link.csv").symlink_to(target)
    except OSError:
        pytest.skip("symlinks not available here")
    assert bh.find_candidates(d, {}, 14) == []


def test_find_candidates_size_limit_and_missing_dir(bh, tmp_path):
    d = tmp_path / "Downloads"
    write(d / "big.csv", EVERBEE)
    assert bh.find_candidates(d, {}, 14, max_bytes=10) == []
    assert bh.find_candidates(tmp_path / "missing", {}, 14) == []


def test_find_candidates_reuses_hash(bh, tmp_path, monkeypatch):
    d = tmp_path / "Downloads"
    write(d / "a.csv", EVERBEE)
    seen: dict = {}
    bh.find_candidates(d, {}, 14, seen=seen)
    calls = []
    real = bh.sha256_file
    monkeypatch.setattr(bh, "sha256_file", lambda p: calls.append(p) or real(p))
    assert len(bh.find_candidates(d, {}, 14, seen=seen)) == 1
    assert calls == []


# ------------------------------------------------------------------ upload

def test_remote_name_is_safe_and_unique(bh):
    sha = "1a2b3c4d" + "0" * 56
    assert bh.remote_name("EverBee Export (3).csv", sha) == "EverBee-Export-3-1a2b3c4d.csv"
    assert bh.remote_name("..\\..\\évil \"name\";rm.CSV", sha) == "evil-name-rm-1a2b3c4d.csv"
    assert bh.remote_name("....csv", sha) == "export-1a2b3c4d.csv"
    assert bh.remote_name("coloring_pages__erank.csv", sha) == "coloring_pages__erank-1a2b3c4d.csv"
    other = "ffffffff" + "0" * 56
    assert bh.remote_name("x.csv", sha) != bh.remote_name("x.csv", other)
    assert len(bh.remote_name("a" * 300 + ".csv", sha)) <= 100


def test_upload_builds_sftp_argv_and_batch(bh, cfg, fake_sftp, winenv, tmp_path):
    src = write(Path(winenv["USERPROFILE"]) / "Downloads" / "EverBee Export.csv", EVERBEE)
    before = (src.read_bytes(), src.stat().st_mtime_ns)
    sha = bh.sha256_file(src)
    res = bh.upload(src, cfg, sha256=sha)
    assert res.ok and res.remote_name == f"EverBee-Export-{sha[:8]}.csv"
    call = fake_sftp.calls[0]
    argv = call["argv"]
    key = str(Path(winenv["USERPROFILE"]) / ".ssh" / "botsy_upload")
    assert argv[0] == "C:/fake/sftp.exe"
    assert argv[1] == "-b" and argv[3:5] == ["-i", key]
    assert argv[5:9] == ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new"]
    assert argv[-1] == "botsy-upload@203.0.113.7"
    assert "-P" not in argv
    name = res.remote_name
    assert call["batch"] == [f'put "{name}" "incoming/{name}.part"',
                             f'rename "incoming/{name}.part" "incoming/{name}"']
    assert call["sent"] == {name: EVERBEE.encode()}
    assert call["kwargs"]["timeout"] == 300
    assert call["kwargs"]["stdin"] is bh.subprocess.DEVNULL
    assert not Path(call["kwargs"]["cwd"]).exists()                   # temp folder cleaned up
    assert (src.read_bytes(), src.stat().st_mtime_ns) == before       # user file untouched


def test_sftp_batch_options_and_output_files(bh, cfg, fake_sftp, winenv):
    src = write(Path(winenv["USERPROFILE"]) / "Downloads" / "e.csv", ERANK)
    assert bh.upload(src, cfg).ok
    call = fake_sftp.calls[0]
    assert b"\r" not in call["batch_bytes"] and call["batch_bytes"].endswith(b"\n")
    argv = call["argv"]
    for opt in ("IdentitiesOnly=yes", "ConnectTimeout=30", "ServerAliveInterval=15",
                "ServerAliveCountMax=4"):
        assert argv[argv.index(opt) - 1] == "-o"
    # Output goes to files, never pipes (a pipe can hang the helper on Windows).
    assert call["kwargs"]["stdout"] is not bh.subprocess.PIPE
    assert "capture_output" not in call["kwargs"]


def test_upload_timeout_grows_with_size(bh, cfg, fake_sftp, winenv):
    rows = "".join(f"Listing {i},Shop,3.99,{i},1,2,3,4 days,tag,Cat\r\n" for i in range(150_000))
    src = write(Path(winenv["USERPROFILE"]) / "Downloads" / "big.csv", EVERBEE + rows)
    size = src.stat().st_size
    assert bh.upload(src, cfg).ok
    assert fake_sftp.calls[0]["kwargs"]["timeout"] == 60 + size // 20_000 > 300


def test_upload_reports_local_problems(bh, cfg, fake_sftp, winenv, monkeypatch):
    src = write(Path(winenv["USERPROFILE"]) / "Downloads" / "e.csv", ERANK)

    def no_temp(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(bh.tempfile, "mkdtemp", no_temp)
    res = bh.upload(src, cfg)
    assert not res.ok and res.backoff and "disk full" in res.message
    assert fake_sftp.calls == []


def test_upload_custom_port(bh, cfg, fake_sftp, winenv):
    src = write(Path(winenv["USERPROFILE"]) / "Downloads" / "e.csv", ERANK)
    cfg = dict(cfg, port=2222)
    assert bh.upload(src, cfg).ok
    argv = fake_sftp.calls[0]["argv"]
    assert argv[argv.index("-P") + 1] == "2222"


def test_upload_never_sends_non_exports(bh, cfg, fake_sftp, winenv):
    src = write(Path(winenv["USERPROFILE"]) / "Downloads" / "bank.csv", BANK)
    res = bh.upload(src, cfg)
    assert not res.ok and not res.backoff
    assert fake_sftp.calls == []


def test_upload_refuses_changed_file(bh, cfg, fake_sftp, winenv):
    src = write(Path(winenv["USERPROFILE"]) / "Downloads" / "e.csv", ERANK)
    res = bh.upload(src, cfg, sha256="0" * 64)
    assert not res.ok and not res.backoff and fake_sftp.calls == []


@pytest.mark.parametrize("stderr,expect", [
    ("botsy-upload@203.0.113.7: Permission denied (publickey).", "did not accept"),
    ("ssh: connect to host 203.0.113.7 port 22: Connection timed out", "Could not reach"),
    ("Host key verification failed.", "identity changed"),
    ("ssh: Could not resolve hostname nope: No such host is known.", "could not be found"),
    ("Connection closed by 203.0.113.7 port 22", "closed the connection"),
    ('dest open "/incoming/x.part": No such file or directory', "folder on the server"),
])
def test_upload_failure_message(bh, cfg, monkeypatch, winenv, stderr, expect):
    fake = FakeSftp(returncode=255, stderr=stderr)
    monkeypatch.setattr(bh.subprocess, "run", fake)
    src = write(Path(winenv["USERPROFILE"]) / "Downloads" / "e.csv", ERANK)
    res = bh.upload(src, cfg)
    assert not res.ok and res.backoff and expect in res.message


def test_upload_without_sftp_or_with_timeout(bh, cfg, monkeypatch, winenv):
    src = write(Path(winenv["USERPROFILE"]) / "Downloads" / "e.csv", ERANK)

    def missing(argv, **kw):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(bh.subprocess, "run", missing)
    res = bh.upload(src, cfg)
    assert not res.ok and "Optional features" in res.message

    def slow(argv, **kw):
        raise bh.subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(bh.subprocess, "run", slow)
    res = bh.upload(src, cfg)
    assert not res.ok and "did not answer" in res.message


def test_backoff_doubles_and_caps(bh):
    assert [bh.backoff_seconds(n, 30) for n in range(0, 8)] == [0, 30, 60, 120, 240, 480, 600, 600]
    assert bh.backoff_seconds(1000, 30) == 600


# ------------------------------------------------------------------ the poll loop

class Clock:
    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


def test_poll_uploads_once_and_records_state(bh, cfg, fake_sftp, winenv):
    downloads = Path(winenv["USERPROFILE"]) / "Downloads"
    write(downloads / "everbee.csv", EVERBEE)
    write(downloads / "bank.csv", BANK)
    helper = bh.Helper(cfg)
    summary = helper.poll()
    assert summary["uploaded"] == ["everbee.csv"] and summary["ignored"] == ["bank.csv"]
    puts = [c for c in fake_sftp.calls if c["sent"]]
    assert len(puts) == 1 and list(puts[0]["sent"].values()) == [EVERBEE.encode()]
    state = json.loads(Path(cfg["state_file"]).read_text(encoding="utf-8"))
    entry = state[bh.sha256_file(downloads / "everbee.csv")]
    assert entry["name"] == "everbee.csv" and entry["uploaded_at"]

    write(downloads / "everbee (1).csv", EVERBEE)          # re-downloaded, same bytes
    fresh = bh.Helper(cfg)                                 # e.g. after a restart
    assert fresh.poll()["uploaded"] == []
    assert len([c for c in fake_sftp.calls if c["sent"]]) == 1


def test_poll_backs_off_after_failure(bh, cfg, monkeypatch, winenv):
    fake = FakeSftp(returncode=255, stderr="Connection timed out")
    monkeypatch.setattr(bh.subprocess, "run", fake)
    write(Path(winenv["USERPROFILE"]) / "Downloads" / "e.csv", ERANK)
    clock = Clock()
    helper = bh.Helper(cfg, clock=clock)
    assert helper.poll()["failed"] == ["e.csv"]
    assert helper.failures == 1 and helper.next_upload_at == clock.t + 30
    puts = lambda: len([c for c in fake.calls if c["batch"][0].startswith("put")])  # noqa: E731
    clock.t += 10
    assert helper.poll()["waiting"] == ["e.csv"] and puts() == 1   # still waiting: no retry
    for expected in (60, 120, 240, 480, 600, 600):
        clock.t = helper.next_upload_at
        helper.poll()
        assert helper.next_upload_at - clock.t == expected
    fake.returncode = 0
    clock.t = helper.next_upload_at
    assert helper.poll()["uploaded"] == ["e.csv"]
    assert helper.failures == 0 and helper.next_upload_at == 0
    assert not Path(cfg["state_file"]).with_suffix(".json.bad").exists()


def test_poll_fetches_outbox_hourly(bh, cfg, fake_sftp, winenv):
    clock = Clock()
    helper = bh.Helper(cfg, clock=clock)
    helper.poll()
    gets = lambda: len([c for c in fake_sftp.calls if "get" in c["batch"][0]])  # noqa: E731
    assert gets() == 1
    clock.t += 1800
    helper.poll()
    assert gets() == 1
    clock.t += 1800
    helper.poll()
    assert gets() == 2


def test_poll_fetch_problem_is_not_retried_every_poll(bh, cfg, fake_sftp, monkeypatch):
    calls = []

    def broken(c):
        calls.append(1)
        raise OSError("no temp folder")

    monkeypatch.setattr(bh, "fetch_outbox", broken)
    clock = Clock()
    helper = bh.Helper(cfg, clock=clock)
    with pytest.raises(OSError):
        helper.poll()                        # main() logs it and carries on
    clock.t += 30
    helper.poll()
    assert len(calls) == 1                   # not again 30 seconds later
    clock.t += 600
    with pytest.raises(OSError):
        helper.poll()
    assert len(calls) == 2                   # but again within 10 minutes


class Recorder:
    def __init__(self):
        self.lines = []

    def _add(self, level, msg, *args, **kw):
        self.lines.append((level, msg % args if args else msg))

    def info(self, msg, *args, **kw):
        self._add("info", msg, *args)

    def warning(self, msg, *args, **kw):
        self._add("warning", msg, *args)

    def exception(self, msg, *args, **kw):
        self._add("error", msg, *args)


def test_poll_logs_a_waiting_file_once(bh, cfg, fake_sftp, winenv, monkeypatch):
    write(Path(winenv["USERPROFILE"]) / "Downloads" / "e.csv", ERANK)
    monkeypatch.setattr(bh, "upload", lambda p, c, sha256=None: bh.UploadResult(
        False, "", "could not read e.csv: locked", backoff=False))
    rec = Recorder()
    monkeypatch.setattr(bh, "log", rec)
    clock = Clock()
    helper = bh.Helper(cfg, clock=clock)
    for _ in range(5):
        assert helper.poll()["failed"] == ["e.csv"]
        clock.t += 30
    assert [m for _, m in rec.lines if "Not uploading e.csv yet" in m] == [
        "Not uploading e.csv yet: could not read e.csv: locked"]


def test_damaged_state_file_is_set_aside(bh, cfg):
    path = Path(cfg["state_file"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert bh.load_state(path) == {}
    assert path.with_name("state.json.bad").exists()


# ------------------------------------------------------------------ fetch_outbox

def test_fetch_outbox_opens_search_list_only_on_change(bh, cfg, fake_sftp, monkeypatch):
    opened = []
    monkeypatch.setattr(bh.os, "startfile", lambda p: opened.append(p), raising=False)
    fake_sftp.outbox = {"outbox/search-list.txt": b"axolotl coloring pages\r\n",
                        "outbox/status.txt": b"last tick: ok\r\n"}
    out = Path(cfg["outbox_dir"])
    assert bh.fetch_outbox(cfg) == {"search-list.txt": "changed", "status.txt": "changed",
                                    "daily-log.txt": "missing"}
    assert opened == [str(out / "search-list.txt")]
    assert (out / "status.txt").read_bytes() == b"last tick: ok\r\n"
    assert fake_sftp.calls[0]["batch"] == ['-get "outbox/search-list.txt" "search-list.txt"',
                                           '-get "outbox/status.txt" "status.txt"',
                                           '-get "outbox/daily-log.txt" "daily-log.txt"']

    fake_sftp.outbox["outbox/status.txt"] = b"last tick: newer\r\n"
    assert bh.fetch_outbox(cfg) == {"search-list.txt": "same", "status.txt": "changed",
                                    "daily-log.txt": "missing"}
    assert len(opened) == 1

    fake_sftp.outbox["outbox/search-list.txt"] = b"frog stickers\r\n"
    assert bh.fetch_outbox(cfg)["search-list.txt"] == "changed"
    assert len(opened) == 2 and (out / "search-list.txt").read_bytes() == b"frog stickers\r\n"


def test_fetch_outbox_missing_files_and_errors(bh, cfg, monkeypatch):
    opened = []
    monkeypatch.setattr(bh.os, "startfile", lambda p: opened.append(p), raising=False)
    monkeypatch.setattr(bh.subprocess, "run", FakeSftp())
    assert bh.fetch_outbox(cfg) == {"search-list.txt": "missing", "status.txt": "missing",
                                    "daily-log.txt": "missing"}
    monkeypatch.setattr(bh.subprocess, "run", FakeSftp(returncode=255, stderr="timed out"))
    assert bh.fetch_outbox(cfg) == {"search-list.txt": "error", "status.txt": "error",
                                    "daily-log.txt": "error"}
    assert opened == []


def test_fetch_outbox_keeps_the_users_notes(bh, cfg, fake_sftp, monkeypatch):
    opened = []
    monkeypatch.setattr(bh.os, "startfile", lambda p: opened.append(p), raising=False)
    fake_sftp.outbox = {"outbox/search-list.txt": b"axolotl coloring pages\r\n",
                        "outbox/status.txt": b"ok\r\n"}
    local = Path(cfg["outbox_dir"]) / "search-list.txt"
    assert bh.fetch_outbox(cfg)["search-list.txt"] == "changed" and len(opened) == 1
    local.write_bytes(b"[x] axolotl coloring pages   done!\r\n")       # the user's notes
    for _ in range(3):                                                  # hourly fetches
        assert bh.fetch_outbox(cfg)["search-list.txt"] == "same"
    assert local.read_bytes() == b"[x] axolotl coloring pages   done!\r\n"
    assert len(opened) == 1                                             # not opened again
    fake_sftp.outbox["outbox/search-list.txt"] = b"frog stickers\r\n"     # next week
    assert bh.fetch_outbox(cfg)["search-list.txt"] == "changed"
    assert local.read_bytes() == b"frog stickers\r\n" and len(opened) == 2
    record = json.loads(Path(cfg["outbox_state_file"]).read_text(encoding="utf-8"))
    assert set(record) == {"search-list.txt", "status.txt"}


def test_fetch_outbox_brings_the_daily_log_without_opening_it(bh, cfg, fake_sftp, monkeypatch):
    opened = []
    monkeypatch.setattr(bh.os, "startfile", lambda p: opened.append(p), raising=False)
    fake_sftp.outbox = {"outbox/daily-log.txt": b"Botsy daily log\r\n"}
    out = Path(cfg["outbox_dir"])
    assert bh.fetch_outbox(cfg)["daily-log.txt"] == "changed"
    assert (out / "daily-log.txt").read_bytes() == b"Botsy daily log\r\n"
    fake_sftp.outbox["outbox/daily-log.txt"] = b"Botsy daily log, next day\r\n"
    assert bh.fetch_outbox(cfg)["daily-log.txt"] == "changed"
    assert (out / "daily-log.txt").read_bytes() == b"Botsy daily log, next day\r\n"
    assert opened == []                                                # only the search list opens
    record = json.loads(Path(cfg["outbox_state_file"]).read_text(encoding="utf-8"))
    assert set(record) == {"daily-log.txt"}


def test_fetch_outbox_puts_back_a_deleted_copy_quietly(bh, cfg, fake_sftp, monkeypatch):
    opened = []
    monkeypatch.setattr(bh.os, "startfile", lambda p: opened.append(p), raising=False)
    fake_sftp.outbox = {"outbox/search-list.txt": b"owl stickers\r\n"}
    bh.fetch_outbox(cfg)
    local = Path(cfg["outbox_dir"]) / "search-list.txt"
    local.unlink()
    assert bh.fetch_outbox(cfg)["search-list.txt"] == "same"
    assert local.read_bytes() == b"owl stickers\r\n" and len(opened) == 1


def test_fetch_outbox_without_a_record_compares_the_local_copy(bh, cfg, fake_sftp, monkeypatch):
    opened = []
    monkeypatch.setattr(bh.os, "startfile", lambda p: opened.append(p), raising=False)
    local = Path(cfg["outbox_dir"]) / "search-list.txt"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"owl stickers\r\n")              # fetched by an older helper version
    fake_sftp.outbox = {"outbox/search-list.txt": b"owl stickers\r\n"}
    assert bh.fetch_outbox(cfg)["search-list.txt"] == "same" and opened == []


def test_open_file_without_startfile(bh, tmp_path, monkeypatch):
    monkeypatch.delattr(bh.os, "startfile", raising=False)
    assert bh.open_file(tmp_path / "x.txt") is False


# ------------------------------------------------------------------ config

def test_load_config_expands_windows_variables(bh, config_file, winenv):
    cfg = bh.load_config(config_file)
    home = Path(winenv["USERPROFILE"])
    assert cfg["key"] == home / ".ssh" / "botsy_upload"
    assert cfg["watch_dir"] == home / "Downloads"
    assert cfg["outbox_dir"] == home / "Documents" / "Botsy"
    assert cfg["state_file"] == Path(winenv["APPDATA"]) / "Botsy" / "state.json"
    assert cfg["log_file"] == Path(winenv["APPDATA"]) / "Botsy" / "helper.log"
    assert cfg["poll_seconds"] == 30 and cfg["max_age_days"] == 14
    assert cfg["user"] == "botsy-upload" and cfg["host"] == "203.0.113.7"


def test_load_config_default_path_and_lowercase_vars(bh, config_file, winenv):
    env = {k.lower(): v for k, v in winenv.items()}  # Windows variables ignore case
    cfg = bh.load_config(None, env=env)
    assert cfg["config_file"] == config_file


def test_expand_path_leaves_unknown_variables(bh):
    assert str(bh.expand_path("%NOPE_NOT_SET%\\x", env={})) == "%NOPE_NOT_SET%/x"
    assert bh.expand_path("~/x", env={}) == Path.home() / "x"


@pytest.mark.parametrize("change,message", [
    ({"host": ""}, "No server address"),
    ({"host": "-oProxyCommand=calc"}, "looks wrong"),
    ({"host": "a b"}, "looks wrong"),
    ({"host": "203.0.113.7:22"}, "looks wrong"),     # sftp would read ":22" as a path
    ({"host": "botsy-upload@203.0.113.7"}, "looks wrong"),
    ({"user": "-x"}, "looks wrong"),
    ({"poll_seconds": "soon"}, "whole number"),
    ({"remote_incoming": "../etc"}, "plain folder"),
])
def test_load_config_rejects_bad_values(bh, config_file, change, message):
    data = json.loads(config_file.read_text(encoding="utf-8-sig"))
    data.update(change)
    config_file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(bh.ConfigError, match=message):
        bh.load_config(config_file)


def test_load_config_accepts_names_and_ipv6(bh, config_file):
    for host in ("botsy.example.com", "[2001:db8::7]"):
        data = json.loads(config_file.read_text(encoding="utf-8-sig"))
        data["host"] = host
        config_file.write_text(json.dumps(data), encoding="utf-8")
        assert bh.load_config(config_file)["host"] == host


def test_default_sftp_prefers_windows_openssh(bh, tmp_path, monkeypatch):
    root = tmp_path / "Windows"
    exe = root / "System32" / "OpenSSH" / "sftp.exe"
    monkeypatch.setenv("SystemRoot", str(root))
    monkeypatch.setattr(bh, "WINDOWS_SFTP", "C:\\Windows/System32/OpenSSH/sftp.exe")
    monkeypatch.setattr(bh, "SYSNATIVE_SFTP", "C:\\Windows/Sysnative/OpenSSH/sftp.exe")
    monkeypatch.setattr(bh.shutil, "which", lambda name: None)
    assert bh.default_sftp() == "sftp"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert bh.default_sftp() == str(exe)


def test_load_config_missing_or_broken(bh, tmp_path):
    with pytest.raises(bh.ConfigError, match="install_helper.ps1"):
        bh.load_config(tmp_path / "none.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(bh.ConfigError, match="not valid JSON"):
        bh.load_config(bad)


def test_load_config_minimums(bh, config_file):
    data = json.loads(config_file.read_text(encoding="utf-8-sig"))
    data["poll_seconds"] = 0
    config_file.write_text(json.dumps(data), encoding="utf-8")
    assert bh.load_config(config_file)["poll_seconds"] == 5


# ------------------------------------------------------------------ lock and main

def test_single_instance_lock(bh, tmp_path):
    path = tmp_path / "Botsy" / "helper.lock"
    first, second = bh.SingleInstance(path), bh.SingleInstance(path)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_main_once_end_to_end(bh, config_file, winenv, fake_sftp, capsys):
    downloads = Path(winenv["USERPROFILE"]) / "Downloads"
    write(downloads / "erank-keywords.csv", ERANK)
    write(downloads / "statement.csv", BANK)
    assert bh.main(["--config", str(config_file), "--once"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["uploaded"] == ["erank-keywords.csv"] and out["ignored"] == ["statement.csv"]
    log_text = (Path(winenv["APPDATA"]) / "Botsy" / "helper.log").read_text(encoding="utf-8")
    assert "Uploaded erank-keywords.csv" in log_text and "Not uploading statement.csv" in log_text
    assert bh.main(["--config", str(config_file), "--once"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["uploaded"] == []


def test_main_once_refuses_second_copy(bh, config_file, winenv, fake_sftp, capsys):
    lock = bh.SingleInstance(Path(winenv["APPDATA"]) / "Botsy" / "helper.lock")
    assert lock.acquire()
    try:
        assert bh.main(["--config", str(config_file), "--once"]) == 1
        assert "already running" in capsys.readouterr().out
        assert fake_sftp.calls == []
    finally:
        lock.release()


def test_main_check(bh, config_file, monkeypatch, capsys):
    ok = FakeSftp()
    monkeypatch.setattr(bh.subprocess, "run", ok)
    assert bh.main(["--config", str(config_file), "--check"]) == 0
    assert "OK: connected to botsy-upload@203.0.113.7" in capsys.readouterr().out
    assert ok.calls[0]["batch"] == ['cd "incoming"', "pwd"]          # looks, never writes
    monkeypatch.setattr(bh.subprocess, "run",
                        FakeSftp(returncode=255, stderr="Permission denied (publickey)."))
    assert bh.main(["--config", str(config_file), "--check"]) == 1
    assert "did not accept this computer's key" in capsys.readouterr().out


def test_main_check_without_key(bh, config_file, winenv, fake_sftp, capsys):
    (Path(winenv["USERPROFILE"]) / ".ssh" / "botsy_upload").unlink()
    assert bh.main(["--config", str(config_file), "--check"]) == 1
    assert "key file" in capsys.readouterr().out and fake_sftp.calls == []


def test_main_without_config(bh, tmp_path, winenv, capsys):
    assert bh.main(["--config", str(tmp_path / "missing.json"), "--once"]) == 2
    assert "Run install_helper.ps1 first" in capsys.readouterr().out
    assert not (Path(winenv["APPDATA"]) / "Botsy").exists()          # nothing created


def test_main_logs_a_broken_config(bh, config_file, winenv, capsys):
    # A hand edit with single backslashes is not valid JSON. Under pythonw nothing can
    # be printed, so the reason has to land in helper.log.
    config_file.write_text('{"host": "203.0.113.7", "watch_dir": "C:\\Users\\x\\OneDrive"',
                           encoding="utf-8")
    assert bh.main(["--once"]) == 2
    text = (Path(winenv["APPDATA"]) / "Botsy" / "helper.log").read_text(encoding="utf-8")
    assert "cannot start" in text and "not valid JSON" in text


def test_main_under_pythonw_has_no_console(bh, config_file, winenv, fake_sftp, monkeypatch):
    monkeypatch.setattr(bh.sys, "stdout", None)
    monkeypatch.setattr(bh.sys, "stderr", None)
    write(Path(winenv["USERPROFILE"]) / "Downloads" / "e.csv", ERANK)
    assert bh.main(["--config", str(config_file), "--once"]) == 0
    assert bh.main(["--config", str(config_file), "--check"]) == 0


# ------------------------------------------------------------------ installer and README

WINDOWS_DIR = Path(__file__).resolve().parents[2] / "windows"
DEPLOY_DIR = Path(__file__).resolve().parents[2] / "deploy" / "lightsail"


def test_installer_is_plain_ascii():
    data = (WINDOWS_DIR / "install_helper.ps1").read_bytes()
    assert data.isascii()                    # Windows PowerShell 5.1 misreads UTF-8 without a BOM


def test_server_command_matches_the_server_setup():
    """The line the installer prints must be one the server's setup.sh understands."""
    ps1 = (WINDOWS_DIR / "install_helper.ps1").read_text(encoding="ascii")
    readme = (WINDOWS_DIR / "README.md").read_text(encoding="utf-8")
    assert '$ServerSetup  = "botsy-setup"' in ps1
    assert '"sudo $ServerSetup --upload-pubkey' in ps1
    assert "sudo botsy-setup --upload-pubkey" in readme
    assert "setup.sh" not in readme          # the user only ever types botsy-setup
    # The installer and the README tell the user to look for exactly these phrases.
    phrases = ("Upload key added", "That upload key was already allowed")
    for phrase in phrases:
        assert phrase in ps1 and phrase in readme
    setup, sshd = DEPLOY_DIR / "setup.sh", DEPLOY_DIR / "sshd_botsy_upload.conf"
    if not (setup.exists() and sshd.exists()):
        pytest.skip("deploy/lightsail is not in this checkout")
    script = setup.read_text(encoding="utf-8")
    assert "--upload-pubkey)" in script and "LAUNCHER=/usr/local/sbin/botsy-setup" in script
    for phrase in phrases:
        assert phrase in script
    keys = [ln.split()[1] for ln in sshd.read_text(encoding="utf-8").splitlines()
            if ln.strip().startswith("AuthorizedKeysFile")]
    assert keys and f'$ServerKeys   = "{keys[0]}"' in ps1
    assert "echo `\"restrict $publicKey`\" | sudo tee -a $ServerKeys" in ps1


SLOW_SFTP = '''#!{python}
import subprocess, sys, time
# A child that keeps the output handles open, like ssh.exe under sftp.exe on Windows.
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(4)"])
sys.stderr.write("still connecting\\n")
sys.stderr.flush()
time.sleep(30)
'''


@pytest.mark.skipif(os.name == "nt", reason="uses a POSIX script as the fake sftp")
def test_run_sftp_timeout_returns_promptly(bh, cfg, tmp_path):
    import stat
    import sys
    fake = tmp_path / "slow-sftp"
    fake.write_text(SLOW_SFTP.format(python=sys.executable), encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    work = tmp_path / "work"
    work.mkdir()
    started = time.monotonic()
    res = bh.run_sftp(dict(cfg, sftp=str(fake)), ["pwd"], work, timeout=1)
    assert time.monotonic() - started < 3.5
    assert not res.ok and "did not answer within 1 seconds" in res.message
    assert "still connecting" in res.stderr
