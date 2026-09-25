"""Daily reconciliation: the sum of the rows' virtual positions must equal the broker's net positions."""
from __future__ import annotations


def expected_net(books) -> dict[str, float]:
    out: dict[str, float] = {}
    for b in books:
        for s, p in b.positions.items():
            out[s] = out.get(s, 0.0) + p.qty
    return {s: q for s, q in out.items() if abs(q) > 1e-9}


def reconcile(books, broker_positions: dict[str, float], tol: float = 1e-6) -> dict:
    exp = expected_net(books)
    diffs = {}
    for s in sorted(set(exp) | set(broker_positions)):
        e, b = exp.get(s, 0.0), broker_positions.get(s, 0.0)
        if abs(e - b) > tol:
            diffs[s] = {"expected": e, "broker": b, "diff": b - e}
    return {"ok": not diffs, "diffs": diffs, "symbols": len(exp)}
