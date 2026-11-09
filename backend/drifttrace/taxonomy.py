"""The drift taxonomy (spec section 5) and the fidelity score (spec step 8).

The score is deliberately simple arithmetic so it can be printed in every report.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class Label(StrEnum):
    FAITHFUL = "FAITHFUL"
    SCOPE_GENERALIZATION = "SCOPE_GENERALIZATION"
    CAUSAL_INFLATION = "CAUSAL_INFLATION"
    HEDGE_LOSS = "HEDGE_LOSS"
    CONDITION_DROPPED = "CONDITION_DROPPED"
    MAGNITUDE_DISTORTION = "MAGNITUDE_DISTORTION"
    DIRECTION_CONTRADICTION = "DIRECTION_CONTRADICTION"
    UNSUPPORTED_ATTRIBUTION = "UNSUPPORTED_ATTRIBUTION"
    SECONDHAND_CITATION = "SECONDHAND_CITATION"
    RETRACTED_SOURCE = "RETRACTED_SOURCE"
    UNVERIFIABLE = "UNVERIFIABLE"


SEVERITY: dict[str, int] = {
    Label.FAITHFUL: 0,
    Label.SECONDHAND_CITATION: 0,
    Label.UNVERIFIABLE: 0,
    Label.HEDGE_LOSS: 1,
    Label.CONDITION_DROPPED: 1,
    Label.SCOPE_GENERALIZATION: 2,
    Label.MAGNITUDE_DISTORTION: 2,
    Label.CAUSAL_INFLATION: 2,
    Label.DIRECTION_CONTRADICTION: 3,
    Label.UNSUPPORTED_ATTRIBUTION: 3,
    Label.RETRACTED_SOURCE: 3,
}

SEVERITY_PENALTY = {0: 0.0, 1: 8.0, 2: 18.0, 3: 35.0}
CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.75, "low": 0.5}
SECONDARY_FACTOR = 0.25

SCORE_FORMULA = (
    "Fidelity = 100 − (the largest path penalty, floored at 0). A path runs from the queried claim "
    "to a source. Path penalty = the sum over its hops of [severity penalty × confidence weight], "
    "where severity penalty is 0 for severity 0, 8 for severity 1, 18 for severity 2 and 35 for "
    "severity 3, and confidence weight is 1.0 for high, 0.75 for medium and 0.5 for low. Each "
    "secondary label adds 25% of its own penalty. Unverifiable hops add no penalty, and a trace "
    "where every hop is unverifiable has no score."
)


@dataclass(frozen=True)
class ScoredHop:
    id: object
    from_node: object
    to_node: object
    primary_label: str
    secondary_labels: tuple[str, ...]
    confidence: str


def hop_penalty(hop: ScoredHop) -> float:
    weight = CONFIDENCE_WEIGHT.get(hop.confidence, 0.5)
    penalty = SEVERITY_PENALTY[SEVERITY.get(hop.primary_label, 0)] * weight
    for label in hop.secondary_labels:
        penalty += SEVERITY_PENALTY[SEVERITY.get(label, 0)] * weight * SECONDARY_FACTOR
    return penalty


def _paths(hops: list[ScoredHop], root: object) -> list[list[ScoredHop]]:
    children: dict[object, list[ScoredHop]] = {}
    for hop in hops:
        children.setdefault(hop.from_node, []).append(hop)

    found: list[list[ScoredHop]] = []

    def walk(node: object, path: list[ScoredHop], seen: frozenset) -> None:
        outgoing = [h for h in children.get(node, []) if h.to_node not in seen]
        if not outgoing:
            if path:
                found.append(path)
            return
        for hop in outgoing:
            walk(hop.to_node, [*path, hop], seen | {hop.to_node})

    walk(root, [], frozenset({root}))
    return found


def worst_path(hops: Iterable[ScoredHop], root: object) -> list[ScoredHop]:
    paths = _paths(list(hops), root)
    if not paths:
        return []
    return max(paths, key=lambda p: sum(hop_penalty(h) for h in p))


def fidelity_score(hops: Iterable[ScoredHop], root: object) -> int | None:
    """Return 0-100, or None when no hop could be checked against a source."""
    hops = list(hops)
    if not hops or all(h.primary_label == Label.UNVERIFIABLE for h in hops):
        return None
    penalty = sum(hop_penalty(h) for h in worst_path(hops, root))
    return max(0, round(100 - penalty))
