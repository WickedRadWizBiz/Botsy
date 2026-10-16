"""Load windows/botsy_helper.py (a standalone script, not a package) for the tests.

Everything runs offline in tmp_path: sftp is never started (subprocess.run is replaced
by a fake), os.startfile is a fake, and %APPDATA% / %USERPROFILE% and the temporary
folder point into tmp_path.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import pytest

HELPER_PATH = Path(__file__).resolve().parents[2] / "windows" / "botsy_helper.py"


def _load():
    if "botsy_helper" in sys.modules:
        return sys.modules["botsy_helper"]
    spec = importlib.util.spec_from_file_location("botsy_helper", HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["botsy_helper"] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def bh():
    return _load()


@pytest.fixture
def winenv(tmp_path, monkeypatch):
    """Windows-like environment variables, all inside tmp_path."""
    home = tmp_path / "home"
    env = {"USERPROFILE": str(home), "APPDATA": str(home / "AppData" / "Roaming"),
           "LOCALAPPDATA": str(home / "AppData" / "Local")}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    temp = tmp_path / "Temp"                    # the helper's own temporary folders, too
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    (home / "Downloads").mkdir(parents=True)
    (home / ".ssh").mkdir()
    (home / ".ssh" / "botsy_upload").write_text("fake private key\n", encoding="utf-8")
    return env


@pytest.fixture
def config_file(winenv):
    """%APPDATA%\\Botsy\\helper.json as install_helper.ps1 writes it (with a BOM)."""
    path = Path(winenv["APPDATA"]) / "Botsy" / "helper.json"
    path.parent.mkdir(parents=True)
    data = {"host": "203.0.113.7", "user": "botsy-upload",
            "key": "%USERPROFILE%\\.ssh\\botsy_upload", "watch_dir": "%USERPROFILE%\\Downloads",
            "poll_seconds": 30, "max_age_days": 14, "sftp": "C:\\fake\\sftp.exe"}
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(data, indent=2).encode("utf-8"))
    return path


@pytest.fixture
def cfg(bh, config_file):
    return bh.load_config(config_file)


class FakeSftp:
    """Stands in for subprocess.run: records each call and the batch file it was given."""

    def __init__(self, returncode=0, stderr="", outbox=None):
        self.calls = []
        self.returncode = returncode
        self.stderr = stderr
        self.outbox = outbox if outbox is not None else {}  # remote name -> bytes

    def __call__(self, argv, **kwargs):
        cwd = Path(kwargs["cwd"])
        batch = Path(argv[argv.index("-b") + 1])
        lines = batch.read_text(encoding="utf-8").splitlines()
        sent = {}
        for line in lines:
            if line.startswith("put "):
                local = line.split('"')[1]
                sent[local] = (cwd / local).read_bytes()
        if self.returncode == 0:
            for line in lines:
                if line.lstrip("-").startswith("get "):
                    remote, local = line.split('"')[1], line.split('"')[3]
                    if remote in self.outbox:
                        (cwd / local).write_bytes(self.outbox[remote])
        self.calls.append({"argv": list(argv), "kwargs": kwargs, "batch": lines, "sent": sent,
                           "batch_bytes": batch.read_bytes()})
        # The helper hands sftp files, not pipes, for its output (see run_sftp).
        assert hasattr(kwargs["stdout"], "write") and hasattr(kwargs["stderr"], "write")
        kwargs["stdout"].write(b"".join(b"sftp> " + ln.encode() + b"\n" for ln in lines))
        kwargs["stderr"].write(self.stderr.encode("utf-8"))

        class Proc:
            pass

        proc = Proc()
        proc.returncode, proc.stdout, proc.stderr = self.returncode, None, None
        return proc


@pytest.fixture
def fake_sftp(bh, monkeypatch):
    fake = FakeSftp()
    monkeypatch.setattr(bh.subprocess, "run", fake)
    return fake
