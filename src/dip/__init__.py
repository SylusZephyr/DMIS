"""Intelligence Platform v2 -- the Dental Market Intelligence Operating System.

Wraps Intelligence Core v1 (``dmie``) with a data lake, pluggable storage
(PostgreSQL / DuckDB / Neo4j / Qdrant, each with an embedded fallback),
a staged pipeline with jobs, the ``/api/v2`` API, and the Next.js 3D
interface in ``frontend/``. See ARCHITECTURE_V2.md.
"""

__version__ = "2.0.0-alpha"
