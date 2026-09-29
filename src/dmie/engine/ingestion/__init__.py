"""Module 1 -- Universal Data Ingestion Engine."""

from dmie.engine.ingestion.adapters import IngestionResult, ingest, ingest_frame, read_table
from dmie.engine.ingestion.schema_detector import DetectionResult, detect_schema

__all__ = ["DetectionResult", "IngestionResult", "detect_schema", "ingest", "ingest_frame", "read_table"]
