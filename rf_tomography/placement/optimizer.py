"""Budgeted receiver placement: greedy by marginal gain per cost + swap local search.

Deterministic. Not guaranteed optimal (the objective is not submodular because of
the shared nuisance offsets); swap search plus random-layout baselines keep it honest.
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np

from .objective import PlacementObjective


def greedy(obj: PlacementObjective, budget: float) -> List[int]:
    chosen: List[int] = []
    cur = obj.score(chosen)
    while True:
        spent = obj.cost(chosen)
        best, best_ratio, best_s = None, 0.0, cur
        for i, c in enumerate(obj.cands):
            if i in chosen or spent + c.cost > budget:
                continue
            s = obj.score(chosen + [i])
            ratio = (s - cur) / c.cost
            if ratio > best_ratio + 1e-12:
                best, best_ratio, best_s = i, ratio, s
        if best is None:
            return chosen
        chosen.append(best)
        cur = best_s


def swap_search(obj: PlacementObjective, chosen: Sequence[int], budget: float,
                max_rounds: int = 20, pool: Sequence[int] | None = None) -> List[int]:
    """First-improvement swap/add search. `pool` limits the candidate indices tried
    (useful when each score is expensive); default is every candidate."""
    allowed = set(range(len(obj.cands))) if pool is None else set(pool)
    chosen = list(chosen)
    cur = obj.score(chosen)
    for _ in range(max_rounds):
        improved = False
        spent = obj.cost(chosen)
        for pos in range(len(chosen)):
            rest = chosen[:pos] + chosen[pos + 1:]
            room = budget - obj.cost(rest)
            for i, c in enumerate(obj.cands):
                if i not in allowed or i in rest or i == chosen[pos] or c.cost > room:
                    continue
                s = obj.score(rest + [i])
                if s > cur + 1e-9:
                    chosen, cur, improved = rest + [i], s, True
                    break
            if improved:
                break
        # also try pure additions that now fit (swap may free budget)
        spent = obj.cost(chosen)
        for i, c in enumerate(obj.cands):
            if i in allowed and i not in chosen and spent + c.cost <= budget:
                s = obj.score(chosen + [i])
                if s > cur + 1e-9:
                    chosen, cur, improved = chosen + [i], s, True
                    break
        if not improved:
            break
    return chosen


def optimise(obj: PlacementObjective, budget: float) -> List[int]:
    return swap_search(obj, greedy(obj, budget), budget)


def random_layouts(obj: PlacementObjective, budget: float, n: int, seed: int) -> List[List[int]]:
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        order, chosen, spent = rng.permutation(len(obj.cands)), [], 0.0
        for i in order:
            c = obj.cands[i].cost
            if spent + c <= budget:
                chosen.append(int(i))
                spent += c
        out.append(chosen)
    return out
