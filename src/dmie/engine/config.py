"""Loads the engine's YAML config (config/engine/*.yaml)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENGINE_CONFIG_DIR = PROJECT_ROOT / "config" / "engine"


@lru_cache(maxsize=None)
def load_yaml(name: str) -> dict:
    path = ENGINE_CONFIG_DIR / name
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def engine_settings() -> dict:
    return load_yaml("engine.yaml")


def section(name: str) -> dict:
    return dict(engine_settings().get(name, {}))
