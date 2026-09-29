"""Knowledge-graph store: Neo4j when DIP_NEO4J_URI is set, otherwise an
embedded store (NetworkX in memory, persisted as node/edge tables in DuckDB).

Both implement the same small API so the pipeline and the UI never care
which one is running:

    upsert(nodes, edges)          nodes: [{id, kind, label, props}], edges: [{source, target, rel, props}]
    replace_market(market, ...)   atomically swap one market's subgraph
    neighbourhood(id, depth, kinds, limit) -> {nodes, edges}
    nodes(kind=None, market=None, limit) / stats()

Graph model (see ARCHITECTURE_V2.md 2.2):
Category -HAS_FAMILY-> ProductFamily -HAS_SEGMENT-> Segment -HAS_MODEL-> Product -HAS_LISTING-> Listing -SOLD_BY-> Seller
Product -MADE_BY-> Brand, Product -PRODUCT_SIMILAR_TO-> Product, Product -COMPETES_WITH-> Product,
Segment -SUPPLIED_BY-> Supplier, Supplier -LOCATED_IN-> Country, * -BELONGS_TO-> Category
"""

from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from functools import lru_cache

import duckdb
import networkx as nx
import pandas as pd

from dip.settings import get_settings

NODE_KINDS = ["Industry", "Category", "ProductFamily", "Segment", "Product", "Listing", "Seller", "Brand",
              "Supplier", "Country"]


class GraphStore(ABC):
    backend = "abstract"

    @abstractmethod
    def replace_market(self, market: str, nodes: list[dict], edges: list[dict]) -> None: ...

    @abstractmethod
    def upsert(self, nodes: list[dict], edges: list[dict], market: str = "__global__") -> None: ...

    @abstractmethod
    def neighbourhood(self, node_id: str, depth: int = 1, kinds: list[str] | None = None, limit: int = 500) -> dict: ...

    @abstractmethod
    def nodes(self, kind: str | None = None, market: str | None = None, limit: int = 1000) -> list[dict]: ...

    @abstractmethod
    def stats(self) -> dict: ...

    def find_path(self, source: str, target: str, max_len: int = 6) -> dict:
        """Shortest connection between two nodes, ignoring direction; ties go to stronger evidence
        (edge weight 1 + (1 - strength)). Returns {"nodes": [...], "edges": [...]} in path order."""
        return {"nodes": [], "edges": []}


_RESERVED = {"id", "kind", "label", "market", "rel"}          # structural attributes; a prop may never shadow them


def _clean_props(props: dict | None) -> dict:
    out = {}
    for k, v in (props or {}).items():
        if k in _RESERVED:
            k = f"prop_{k}"
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        if isinstance(v, (dict, list, tuple)):
            v = json.dumps(v, default=str, ensure_ascii=False)
        out[k] = v
    return out


