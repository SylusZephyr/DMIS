"""Product knowledge layer (spec: AI-Powered Global Dental Product Intelligence).

Turns resolved listings into explained knowledge:

* ``units``        -- unit normalisation (spec 67): "50k RPM", "50 KRPM" -> 50000 rpm
* ``attributes``   -- schema-driven attribute extraction with a confidence and the text span that
                      supports each value, plus the listing's component role (spec 10, 19, 101)
* ``applications`` -- dental application ontology and the Dental Confidence score (spec 11, 13.1, 90)
* ``provenance``   -- market observations and evidence records with value kinds (spec 14, 80, 81)

Every rule, weight and vocabulary comes from ``config/platform/knowledge.yaml``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from dip.settings import PROJECT_ROOT

CONFIG = PROJECT_ROOT / "config" / "platform" / "knowledge.yaml"


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load(Path(CONFIG).read_text(encoding="utf-8"))
