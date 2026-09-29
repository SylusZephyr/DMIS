"""Knowledge Graph (Module 11).

    Category -> Product Family -> Product Segment -> Product -> Listing
    Product -> Brand,  Listing -> Brand,  Supplier -> Segment

Built with NetworkX and persisted to DuckDB (mi_graph_nodes /
mi_graph_edges) as plain node/edge tables so it can be exported to
Neo4j later without changing the model (``to_cypher``).
"""

from __future__ import annotations

import json

import networkx as nx
import numpy as np
import pandas as pd


def _nid(kind: str, key) -> str:
    return f"{kind}:{key}"


def build_graph(market_name: str, families: pd.DataFrame, segments: pd.DataFrame, products: pd.DataFrame,
                listings: pd.DataFrame, suppliers: pd.DataFrame | None = None,
                supplier_matches: pd.DataFrame | None = None, include_listings: bool = True) -> nx.DiGraph:
    G = nx.DiGraph(name=market_name)
    cat = _nid("category", market_name)
    G.add_node(cat, kind="category", label=market_name)
    for _, f in families.iterrows():
        n = _nid("family", f"{market_name}/{f['family_id']}")
        G.add_node(n, kind="family", label=f["family_label"], listings=int(f.get("listings", 0)))
        G.add_edge(cat, n, rel="HAS_FAMILY")
    for _, s in segments.iterrows():
        n = _nid("segment", f"{market_name}/{s['segment_id']}")
        G.add_node(n, kind="segment", label=s["segment_label"],
                   revenue=_f(s.get("monthly_revenue")), score=_f(s.get("opportunity_score")),
                   products=int(s.get("products", 0) or 0))
        fam = s.get("family_id")
        parent = _nid("family", f"{market_name}/{fam}") if fam is not None and pd.notna(fam) else cat
        G.add_edge(parent if parent in G else cat, n, rel="HAS_SEGMENT")
    for _, p in products.iterrows():
        n = _nid("product", p["product_id"])
        G.add_node(n, kind="product", label=str(p["title"])[:80], revenue=_f(p.get("monthly_revenue")),
                   sales=_f(p.get("monthly_sales")), price=_f(p.get("price")), score=_f(p.get("opportunity_score")),
                   listings=int(p.get("listing_count", 1)), image=p.get("image"))
        seg = _nid("segment", f"{market_name}/{p['segment_id']}")
        G.add_edge(seg if seg in G else cat, n, rel="HAS_PRODUCT")
        if isinstance(p.get("brand"), str):
            b = _nid("brand", p["brand"].strip().lower())
            if b not in G:
                G.add_node(b, kind="brand", label=p["brand"])
            G.add_edge(n, b, rel="MADE_BY")
    if include_listings:
        for _, lst in listings.iterrows():
            n = _nid("listing", lst["id"])
            G.add_node(n, kind="listing", label=str(lst["id"]), price=_f(lst.get("price")), sales=_f(lst.get("sales")),
                       best=bool(lst.get("is_best_listing", False)))
            G.add_edge(_nid("product", lst["product_id"]), n, rel="HAS_LISTING")
    if suppliers is not None and supplier_matches is not None:
        for _, m in supplier_matches.iterrows():
            sn = _nid("supplier", m["supplier_id"])
            if sn not in G:
                sup = suppliers[suppliers["supplier_id"] == m["supplier_id"]]
                label = sup["name"].iloc[0] if len(sup) else m["supplier_id"]
                G.add_node(sn, kind="supplier", label=label, score=_f(sup["supplier_score"].iloc[0]) if len(sup) else None)
            seg = _nid("segment", f"{market_name}/{m['segment_id']}")
            if seg in G:
                G.add_edge(sn, seg, rel="CAN_SUPPLY", weight=_f(m.get("match_score")))
    return G


def _f(v):
    try:
        return None if v is None or pd.isna(v) else float(v)
    except (TypeError, ValueError):
        return None


def graph_tables(G: nx.DiGraph) -> tuple[pd.DataFrame, pd.DataFrame]:
    nodes = [{"node_id": n, "kind": d.get("kind"), "label": d.get("label"),
              "properties": json.dumps({k: v for k, v in d.items() if k not in ("kind", "label")}, default=str)}
             for n, d in G.nodes(data=True)]
    edges = [{"source": a, "target": b, "rel": d.get("rel"), "weight": d.get("weight")} for a, b, d in G.edges(data=True)]
    return pd.DataFrame(nodes), pd.DataFrame(edges, columns=["source", "target", "rel", "weight"])


def graph_from_tables(nodes: pd.DataFrame, edges: pd.DataFrame) -> nx.DiGraph:
    G = nx.DiGraph()
    for _, n in nodes.iterrows():
        props = json.loads(n["properties"]) if isinstance(n["properties"], str) else {}
        G.add_node(n["node_id"], kind=n["kind"], label=n["label"], **props)
    for _, e in edges.iterrows():
        w = e["weight"]
        G.add_edge(e["source"], e["target"], rel=e["rel"], weight=None if w is None or pd.isna(w) else float(w))
    return G


def layout_3d(G: nx.DiGraph, seed: int = 7) -> dict[str, np.ndarray]:
    """Deterministic 3D spring layout, category at the centre."""
    if len(G) == 0:
        return {}
    # weight=None: persisted edges carry NaN weights, which would poison every position
    return nx.spring_layout(G.to_undirected(), dim=3, seed=seed, k=1.2 / np.sqrt(max(len(G), 1)), iterations=80, weight=None)


def subgraph_around(G: nx.DiGraph, node: str, depth: int = 1) -> nx.DiGraph:
    """Expand a node: its neighbourhood up to ``depth`` hops (both directions)."""
    if node not in G:
        return nx.DiGraph()
    keep = {node}
    frontier = {node}
    for _ in range(depth):
        nxt = set()
        for n in frontier:
            nxt |= set(G.successors(n)) | set(G.predecessors(n))
        keep |= nxt
        frontier = nxt
    return G.subgraph(keep).copy()


def to_cypher(G: nx.DiGraph) -> str:
    """Export as Cypher statements for a future Neo4j migration."""
    lines = []
    for n, d in G.nodes(data=True):
        props = {k: v for k, v in d.items() if v is not None and not isinstance(v, (list, dict))}
        props["id"] = n
        body = ", ".join(f"{k}: {json.dumps(v, default=str)}" for k, v in props.items())
        lines.append(f"MERGE (:{str(d.get('kind', 'node')).title()} {{{body}}});")
    for a, b, d in G.edges(data=True):
        lines.append(f"MATCH (a {{id: {json.dumps(a)}}}), (b {{id: {json.dumps(b)}}}) MERGE (a)-[:{d.get('rel', 'RELATED')}]->(b);")
    return "\n".join(lines)
