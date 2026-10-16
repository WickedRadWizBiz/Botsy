"""windows/botsy_helper.py against the real OpenSSH sftp client (offline, no root).

`sftp -D` talks straight to a local sftp-server, with no ssh, keys or network, so the
batch files the helper writes are run by the real parser: quoting, "-get", rename of
the .part file, exit codes and error text. A small wrapper takes the helper's ssh
options off the command line first. Skipped where OpenSSH is not installed.
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
import time
from pathlib import Path

import pytest

EVERBEE = ("Product Name,Shop Name,Price,Mo. Sales,Mo. Revenue,Reviews,Favorites\r\n"
           "Farm animal coloring pages,ShopOne,3.99,120,478.8,40,300\r\n")
BANK = "Transaction Date,Description,Amount\n10/01/2026,GROCERY STORE,-54.20\n"

SFTP = shutil.which("sftp")
SERVER = next((p for p in ("/usr/lib/openssh/sftp-server", "/usr/libexec/openssh/sftp-server",
                           "/usr/libexec/sftp-server", "/usr/lib/ssh/sftp-server")
               if os.path.isfile(p)), None)

pytestmark = pytest.mark.skipif(os.name == "nt" or not SFTP or not SERVER,
                                reason="needs the OpenSSH sftp client and sftp-server")

WRAPPER = '''#!{python}
"""Runs the helper's sftp command against a local sftp-server instead of a real server."""
import subprocess, sys
args, batch, i = sys.argv[1:], None, 0
while i < len(args):
    if args[i] in ("-i", "-o", "-P"):
        i += 2
    elif args[i] == "-b":
        batch, i = args[i + 1], i + 2
    else:
        i += 1  # user@host
sys.exit(subprocess.call([{sftp!r}, "-D", {server!r} + " -d " + {inbox!r}, "-b", batch]))
'''


@pytest.fixture
def real(bh, cfg, tmp_path):
    inbox = tmp_path / "inbox"
    (inbox / "incoming").mkdir(parents=True)
    (inbox / "outbox").mkdir()
    wrapper = tmp_path / "fake-sftp"
    wrapper.write_text(WRAPPER.format(python=sys.executable, sftp=SFTP, server=SERVER,
                                      inbox=str(inbox)), encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return dict(cfg, sftp=str(wrapper)), inbox


def _export(winenv, name: str, text: str) -> Path:
    path = Path(winenv["USERPROFILE"]) / "Downloads" / name
    path.write_text(text, encoding="utf-8", newline="")
    t = time.time() - 60
    os.utime(path, (t, t))
    return path


def test_check_upload_and_fetch_for_real(bh, real, winenv, monkeypatch):
    cfg, inbox = real
    ok, message = bh.check_connection(cfg)
    assert ok, message

    src = _export(winenv, 'EverBee "Export" (2).csv', EVERBEE)
    before = src.read_bytes()
    res = bh.upload(src, cfg)
    assert res.ok, res.message
    assert sorted(p.name for p in (inbox / "incoming").iterdir()) == [res.remote_name]
    assert (inbox / "incoming" / res.remote_name).read_bytes() == before
    assert src.read_bytes() == before                      # the user's file is untouched

    opened = []
    monkeypatch.setattr(bh.os, "startfile", lambda p: opened.append(p), raising=False)
    (inbox / "outbox" / "search-list.txt").write_bytes(b"owl stickers\r\n")
    # status.txt and daily-log.txt not written yet: "-get" lets the batch carry on.
    assert bh.fetch_outbox(cfg) == {"search-list.txt": "changed", "status.txt": "missing",
                                    "daily-log.txt": "missing"}
    assert len(opened) == 1


def test_poll_for_real_sends_exports_only_once(bh, real, winenv, monkeypatch):
    cfg, inbox = real
    monkeypatch.setattr(bh.os, "startfile", lambda p: None, raising=False)
    _export(winenv, "erank.csv", EVERBEE)
    _export(winenv, "statement.csv", BANK)
    helper = bh.Helper(cfg)
    summary = helper.poll()
    assert summary["uploaded"] == ["erank.csv"] and summary["ignored"] == ["statement.csv"]
    assert bh.Helper(cfg).poll()["uploaded"] == []         # a restart sends nothing again
    assert len(list((inbox / "incoming").iterdir())) == 1


def test_missing_server_folder_is_reported_not_recorded(bh, real, winenv):
    cfg, inbox = real
    (inbox / "incoming").rmdir()
    ok, message = bh.check_connection(cfg)
    assert not ok and "folder on the server is missing" in message
    res = bh.upload(_export(winenv, "e.csv", EVERBEE), cfg)
    assert not res.ok and res.backoff and "folder on the server is missing" in res.message
