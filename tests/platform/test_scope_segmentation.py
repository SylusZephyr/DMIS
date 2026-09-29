"""Phase 5.3: category boundary (with safeguards and person overrides) and taxonomy-anchored
segmentation (split only when clusters are distinct; keywords without stop terms / brands)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dip.metrics import config
from dip.pipeline import scope
from dip.pipeline.clustering import taxonomy


@pytest.fixture(autouse=True)
def definition(monkeypatch):
    defs = {"widgets": {"description": "Brushless lab micromotor handpieces and their control boxes. "
                                       "Excludes: toothbrushes, dental floss, jewelry, toys. Boundary cases: kits."}}
    monkeypatch.setattr(scope, "definitions", lambda: defs)


def contaminated(n_core=40, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_core):
        rows.append({"id": f"A{i}", "category": "Lab Micromotors", "title": f"Brushless micromotor handpiece {rng.integers(20, 60)}k rpm"})
    for i in range(12):
        rows.append({"id": f"B{i}", "category": "Manual Toothbrushes", "title": f"Soft toothbrush for implants pack {i}"})
    for i in range(10):
        rows.append({"id": f"C{i}", "category": "Dental Floss", "title": f"Floss threaders for braces bridges implants {i}"})
    for i in range(5):
        rows.append({"id": f"D{i}", "category": "Workshop Supplies", "title": f"Bench clamp tool {i}"})
    df = pd.DataFrame(rows)
    df["is_relevant"], df["relevance_status"], df["relevance_explanation"] = True, "relevant", ""
    return df


def test_contamination_excluded_with_reason():
    df, table = scope.apply(contaminated(), "widgets")
    dec = table.set_index("category")["decision"]
    assert dec["Manual Toothbrushes"] == "out" and dec["Dental Floss"] == "out"
    assert dec["Lab Micromotors"] == "in"
    assert dec["Workshop Supplies"] == "review"                              # no evidence either way -> a person decides
    kept = df[df["is_relevant"]]
    assert set(kept["category"]) == {"Lab Micromotors", "Workshop Supplies"}  # precision: nothing excluded is in scope
    out = df[~df["is_relevant"]]
    assert (out["relevance_status"] == "out_of_scope").all() and out["relevance_explanation"].str.contains("outside").all()


def test_large_subcategory_never_auto_excluded():
    df = contaminated(n_core=5)                                              # toothbrushes become 12 / 32 = 37 % of listings
    table = scope.classify(df, "widgets").set_index("category")
    assert table.loc["Manual Toothbrushes", "decision"] == "review"
    assert "a person must confirm" in table.loc["Manual Toothbrushes", "reason"]


def test_titles_contradicting_the_name_go_to_review():
    df = contaminated()
    df.loc[df["category"] == "Dental Floss", "title"] = "Brushless micromotor handpiece spare"
    table = scope.classify(df, "widgets").set_index("category")
    assert table.loc["Dental Floss", "decision"] == "review"


def test_person_override_and_human_labels_win(monkeypatch):
    monkeypatch.setattr(scope, "_overrides", lambda m: {"Dental Floss": {"decision": "in", "by": "pm@x", "note": "we sell floss"}})
    df = contaminated()
    df.loc[df["id"] == "B0", "relevance_status"] = "human_relevant"
    out, table = scope.apply(df, "widgets")
    assert table.set_index("category").loc["Dental Floss", "source"] == "person"
    assert out.loc[out["id"] == "C0", "is_relevant"].item()                   # floss kept by the person's decision
    assert out.loc[out["id"] == "B0", "is_relevant"].item()                   # a person's relevance label is never overridden


def test_no_definition_keeps_everything(monkeypatch):
    monkeypatch.setattr(scope, "definitions", lambda: {})
    df, table = scope.apply(contaminated(), "unknown")
    assert df["is_relevant"].all() and set(table["decision"]) == {"in"}


# ---------------------------------------------------------------- segmentation
def listings_for_split():
    rows = []
    for i in range(20):
        rows.append({"id": f"M{i}", "category": "Lab Equipment", "brand": f"B{i % 4}", "price": 300.0,
                     "title": f"Brushless micromotor handpiece {50 + i % 3}000 rpm polishing"})
    for i in range(20):
        rows.append({"id": f"T{i}", "category": "Lab Equipment", "brand": f"B{i % 4}", "price": 10.0,
                     "title": "Disposable impression trays plastic upper lower 50 pcs"})
    for i in range(6):
        rows.append({"id": f"W{i}", "category": "Modeling Wax", "brand": "Acme", "price": 12.0, "title": "Base plate wax sheets pink"})
    return pd.DataFrame(rows)


def test_distinct_products_are_split_and_labelled():
    res = taxonomy.discover(listings_for_split())
    segs = res.segments.set_index("segment_id")
    lab = segs[segs["family_label"] == "Lab Equipment"]
    assert len(lab) == 2                                                      # micromotors vs trays: distinct
    assert all(lbl.startswith("Lab Equipment · ") for lbl in lab["segment_label"])
    wax = segs[segs["family_label"] == "Modeling Wax"]
    assert len(wax) == 1 and wax["segment_label"].iloc[0] == "Modeling Wax"   # small sub-category = one segment
    stop = {s.lower() for s in config()["segmentation"]["stop_terms"]}
    for terms in segs["top_terms"]:
        assert not (set(" ".join(terms).split()) & (stop | {"b0", "b1", "acme"}))


def test_variants_of_one_product_are_not_split():
    # realistic variant titles: one product type, long varied descriptions, one variant word differs
    rng = np.random.default_rng(4)
    vocab = ["dental", "elevator", "luxating", "root", "tip", "extraction", "stainless", "steel", "surgical", "instrument",
             "periotome", "tooth", "surgery", "german", "grade", "handle", "blade", "set", "professional", "dentist",
             "oral", "tools", "autoclavable", "ergonomic", "sharp", "precision", "clinic", "lab"]
    df = pd.DataFrame([{"id": f"E{i}", "category": "Elevators", "brand": f"B{i % 5}", "price": 20.0,
                        "title": " ".join(rng.choice(vocab, size=12, replace=False)) + f" {['straight', 'curved', 'angled'][i % 3]}"}
                       for i in range(30)])
    res = taxonomy.discover(df)
    assert len(res.segments) == 1 and res.method["split"]["Elevators"]["segments"] == 1


def test_no_subcategory_falls_back_to_text_discovery():
    df = listings_for_split().drop(columns=["category"])
    res = taxonomy.discover(df)
    assert "text clusters" in res.method["basis"] and len(res.segments) >= 1


def test_negated_and_meta_words_never_become_exclusion_terms():
    """'decorative or wearable, not educational' keeps educational items in scope: the parser used to turn
    'educational' into exclusion evidence and held the dental-models market's core sub-category in review."""
    from dip.pipeline.scope import drop_negated, scope_terms

    assert "educational" not in drop_negated("novelty items whose function is decorative or wearable, not educational (earrings)")
    assert "earrings" in drop_negated("novelty items whose function is decorative or wearable, not educational (earrings)")
    d = {"description": "Dental teaching models and impression trays. Excludes: decor items, not educational "
                        "(tooth-shaped earrings, hair clips); pet products (a different industry from this project's "
                        "human-dental scope); toys with no genuine dental-education design intent. Boundary cases: skulls."}
    inc, exc = scope_terms(d)
    assert {"earring", "hair", "clip", "pet"} <= exc
    assert not ({"educational", "education", "genuine", "intent", "human", "project", "scope", "skull"} & exc)
    explicit = {**d, "scope_terms": {"include": ["typodont"], "exclude": ["earring"]}}
    assert scope_terms(explicit) == ({"typodont"}, {"earring"})              # explicit lists win over parsing
