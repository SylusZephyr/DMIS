"""Sourcing intelligence: find, compare and rank suppliers for a product concept across Chinese marketplaces
(1688, Alibaba.com, Taobao, AliExpress, Made-in-China), and reach out with a ready RFQ.

    from dip.sourcing_intel import concept, run_concept
    c = concept.from_scope("denture_base", "segment", "Ccc0226-S5")   # from a board / map scope
    r = run_concept(c)                                                 # offers, suppliers, best pick

platforms.py (APIs and field mapping), concept.py (what we source), score.py (fit, landed cost, margin,
reliability, compliance, MOQ, Pareto), run.py (orchestration, RFQ, reach out). Settings:
config/platform/sourcing_intel.yaml.
"""

from dip.sourcing_intel import concept
from dip.sourcing_intel.platforms import status
from dip.sourcing_intel.run import reach_out, rfq, run_concept, runs

__all__ = ["concept", "reach_out", "rfq", "run_concept", "runs", "status"]
