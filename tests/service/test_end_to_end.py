"""The whole server loop, offline in tmp_path, with the real code at every step:

1. Exports land in incoming/ the way the Windows helper sends them (<name>.part, then a
   rename), next to an upload still in progress and a CSV that is not an export.
2. `python -m service tick` takes them in, researches (briefs and history snapshots),
   runs the pipeline as a dry run and writes outbox/status.txt.
3. windows/botsy_helper.py --once, started as its own process, uploads a new export from
   a PC "Downloads" folder through a fake sftp found on PATH. The fake carries out only
   the requests the server allows (put, rename, -get, cd, pwd), into the same inbox.
4. A second tick takes in the helper's upload: the server keeps the helper's niche prefix
   and the same short hash.
5. `python -m service searchlist` lists the helper's export as a near miss, and a second
   helper pass copies search-list.txt and status.txt into the PC's Documents\\Botsy.

Nothing in the outbox, the history or the PC copies may name a shop or hold a link.
Everything is invented (demo exports, fake shops and links); no network, no root.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core.config import ROOT
from core.items import read_json
from research.demo import write_demo_inputs
from service import __main__ as cli
from service import status
from service.paths import service_paths

from ._support import DATE, make_settings, upload

pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(os.name == "nt", reason="the fake sftp is a POSIX script")]

HELPER = ROOT / "windows" / "botsy_helper.py"
BANK = "Transaction Date,Description,Amount\n10/01/2026,GROCERY STORE,-54.20\n"
# Shop names in the invented exports, and anything that looks like a link.
LEAKS = re.compile(r"demoshop|pcshop|https?://|www\.|etsy\.com|/listing/", re.I)

FAKE_SFTP = '''#!{python}
"""Fake sftp: runs the helper's batch file in $FAKE_SFTP_ROOT (the chroot)."""
import os, shlex, shutil, sys
from pathlib import Path

root = Path(os.environ["FAKE_SFTP_ROOT"]).resolve()
args, batch, i = sys.argv[1:], None, 0
while i < len(args):
    if args[i] in ("-i", "-o", "-P"):
        i += 2
    elif args[i] == "-b":
        batch, i = args[i + 1], i + 2
    else:
        i += 1
with open(os.environ["FAKE_SFTP_LOG"], "a", encoding="utf-8") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")


def remote(p):
    q = (root / p.lstrip("/")).resolve()
    if root != q and root not in q.parents:
        sys.exit("outside the chroot: " + p)
    return q


for line in Path(batch).read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    with open(os.environ["FAKE_SFTP_LOG"], "a", encoding="utf-8") as log:
        log.write("> " + line + "\\n")
    optional = line.startswith("-")
    words = shlex.split(line.lstrip("-"))
    cmd, rest = words[0], words[1:]
    try:
        if cmd == "put" and len(rest) == 2:
            shutil.copyfile(rest[0], remote(rest[1]))
        elif cmd == "rename" and len(rest) == 2:
            os.replace(remote(rest[0]), remote(rest[1]))
        elif cmd == "get" and len(rest) == 2:
            shutil.copyfile(remote(rest[0]), rest[1])
        elif cmd == "cd" and len(rest) == 1 and remote(rest[0]).is_dir():
            pass
        elif cmd == "pwd" and not rest:
            print("Remote working directory: /")
        else:
            sys.exit("the server refuses: " + line)
    except OSError as e:
        print(e, file=sys.stderr)
        if not optional:
            sys.exit(1)
