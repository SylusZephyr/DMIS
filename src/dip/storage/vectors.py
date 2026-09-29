"""Vector similarity store on Qdrant.

Server mode when DIP_QDRANT_URL is set; otherwise qdrant-client's local
mode (same API, persisted under data/platform/qdrant). Used to:

* find similar products (``similar``)
* block candidate duplicate pairs at scale (``neighbours_for``)
* cluster unknown products against known ones

Embeddings are deterministic and need no model download: word 1-2 grams +
char 3-5 grams hashed into a fixed space, then L2-normalised and reduced
to ``DIM`` dimensions with a seeded random projection. Swapping in a local
sentence-embedding model only means replacing ``embed``.
"""

from __future__ import annotations

import atexit
import threading
import uuid
from functools import lru_cache

import numpy as np
from scipy.sparse import hstack
from sklearn.feature_extraction.text import HashingVectorizer
from sklearn.preprocessing import normalize

from dip.settings import get_settings

DIM = 256
COLLECTION = "products"
_word = HashingVectorizer(analyzer="word", ngram_range=(1, 2), n_features=2 ** 14, alternate_sign=False, norm=None)
_char = HashingVectorizer(analyzer="char_wb", ngram_range=(3, 5), n_features=2 ** 14, alternate_sign=False, norm=None)


@lru_cache(maxsize=1)
def _projection() -> np.ndarray:
    rng = np.random.default_rng(20260924)
    return rng.standard_normal((2 ** 15, DIM)).astype(np.float32) / np.sqrt(DIM)


def embed(texts: list[str]) -> np.ndarray:
    texts = [(t or "").lower() for t in texts]
    X = hstack([_word.transform(texts), _char.transform(texts)]).tocsr()
    X = normalize(X.astype(np.float32))
    dense = X @ _projection()
    return normalize(np.asarray(dense, dtype=np.float32))


def point_id(key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


class VectorStore:
    _lock = threading.Lock()

    def __init__(self):
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        s = get_settings()
        if s.qdrant_url:
            self.client = QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key)
            self.backend = "qdrant server"
        else:
            path = s.data_dir / "qdrant"
            path.mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=str(path))
            self.backend = "qdrant local"
        if not self.client.collection_exists(COLLECTION):
            self.client.create_collection(COLLECTION, vectors_config=VectorParams(size=DIM, distance=Distance.COSINE))

    def replace_market(self, market: str, product_ids: list[str], texts: list[str], payloads: list[dict]) -> int:
        from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue, PointStruct

        with self._lock:
            self.client.delete(COLLECTION, points_selector=FilterSelector(
                filter=Filter(must=[FieldCondition(key="market", match=MatchValue(value=market))])))
            if not product_ids:
                return 0
            vecs = embed(texts)
            batch = 512
            for i in range(0, len(product_ids), batch):
                pts = [PointStruct(id=point_id(pid), vector=vecs[j].tolist(),
                                   payload={"product_id": pid, "market": market, **payloads[j]})
                       for j, pid in enumerate(product_ids[i:i + batch], start=i)]
                self.client.upsert(COLLECTION, points=pts)
        return len(product_ids)

    def similar_to_text(self, text: str, limit: int = 10, market: str | None = None) -> list[dict]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        flt = Filter(must=[FieldCondition(key="market", match=MatchValue(value=market))]) if market else None
        res = self.client.query_points(COLLECTION, query=embed([text])[0].tolist(), limit=limit, query_filter=flt)
        return [{"score": round(float(p.score), 4), **(p.payload or {})} for p in res.points]

    def similar(self, product_id: str, limit: int = 10, market: str | None = None) -> list[dict]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        pts = self.client.retrieve(COLLECTION, ids=[point_id(product_id)], with_vectors=True)
        if not pts:
            return []
        flt = Filter(must=[FieldCondition(key="market", match=MatchValue(value=market))]) if market else None
        res = self.client.query_points(COLLECTION, query=pts[0].vector, limit=limit + 1, query_filter=flt)
        return [{"score": round(float(p.score), 4), **(p.payload or {})} for p in res.points
                if (p.payload or {}).get("product_id") != product_id][:limit]

    def count(self) -> int:
        return self.client.count(COLLECTION).count


def neighbours_for(texts: list[str], k: int = 10) -> list[list[int]]:
    """In-batch approximate neighbours for dedup blocking (exact cosine on
    the projected space, chunked -- O(n * chunk) memory, fine to ~10^6)."""
    V = embed(texts)
    n = len(V)
    k = min(k, n - 1) if n > 1 else 0
    out: list[list[int]] = []
    for i in range(0, n, 2048):
        S = V[i:i + 2048] @ V.T
        for r, row in enumerate(S):
            row[i + r] = -1
            idx = np.argpartition(-row, k)[:k] if k else []
            out.append(list(map(int, idx)))
    return out


@lru_cache(maxsize=1)
def get_vector_store() -> VectorStore:
    store = VectorStore()
    atexit.register(store.client.close)  # clean shutdown of qdrant local mode
    return store
