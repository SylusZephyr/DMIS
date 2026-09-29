"""Category boundary: which marketplace sub-categories belong to this market?

Keyword-built exports mix in unrelated products (e.g. water flossers mentioning "implants"). Each
sub-category found in the data (the `category` field, e.g. SellerSprite 小类目) is scored against the
market's own definition in config/categories.yaml:

* include / exclude terms: the category's explicit ``scope_terms: {include: [...], exclude: [...]}`` when it has
  them; otherwise parsed from the definition -- text before "Excludes" = include, after it (up to "Boundary
  cases", which is neutral) = exclude, with negated phrases ("not educational", "no genuine ... intent",
  "rather than ...") dropped so a word the definition *keeps* never becomes exclusion evidence
* terms in both, generic domain words, and terms in most sub-category names carry no evidence

Evidence = name hits (weighted) + share of the sub-category's titles containing include / exclude
terms. Clear exclude -> out of scope (listings kept with that reason, never silently dropped);
clear include -> in; otherwise **review** (kept in, flagged). A large sub-category (more than
``max_auto_exclude_share`` of the listings) is never auto-excluded unless its category sets
``boundary: enforce`` in config/categories.yaml. Decisions confirmed by a person
(ScopeDecision table) override the classifier on every run. Markets without a definition keep all
sub-categories and report their composition.
"""

from __future__ import annotations

import re
from functools import lru_cache

import pandas as pd
import yaml

from dip.metrics import config
from dip.settings import PROJECT_ROOT
from dip.storage import business as b


@lru_cache(maxsize=1)
def definitions() -> dict:
    data = yaml.safe_load((PROJECT_ROOT / "config" / "categories.yaml").read_text(encoding="utf-8")) or {}
    return data.get("categories", data)


def _norm(tok: str) -> str:
    t = tok.lower()
    for suf, rep in (("ies", "y"), ("sses", "ss"), ("es", ""), ("s", "")):
        if len(t) > 4 and t.endswith(suf) and not t.endswith("ss"):
            return t[: -len(suf)] + rep
    return t


def terms(text: str | None) -> set[str]:
    cfg = config()["scope"]
    generic = {_norm(g) for g in cfg["generic_terms"]}
    out = set()
    for tok in re.findall(r"[a-zA-Z][a-zA-Z\-]+", text or ""):
        for part in tok.split("-"):
            n = _norm(part)
            if len(n) >= cfg["min_term_length"] and n not in generic and n not in _STOP:
                out.add(n)
    return out


_STOP = {"the", "and", "for", "with", "that", "this", "whose", "which", "from", "into", "its", "their", "they", "them",
         "are", "not", "any", "one", "only", "such", "also", "themselves", "itself", "other", "several", "specific",
         "specifically", "being", "directly", "primary", "purpose", "product", "copy", "list", "lists", "mention", "merely",
         "situation", "compatible", "alongside", "without", "whether", "case", "cases", "judgment", "careful", "match",
         "keyword", "happen", "happens", "include", "includes", "including", "among", "shown", "used", "use", "marketing",
         # words about the definition or the export, not product types ("... this project's human-dental scope",
         # "... even when the raw export's sub-category groups them ...")
         "category", "categories", "export", "raw", "sub", "scope", "project", "industry", "even", "when", "but", "during",
         "item", "items", "group", "groups", "different", "function", "real", "human", "row", "count", "majority", "large"}


def split_definition(desc: str) -> tuple[str, str]:
    """(include text, exclude text); 'Boundary cases' onwards is neutral."""
    d = desc or ""
    m = re.search(r"\bexclud\w*", d, re.I)
    inc = d[: m.start()] if m else d
    exc = d[m.end():] if m else ""
    bc = re.search(r"\bboundary cases?\b", exc, re.I)
    if bc:
        exc = exc[: bc.start()]
    return inc, exc


_NEGATED = re.compile(r"\b(?:not|no|never|without|rather than)\b[^,.;:()\"]*", re.I)


def drop_negated(text: str) -> str:
    """Remove negated phrases: in "decorative or wearable, not educational" the word *educational* describes what
    stays in scope, so it must not become an exclusion term."""
    return _NEGATED.sub(" ", text or "")


def scope_terms(defin: dict) -> tuple[set[str], set[str]]:
    """(include terms, exclude terms) of a category definition (see module docstring)."""
    explicit = defin.get("scope_terms") or {}
    if explicit.get("include") or explicit.get("exclude"):
        return terms(" ".join(explicit.get("include") or [])), terms(" ".join(explicit.get("exclude") or []))
    inc_t, exc_t = split_definition(defin.get("description") or "")
    return terms(inc_t), terms(drop_negated(exc_t))


