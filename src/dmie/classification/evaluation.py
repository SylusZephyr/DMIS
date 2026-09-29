"""Compares classifier predictions (listing_classification table) against
the human-reviewed gold dataset. Read-only with respect to the gold
dataset — never writes to data/validated/.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import pandas as pd

from dmie.database.connection import PROJECT_ROOT

GOLD_PATH = PROJECT_ROOT / "data" / "validated" / "denture_base" / "gold_labels.csv"
LABELS = ["RELEVANT", "IRRELEVANT", "UNCERTAIN"]

# Gold dataset's `relevant` column uses YES/NO/UNCERTAIN; the classifier's
# `relevance_class` uses RELEVANT/IRRELEVANT/UNCERTAIN. Same 3-way concept,
# different vocabulary — mapped explicitly here rather than assumed equal.
_GOLD_TO_CLASS = {"YES": "RELEVANT", "NO": "IRRELEVANT", "UNCERTAIN": "UNCERTAIN"}


@dataclass
class ClassMetrics:
    label: str
    precision: float
    recall: float
    f1: float
    tp: int
    fp: int
    fn: int


@dataclass
class EvaluationReport:
    n_evaluated: int
    metrics: list[ClassMetrics]
    confusion_matrix: pd.DataFrame  # rows = gold, columns = predicted


def load_gold() -> pd.DataFrame:
    gold = pd.read_csv(GOLD_PATH)
    gold["gold_class"] = gold["relevant"].map(_GOLD_TO_CLASS)
    return gold


def load_predictions(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute(
        "SELECT listing_id, relevance_class AS pred_class, confidence, "
        "reason, review_status FROM listing_classification"
    ).df()


def _precision_recall_f1(gold_class: pd.Series, pred_class: pd.Series, label: str) -> ClassMetrics:
    tp = int(((gold_class == label) & (pred_class == label)).sum())
    fp = int(((gold_class != label) & (pred_class == label)).sum())
    fn = int(((gold_class == label) & (pred_class != label)).sum())
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return ClassMetrics(label=label, precision=precision, recall=recall, f1=f1, tp=tp, fp=fp, fn=fn)


def confusion_matrix(gold_class: pd.Series, pred_class: pd.Series) -> pd.DataFrame:
    matrix = pd.crosstab(gold_class, pred_class)
    return matrix.reindex(index=LABELS, columns=LABELS, fill_value=0)


def evaluate(con: duckdb.DuckDBPyConnection) -> EvaluationReport:
    gold = load_gold()
    predictions = load_predictions(con)
    merged = gold.merge(predictions, on="listing_id", how="inner")

    metrics = [_precision_recall_f1(merged["gold_class"], merged["pred_class"], label) for label in LABELS]
    cm = confusion_matrix(merged["gold_class"], merged["pred_class"])

    return EvaluationReport(n_evaluated=len(merged), metrics=metrics, confusion_matrix=cm)


def format_report(report: EvaluationReport) -> str:
    lines = [f"Evaluated {report.n_evaluated} gold-labeled listings", ""]
    lines.append(f"{'label':<12}{'precision':>10}{'recall':>10}{'f1':>10}{'tp':>6}{'fp':>6}{'fn':>6}")
    for m in report.metrics:
        lines.append(
            f"{m.label:<12}{m.precision:>10.2f}{m.recall:>10.2f}{m.f1:>10.2f}{m.tp:>6}{m.fp:>6}{m.fn:>6}"
        )
    lines.append("")
    lines.append("Confusion matrix (rows = gold, columns = predicted):")
    lines.append(report.confusion_matrix.to_string())
    return "\n".join(lines)


if __name__ == "__main__":
    from dmie.database.connection import get_connection

    connection = get_connection()
    try:
        result = evaluate(connection)
    finally:
        connection.close()
    print(format_report(result))
