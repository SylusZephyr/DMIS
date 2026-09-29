"""The feedback model learns from human corrections: logistic regression for
few labels, XGBoost (optional dependency) once there are enough."""

from pathlib import Path

import pandas as pd
import pytest

from dmie.engine.ingestion import ingest
from dmie.engine.relevance import classify_relevance, listing_text

ROOT = Path(__file__).resolve().parents[2]
ANIMAL = "animal anatomical models"


@pytest.fixture(scope="module")
def frame():
    return ingest(ROOT / "data" / "raw" / "dental_models" / "dental_models_sellersprite.xlsx").frame


def _feedback(frame: pd.DataFrame, n: int) -> pd.DataFrame:
    """A reviewer who agrees with the model except: animal dental models ARE dental (veterinary dentistry)."""
    base = classify_relevance(frame)
    fb = pd.DataFrame({"text": listing_text(frame).to_numpy(), "label": base["is_relevant"].to_numpy()})
    fb.loc[frame["category"].fillna("").str.lower().eq(ANIMAL).to_numpy(), "label"] = True
    return fb.head(n) if n < len(fb) else fb


def test_xgboost_learns_corrections(frame):
    pytest.importorskip("xgboost", reason="optional ML extra: pip install .[ml] (installed in CI)")
    fb = _feedback(frame, 400)
    assert len(fb) >= 200
    before = classify_relevance(frame)
    after = classify_relevance(frame, feedback=fb)
    animal = frame["category"].fillna("").str.lower().eq(ANIMAL)
    assert after.loc[animal, "relevance_evidence"].str.contains("feedback model (xgboost)", regex=False).all()
    assert after.loc[animal, "relevance_score"].mean() > before.loc[animal, "relevance_score"].mean() + 10
    # it does not wreck everything else
    other = ~animal
    agree = (after.loc[other, "is_relevant"] == before.loc[other, "is_relevant"]).mean()
    assert agree >= 0.95


def test_few_labels_use_logistic(frame):
    fb = _feedback(frame, 20)
    out = classify_relevance(frame, feedback=fb)
    ev = out["relevance_evidence"]
    assert ev.str.contains("feedback model (logistic)", regex=False).all() or fb["label"].nunique() < 2
