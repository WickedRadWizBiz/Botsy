"""Settings, paths and environment loading shared by every stage."""
from __future__ import annotations

import copy
import os
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


class MissingSecretError(RuntimeError):
    """A required secret is not set. Secrets live only in .env (see .env.example)."""


def load_settings(path: str | Path | None = None) -> dict:
    """Load config/settings.yaml (or another YAML file). Returns a fresh dict every call."""
    p = Path(path) if path else CONFIG_DIR / "settings.yaml"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def section(settings: dict, name: str, defaults: dict | None = None) -> dict:
    """Return settings[name] deep-merged over `defaults` (settings win)."""
    return deep_merge(copy.deepcopy(defaults or {}), settings.get(name) or {})


def deep_merge(base: dict, override: dict) -> dict:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base


def resolve(rel: str | Path) -> Path:
    """Resolve a settings path (relative to the repo root) to an absolute Path."""
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p


def get_shop(settings: dict, shop_id: str | None = None) -> dict:
    shops = settings["shops"]
    if shop_id is None:
        return shops[0]
    for shop in shops:
        if shop["id"] == shop_id:
            return shop
    raise KeyError(f"unknown shop id {shop_id!r}; known: {[s['id'] for s in shops]}")


def load_env() -> None:
    """Load .env from the repo root into os.environ (existing variables win)."""
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)


def require_env(name: str) -> str:
    load_env()
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingSecretError(f"{name} is not set. Add it to .env (see .env.example).")
    return value


def today() -> str:
    """ISO date for output names. PIPELINE_DATE overrides it for reproducible runs."""
    value = os.environ.get("PIPELINE_DATE") or date.today().isoformat()
    date.fromisoformat(value)  # raises ValueError on anything that is not YYYY-MM-DD
    if len(value) != 10:
        raise ValueError(f"PIPELINE_DATE must be YYYY-MM-DD, got {value!r}")
    return value