class EmbeddedGraphStore(GraphStore):
    """NetworkX + DuckDB persistence. Fine for ~10^6 edges on one machine."""

    backend = "embedded"
    _lock = threading.Lock()

    def __init__(self, path: str):
        self.path = path
        self._g: nx.MultiDiGraph | None = None
        with self._con() as con:
            con.execute("CREATE TABLE IF NOT EXISTS g_nodes (market VARCHAR, id VARCHAR, kind VARCHAR, label VARCHAR, props VARCHAR)")
            con.execute("CREATE TABLE IF NOT EXISTS g_edges (market VARCHAR, source VARCHAR, target VARCHAR, rel VARCHAR, props VARCHAR)")

    def _con(self):
        return duckdb.connect(self.path)

    def _graph(self) -> nx.MultiDiGraph:
        if self._g is None:
            g = nx.MultiDiGraph()
            with self._con() as con:
                for m, i, k, l, p in con.execute("SELECT market, id, kind, label, props FROM g_nodes").fetchall():
                    g.add_node(i, kind=k, label=l, market=m, **json.loads(p or "{}"))
                for m, s, t, r, p in con.execute("SELECT market, source, target, rel, props FROM g_edges").fetchall():
                    g.add_edge(s, t, key=r, rel=r, market=m, **json.loads(p or "{}"))
            self._g = g
        return self._g

    def _write(self, market: str, nodes: list[dict], edges: list[dict], replace: bool) -> None:
        nd = pd.DataFrame([{"market": market, "id": n["id"], "kind": n["kind"], "label": str(n.get("label", ""))[:300],
                            "props": json.dumps(_clean_props(n.get("props")), default=str, ensure_ascii=False)} for n in nodes],
                          columns=["market", "id", "kind", "label", "props"])
        ed = pd.DataFrame([{"market": market, "source": e["source"], "target": e["target"], "rel": e["rel"],
                            "props": json.dumps(_clean_props(e.get("props")), default=str)} for e in edges],
                          columns=["market", "source", "target", "rel", "props"])
        with self._lock, self._con() as con:
            con.execute("BEGIN")
            if replace:
                con.execute("DELETE FROM g_nodes WHERE market = ?", [market])
                con.execute("DELETE FROM g_edges WHERE market = ?", [market])
            else:
                if len(nd):
                    con.register("nd", nd)
                    con.execute("DELETE FROM g_nodes WHERE id IN (SELECT id FROM nd)")
                    con.unregister("nd")
            if len(nd):
                con.register("nd", nd); con.execute("INSERT INTO g_nodes SELECT * FROM nd"); con.unregister("nd")
            if len(ed):
                con.register("ed", ed); con.execute("INSERT INTO g_edges SELECT * FROM ed"); con.unregister("ed")
            con.execute("COMMIT")
        self._g = None  # reload lazily

    def replace_market(self, market, nodes, edges):
        self._write(market, nodes, edges, replace=True)

    def upsert(self, nodes, edges, market="__global__"):
        self._write(market, nodes, edges, replace=False)

    @staticmethod
    def _node_dict(g, n) -> dict:
        d = dict(g.nodes[n])
        return {"id": n, "kind": d.pop("kind", None), "label": d.pop("label", n), "market": d.pop("market", None), "props": d}

    def neighbourhood(self, node_id, depth=1, kinds=None, limit=500):
        g = self._graph()
        if node_id not in g:
            return {"nodes": [], "edges": []}
        keep, frontier = {node_id}, {node_id}
        for _ in range(depth):
            nxt = set()
            for n in frontier:
                for m in list(g.successors(n)) + list(g.predecessors(n)):
                    if kinds and g.nodes[m].get("kind") not in kinds:
                        continue
                    nxt.add(m)
                    if len(keep) + len(nxt) >= limit:
                        break
            nxt -= keep
            keep |= nxt
            frontier = nxt
            if len(keep) >= limit:
                break
        sub = g.subgraph(keep)
        return {"nodes": [self._node_dict(g, n) for n in sub.nodes],
                "edges": [{"source": a, "target": b, "rel": d.get("rel"),
                           "props": {k: v for k, v in d.items() if k not in ("rel", "market")}}
                          for a, b, d in sub.edges(data=True)]}

    def find_path(self, source, target, max_len=6):
        g = self._graph()
        if source not in g or target not in g:
            return {"nodes": [], "edges": []}
        u = nx.Graph()
        for a, b, d in g.edges(data=True):
            st = d.get("strength")
            w = 1.0 + (1.0 - float(st)) if isinstance(st, (int, float)) else 1.5
            if not u.has_edge(a, b) or u[a][b]["weight"] > w:
                u.add_edge(a, b, weight=w, data=(a, b, d))
        try:
            seq = nx.shortest_path(u, source, target, weight="weight")
        except nx.NetworkXNoPath:
            return {"nodes": [], "edges": []}
        if len(seq) - 1 > max_len:
            return {"nodes": [], "edges": [], "note": f"no connection within {max_len} steps"}
        edges = []
        for x, y in zip(seq, seq[1:]):
            a, b, d = u[x][y]["data"]
            edges.append({"source": a, "target": b, "rel": d.get("rel"),
                          "props": {k: v for k, v in d.items() if k not in ("rel", "market")}})
        return {"nodes": [self._node_dict(g, n) for n in seq], "edges": edges}

    def nodes(self, kind=None, market=None, limit=1000):
        g = self._graph()
        out = []
        for n, d in g.nodes(data=True):
            if kind and d.get("kind") != kind:
                continue
            if market and d.get("market") != market:
                continue
            out.append(self._node_dict(g, n))
            if len(out) >= limit:
                break
        return out

    def stats(self):
        with self._con() as con:
            nk = dict(con.execute("SELECT kind, count(*) FROM g_nodes GROUP BY kind").fetchall())
            er = dict(con.execute("SELECT rel, count(*) FROM g_edges GROUP BY rel").fetchall())
        return {"backend": self.backend, "nodes": nk, "edges": er}


