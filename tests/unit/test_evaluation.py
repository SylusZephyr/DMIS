import pandas as pd

from dmie.classification.evaluation import _precision_recall_f1, confusion_matrix


def test_precision_recall_f1_hand_computed():
    # 4 RELEVANT, 4 IRRELEVANT gold rows. Predicted: 3 correct RELEVANT,
    # 1 RELEVANT missed as IRRELEVANT (FN), 1 IRRELEVANT wrongly predicted
    # RELEVANT (FP), rest correct.
    gold = pd.Series(["RELEVANT", "RELEVANT", "RELEVANT", "RELEVANT",
                       "IRRELEVANT", "IRRELEVANT", "IRRELEVANT", "IRRELEVANT"])
    pred = pd.Series(["RELEVANT", "RELEVANT", "RELEVANT", "IRRELEVANT",
                       "RELEVANT", "IRRELEVANT", "IRRELEVANT", "IRRELEVANT"])

    m = _precision_recall_f1(gold, pred, "RELEVANT")
    assert m.tp == 3
    assert m.fp == 1
    assert m.fn == 1
    assert m.precision == 0.75  # 3 / (3 + 1)
    assert m.recall == 0.75     # 3 / (3 + 1)
    assert m.f1 == 0.75


def test_precision_recall_f1_no_predictions_for_label_is_zero_not_error():
    gold = pd.Series(["IRRELEVANT", "IRRELEVANT"])
    pred = pd.Series(["IRRELEVANT", "IRRELEVANT"])
    m = _precision_recall_f1(gold, pred, "RELEVANT")
    assert m.tp == 0 and m.fp == 0 and m.fn == 0
    assert m.precision == 0.0
    assert m.recall == 0.0
    assert m.f1 == 0.0


def test_confusion_matrix_shape_and_values():
    gold = pd.Series(["RELEVANT", "IRRELEVANT", "UNCERTAIN"])
    pred = pd.Series(["RELEVANT", "RELEVANT", "UNCERTAIN"])
    cm = confusion_matrix(gold, pred)
    assert list(cm.index) == ["RELEVANT", "IRRELEVANT", "UNCERTAIN"]
    assert list(cm.columns) == ["RELEVANT", "IRRELEVANT", "UNCERTAIN"]
    assert cm.loc["IRRELEVANT", "RELEVANT"] == 1
    assert cm.loc["UNCERTAIN", "UNCERTAIN"] == 1
    assert cm.loc["RELEVANT", "IRRELEVANT"] == 0