def classify(frame: pd.DataFrame, market: str) -> pd.DataFrame:
    """One row per sub-category: listings, include/exclude evidence, auto decision, final decision, reason."""
    cfg = config()["scope"]
    if "category" not in frame or frame["category"].isna().all():
        return pd.DataFrame(columns=["category", "listings", "decision", "source", "reason"])
    defin = definitions().get(market) or {}
    inc, exc = scope_terms(defin)
    both = inc & exc
    inc, exc = inc - both, exc - both
    cats = frame["category"].fillna("(none)").astype(str)
    names = sorted(cats.unique())
    # terms in most sub-category names are domain words, not evidence
    name_terms = {c: terms(c) for c in names}
    freq = pd.Series([t for ts in name_terms.values() for t in ts]).value_counts()
    common = set(freq[freq > max(2, len(names) / 2)].index)
    inc, exc = inc - common, exc - common
    overrides = _overrides(market)
    rows = []
    titles = frame["title"].fillna("").astype(str)
    title_terms = titles.map(terms)
    for c in names:
        m = (cats == c).to_numpy()
        tt = title_terms[m]
        inc_share = float(tt.map(lambda s: bool(s & inc)).mean()) if m.any() else 0.0
        exc_share = float(tt.map(lambda s: bool(s & exc)).mean()) if m.any() else 0.0
        n_in = len(name_terms[c] & inc)
        n_out = len(name_terms[c] & exc)
        ev_in = inc_share + cfg["name_weight"] * n_in
        ev_out = exc_share + cfg["name_weight"] * n_out
        if not (inc or exc):
            auto, why = "in", "no category definition -- kept"
        elif ev_out - ev_in >= cfg["out_margin"]:
            share = float(m.mean())
            if exc_share <= inc_share and not (exc_share == inc_share == 0):
                auto = "review"
                why = (f"name suggests exclusion {sorted(name_terms[c] & exc)} but titles disagree "
                       f"({inc_share:.0%} match included terms, {exc_share:.0%} excluded)")
            elif share > cfg["max_auto_exclude_share"] and defin.get("boundary") != "enforce":
                auto = "review"
                why = (f"evidence points to exclusion, but this sub-category holds {share:.0%} of the listings -- "
                       "a person must confirm (or set boundary: enforce for this category in config/categories.yaml)")
            elif share > cfg["max_auto_exclude_share"]:
                auto = "out"
                why = (f"evidence points to exclusion and the category's boundary is enforced (config/categories.yaml); "
                       f"this sub-category holds {share:.0%} of the listings")
            else:
                auto = "out"
                why = (f"name matches excluded terms {sorted(name_terms[c] & exc)}" if n_out else
                       f"{exc_share:.0%} of titles match excluded terms vs {inc_share:.0%} included")
        elif ev_in - ev_out >= cfg["in_margin"]:
            auto = "in"
            why = (f"name matches included terms {sorted(name_terms[c] & inc)}" if n_in else
                   f"{inc_share:.0%} of titles match included terms vs {exc_share:.0%} excluded")
        else:
            auto, why = "review", f"unclear: {inc_share:.0%} of titles match included terms, {exc_share:.0%} excluded"
        ov = overrides.get(c)
        rows.append({"category": c, "listings": int(m.sum()), "include_title_share": round(inc_share, 3),
                     "exclude_title_share": round(exc_share, 3), "name_include_hits": n_in, "name_exclude_hits": n_out,
                     "auto_decision": auto, "auto_reason": why,
                     "decision": ov["decision"] if ov else auto, "source": "person" if ov else "classifier",
                     "reason": (f"confirmed by {ov['by'] or 'a person'}" + (f": {ov['note']}" if ov.get("note") else "")) if ov else why})
    return pd.DataFrame(rows).sort_values("listings", ascending=False).reset_index(drop=True)


def _overrides(market: str) -> dict:
    try:
        with b.session() as s:
            return {x.category: {"decision": x.decision, "by": x.decided_by, "note": x.note}
                    for x in s.query(b.ScopeDecision).filter_by(market_name=market).all() if x.decision in ("in", "out")}
    except Exception:
        return {}


def apply(frame: pd.DataFrame, market: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Mark listings of out-of-scope sub-categories as not relevant (with the reason); flag review ones."""
    table = classify(frame, market)
    df = frame                                              # in place: the record frame can have 10^6 rows
    df["scope_status"] = "in"
    if table.empty:
        return df, table
    dec = table.set_index("category")
    cats = df["category"].fillna("(none)").astype(str)
    status = cats.map(dec["decision"]).fillna("in")
    reason = cats.map(dec["reason"])
    df["scope_status"] = status
    human = df["relevance_status"].astype(str).str.startswith("human").to_numpy()   # a person's label wins
    out = (status == "out").to_numpy() & df["is_relevant"].to_numpy() & ~human
    df.loc[out, "is_relevant"] = False
    df.loc[out, "relevance_status"] = "out_of_scope"
    df.loc[out, "relevance_explanation"] = "sub-category '" + cats[out] + "' is outside the category definition: " + reason[out]
    return df, table


def impact(frame: pd.DataFrame, table: pd.DataFrame) -> dict:
    """Listing and revenue share of each decision, and what enforcing the boundary would remove: the
    review sub-categories whose evidence points to exclusion (source revenue where the export has it)."""
    if table.empty:
        return {}
    cats = frame["category"].fillna("(none)").astype(str)
    rev = pd.to_numeric(frame["revenue"], errors="coerce") if "revenue" in frame else pd.Series(float("nan"), index=frame.index)
    tot_n, tot_r = len(frame), float(rev.sum())
    by_cat = pd.DataFrame({"listings": cats.value_counts(), "revenue": rev.groupby(cats).sum()}).fillna(0)
    out: dict = {"listings": tot_n, "revenue": tot_r if tot_r > 0 else None, "by_decision": {}}
    for d, g in table.groupby("decision"):
        c = by_cat.reindex(g["category"]).fillna(0)
        out["by_decision"][d] = {"listing_share": round(float(c["listings"].sum()) / max(tot_n, 1), 3),
                                 "revenue_share": round(float(c["revenue"].sum()) / tot_r, 3) if tot_r > 0 else None}
    enforce = table[(table["decision"] == "review") & table["auto_reason"].str.startswith("evidence points to exclusion")]
    c = by_cat.reindex(enforce["category"]).fillna(0)
    out["if_enforced"] = {"categories": enforce["category"].tolist(),
                          "listing_share_removed": round(float(c["listings"].sum()) / max(tot_n, 1), 3),
                          "revenue_share_removed": round(float(c["revenue"].sum()) / tot_r, 3) if tot_r > 0 else None}
    return out