class Neo4jGraphStore(GraphStore):
    backend = "neo4j"

    def __init__(self, uri: str, user: str, password: str | None):
        from neo4j import GraphDatabase

        self.driver = GraphDatabase.driver(uri, auth=(user, password or ""))
        with self.driver.session() as s:
            s.run("CREATE CONSTRAINT node_id IF NOT EXISTS FOR (n:Node) REQUIRE n.id IS UNIQUE")

    def _write(self, market, nodes, edges, replace):
        with self.driver.session() as s:
            if replace:
                s.run("MATCH (n:Node {market: $m}) DETACH DELETE n", m=market)
            by_kind: dict[str, list] = {}
            for n in nodes:
                by_kind.setdefault(n["kind"], []).append({"id": n["id"], "label": str(n.get("label", ""))[:300],
                                                          "market": market, **_clean_props(n.get("props"))})
            for kind, rows in by_kind.items():
                s.run(f"UNWIND $rows AS r MERGE (n:Node {{id: r.id}}) SET n += r, n:`{kind}`, n.kind = '{kind}'", rows=rows)
            by_rel: dict[str, list] = {}
            for e in edges:
                by_rel.setdefault(e["rel"], []).append({"s": e["source"], "t": e["target"], "p": _clean_props(e.get("props"))})
            for rel, rows in by_rel.items():
                s.run(f"UNWIND $rows AS r MATCH (a:Node {{id: r.s}}), (b:Node {{id: r.t}}) MERGE (a)-[x:`{rel}`]->(b) SET x += r.p",
                      rows=rows)

    def replace_market(self, market, nodes, edges):
        self._write(market, nodes, edges, True)

    def upsert(self, nodes, edges, market="__global__"):
        self._write(market, nodes, edges, False)

    def neighbourhood(self, node_id, depth=1, kinds=None, limit=500):
        q = (f"MATCH p=(a:Node {{id: $id}})-[*1..{int(depth)}]-(b:Node) "
             + ("WHERE b.kind IN $kinds " if kinds else "")
             + "WITH p LIMIT $limit UNWIND nodes(p) AS n UNWIND relationships(p) AS r "
               "RETURN collect(DISTINCT n) AS ns, collect(DISTINCT r) AS rs")
        with self.driver.session() as s:
            rec = s.run(q, id=node_id, kinds=kinds or [], limit=limit).single()
        if rec is None:
            return {"nodes": [], "edges": []}
        nodes = [{"id": n["id"], "kind": n.get("kind"), "label": n.get("label"), "market": n.get("market"),
                  "props": {k: v for k, v in dict(n).items() if k not in ("id", "kind", "label", "market")}} for n in rec["ns"]]
        edges = [{"source": r.start_node["id"], "target": r.end_node["id"], "rel": r.type, "props": dict(r)} for r in rec["rs"]]
        return {"nodes": nodes, "edges": edges}

    def find_path(self, source, target, max_len=6):
        q = (f"MATCH (a:Node {{id: $s}}), (b:Node {{id: $t}}), p = shortestPath((a)-[*..{int(max_len)}]-(b)) "
             "RETURN nodes(p) AS ns, relationships(p) AS rs")
        with self.driver.session() as s:
            rec = s.run(q, s=source, t=target).single()
        if rec is None:
            return {"nodes": [], "edges": []}
        nodes = [{"id": n["id"], "kind": n.get("kind"), "label": n.get("label"), "market": n.get("market"),
                  "props": {k: v for k, v in dict(n).items() if k not in ("id", "kind", "label", "market")}} for n in rec["ns"]]
        edges = [{"source": r.start_node["id"], "target": r.end_node["id"], "rel": r.type, "props": dict(r)} for r in rec["rs"]]
        return {"nodes": nodes, "edges": edges}

    def nodes(self, kind=None, market=None, limit=1000):
        where = []
        if kind:
            where.append("n.kind = $kind")
        if market:
            where.append("n.market = $market")
        q = "MATCH (n:Node) " + ("WHERE " + " AND ".join(where) if where else "") + " RETURN n LIMIT $limit"
        with self.driver.session() as s:
            rows = s.run(q, kind=kind, market=market, limit=limit)
            return [{"id": r["n"]["id"], "kind": r["n"].get("kind"), "label": r["n"].get("label"),
                     "market": r["n"].get("market"), "props": dict(r["n"])} for r in rows]

    def stats(self):
        with self.driver.session() as s:
            nk = {r["k"]: r["c"] for r in s.run("MATCH (n:Node) RETURN n.kind AS k, count(*) AS c")}
            er = {r["t"]: r["c"] for r in s.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c")}
        return {"backend": self.backend, "nodes": nk, "edges": er}


@lru_cache(maxsize=1)
def get_graph_store() -> GraphStore:
    s = get_settings()
    if s.neo4j_uri:
        return Neo4jGraphStore(s.neo4j_uri, s.neo4j_user, s.neo4j_password)
    return EmbeddedGraphStore(str(s.data_dir / "graph.duckdb"))
