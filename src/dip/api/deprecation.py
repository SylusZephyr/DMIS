"""Retired v1 endpoints: they answer HTTP 410 Gone with the endpoint that replaces them.

Kept as stubs (instead of 404) so old scripts and bookmarks get a pointer; listed in OpenAPI as deprecated.
See docs/API_DOCUMENTATION.md, "Retired endpoints".
"""

from __future__ import annotations

from fastapi import HTTPException

RETIRED = {
    "POST /analyst/ask": "POST /api/v2/analyst/ask-v3",
    "POST /ask": "POST /api/v2/analyst/ask-v3",
    "POST /launch/evaluate": "POST /api/v2/launch/simulate",
    "POST /launch/compare": "POST /api/v2/launch/compare-v3",
    "POST /simulate": "POST /api/v2/launch/simulate",
    "POST /shopping/recommend": "POST /api/v2/shopping/recommend-v3",
    "GET /markets/{market}/galaxy": "GET /api/v2/markets/{market}/galaxy-v3",
    "GET /markets/{market}/competitors": "GET /api/v2/markets/{market}/competitors-v3",
    "GET /competitors/{brand}": "GET /api/v2/markets/{market}/competitors-v3",
}


def gone(endpoint: str):
    """Raise 410 for a retired endpoint (``endpoint`` is a key of ``RETIRED``)."""
    raise HTTPException(410, {"error": "endpoint retired", "endpoint": f"{endpoint.split()[0]} /api/v2{endpoint.split()[1]}",
                              "use_instead": RETIRED[endpoint]})


def responses(endpoint: str) -> dict:
    return {410: {"description": f"Retired. Use {RETIRED[endpoint]}."}}