'''


@pytest.fixture
def world(tmp_path):
    """Settings and inbox in tmp_path (2 items per run keeps the dry run quick)."""
    settings, path = make_settings(tmp_path, pipeline={"max_items_per_run": 2})
    return settings, path, service_paths(settings)


def _pc(tmp_path: Path, inbox: Path) -> dict:
    """A Windows PC in tmp_path: Downloads with one new export and a bank statement,
    helper.json as the installer writes it, and the fake sftp first on PATH."""
    home = tmp_path / "pc"
    downloads = home / "Downloads"
    downloads.mkdir(parents=True)
    (home / ".ssh").mkdir()
    (home / ".ssh" / "botsy_upload").write_text("fake key\n", encoding="utf-8")
    # 7 listings from 2 shops: a near miss for the 10-listing, 3-shop brief rule.
    rows = ["Product Name,Shop Name,Price,Mo. Sales,Reviews,Favorites,Listing Age,Tags,Link"]
    rows += [f"Axolotl Coloring Pages for Kids Set {i},PcShop{i % 2},3.99,{20 + i},{i},{5 * i},"
             f"90 days,axolotl,https://www.etsy.com/listing/{7_000_000 + i}/x" for i in range(7)]
    export = downloads / "coloring_pages__EverBee Export (3).csv"
    export.write_text("\n".join(rows) + "\n", encoding="utf-8")
    (downloads / "bank statement.csv").write_text(BANK, encoding="utf-8")
    old = time.time() - 120                     # finished downloading a while ago
    for p in downloads.iterdir():
        os.utime(p, (old, old))
    appdata = home / "AppData" / "Roaming"
    config = appdata / "Botsy" / "helper.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({
        "host": "203.0.113.7", "user": "botsy-upload", "key": "%USERPROFILE%\\.ssh\\botsy_upload",
        "watch_dir": "%USERPROFILE%\\Downloads", "poll_seconds": 30, "max_age_days": 14}),
        encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "sftp"
    fake.write_text(FAKE_SFTP.format(python=sys.executable), encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    temp = tmp_path / "pc-temp"
    temp.mkdir()
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}", "USERPROFILE": str(home),
           "APPDATA": str(appdata), "LOCALAPPDATA": str(home / "AppData" / "Local"),
           "TMPDIR": str(temp), "HOME": str(home), "FAKE_SFTP_ROOT": str(inbox),
           "FAKE_SFTP_LOG": str(tmp_path / "sftp.log")}
    return {"home": home, "export": export, "config": config, "env": env}


def _helper_once(pc: dict) -> dict:
    """windows/botsy_helper.py --once as its own process; returns its summary line."""
    res = subprocess.run([sys.executable, str(HELPER), "--config", str(pc["config"]), "--once"],
                         env=pc["env"], capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def _no_leaks(*folders: Path) -> None:
    for folder in folders:
        for p in folder.rglob("*"):
            if p.is_file():
                text = p.read_text(encoding="utf-8")
                assert not LEAKS.search(text), f"{p} names a shop or holds a link"


def test_uploads_to_drafts_to_search_list_and_back_to_the_pc(world, tmp_path, capsys):
    settings, path, paths = world

    # 1. Uploads as the helper makes them, plus one in progress and one that is no export.
    for p in write_demo_inputs(tmp_path / "demo"):
        upload(paths.incoming, p.name, p.read_bytes())
    (paths.incoming / "everbee-later.csv.part").write_bytes(b"Product Name,Shop Name\nhalf a fi")
    upload(paths.incoming, "bank-statement.csv", BANK.encode())

    # 2. The tick the systemd path unit starts.
    assert cli.main(["tick", "--settings", str(path), "--date", DATE]) == 0
    assert "2 accepted, 0 duplicate, 1 rejected; research ran" in capsys.readouterr().out
    assert sorted(p.name for p in paths.incoming.iterdir()) == ["everbee-later.csv.part"]
    stored = sorted(p.name for p in paths.inputs.glob("*.csv"))
    assert [re.sub(r"-[0-9a-f]{8}\.csv$", "", n) for n in stored] == [f"erank-{DATE}", f"everbee-{DATE}"]
    assert len(read_json(paths.inputs / ".ingested.json")) == 2
    reason = (paths.rejected / f"{DATE}-bank-statement.csv.reason.txt").read_text(encoding="utf-8")
    assert "does not look like an EverBee or eRank export" in reason

    niches = ["activity_sheets", "coloring_pages", "stickers"]
    assert sorted(p.name for p in paths.briefs.glob("*.json")) == [f"{n}-{DATE}.json" for n in niches]
    snaps = {p.stem: read_json(p) for p in (paths.history / DATE).glob("*.json")}
    assert sorted(snaps) == niches
    assert all(s["schema"] == "history/v1" and s["themes"] for s in snaps.values())

    doc = status.load(paths.outbox)
    run = doc["ticks"][-1]["run"]
    assert run["ran"] and run["dry_run"] and run["generated"] == 2 and run["drafts"] == 2
    items = [d for d in (tmp_path / "dry").iterdir() if d.is_dir() and d.name.startswith(DATE)]
    assert len(items) == 2 and all((d / "listing.json").exists() for d in items)
    text = (paths.outbox / status.TEXT_NAME).read_bytes().decode("utf-8")
    assert "\r\n" in text and "Mode: TEST (dry run)" in text
    assert "Not used: bank-statement.csv" in text and "Practice drafts (test mode" in text
    assert "Problems: none" in text

    # 3. The Windows helper uploads a new export from the PC.
    pc = _pc(tmp_path, paths.inbox)
    summary = _helper_once(pc)
    assert summary["uploaded"] == [pc["export"].name] and summary["ignored"] == ["bank statement.csv"]
    sha = hashlib.sha256(pc["export"].read_bytes()).hexdigest()
    sent = f"coloring_pages__EverBee-Export-3-{sha[:8]}.csv"
    assert sorted(p.name for p in paths.incoming.iterdir()) == sorted(["everbee-later.csv.part", sent])
    batch = [ln[2:] for ln in (tmp_path / "sftp.log").read_text(encoding="utf-8").splitlines()
             if ln.startswith("> ")]
    assert batch[:2] == [f'put "{sent}" "incoming/{sent}.part"',
                         f'rename "incoming/{sent}.part" "incoming/{sent}"']

    # 4. The next tick keeps the helper's niche prefix and the same short hash.
    assert cli.main(["tick", "--settings", str(path), "--date", DATE]) == 0
    assert (paths.inputs / f"coloring_pages__everbee-{DATE}-{sha[:8]}.csv").exists()
    assert sorted(p.name for p in paths.incoming.iterdir()) == ["everbee-later.csv.part"]
    near = {t["name"]: t for t in read_json(paths.history / DATE / "coloring_pages.json")["themes"]}
    assert near["axolotl"]["qualified"] is False and near["axolotl"]["listing_rows"] == 7

    # 5. The weekly search list, then the helper brings it (and the status) to the PC.
    assert cli.main(["searchlist", "--settings", str(path), "--date", DATE]) == 0
    raw = (paths.outbox / "search-list.txt").read_bytes()
    assert raw == (paths.outbox / f"search-list-{DATE}.txt").read_bytes()
    lines = raw.decode("utf-8").split("\r\n")
    assert "  coloring pages for kids  (every week)" in lines
    assert "  axolotl coloring pages  (7 of 10 listings, 2 of 3 shops so far)" in lines
    assert lines[-2].startswith("Last week: 3 exports received, 3 brief files written, 2 items made")
    assert "\n" not in raw.decode("utf-8").replace("\r\n", "")

    _helper_once(pc)
    documents = pc["home"] / "Documents" / "Botsy"
    assert (documents / "search-list.txt").read_bytes() == raw
    assert (documents / "status.txt").read_bytes() == (paths.outbox / status.TEXT_NAME).read_bytes()

    _no_leaks(paths.outbox, paths.history, documents)
