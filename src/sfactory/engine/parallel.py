"""Parallel trade-cache precompute (ADR-0004): the expensive, embarrassingly parallel part of a run is computing
trades per (symbol, entry, exit, filters). This module enumerates every setting a set of rows can ask for,
computes them per symbol in a process pool, and merges the frames into the parent cache under the exact keys
`TradeCache.trades` would use. The research code then runs unchanged and finds everything cached.

- Standard library only (`concurrent.futures.ProcessPoolExecutor` with the spawn start method on every platform:
  the worker is a module-level function and gets plain arrays, so the same code runs on Windows).
- Deterministic: a worker builds a one-symbol TradeCache with the parent's data version, cost model, regime
  series and index events, and calls the same `trades` code; results do not depend on the worker count
  (tested: 1 worker == N workers == no precompute, frame for frame).
- The Numba kernels use `cache=True`, so workers load compiled code from disk after the first run.
- With a `cache_dir` the parent writes every new frame to Parquet, so later runs start warm on any worker count.
"""
from __future__ import annotations

import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor

from sfactory.engine.cache import TradeCache
from sfactory.progress import progress
from sfactory.signals.methods import EntrySpec


def row_specs(cfg) -> set[tuple]:
    """Every (entry, exit, filters, delay) the in-fold selector of a row may request (superset by rung).
    Ensembles contribute their members' settings."""
    members = cfg.expanded() if hasattr(cfg, "expanded") else [cfg]
    out = set()
    for c in members:
        thresholds = c.grid if c.level >= 1 else (c.fixed,)
        exits = (c.neutral,) + (tuple(e for e in c.exit_lib if e != c.neutral) if c.level >= 3 else ())
        fsets = [()] + ([(f,) for f in c.filters] if c.level >= 4 else [])
        for thr in thresholds:
            for ex in exits:
                for fl in fsets:
                    out.add((EntrySpec(c.method, thr, c.direction), ex, c.structural + fl, c.entry_delay))
    return out


def _worker(payload) -> list:
    sym, arr, dv, notional, cost_model, regime, index_add, events_id, specs = payload
    c = TradeCache({sym: arr}, dv, notional, cost_model=cost_model)
    if regime is not None:
        c.set_market_regime(regime[0], regime[1], regime[2])
    if index_add is not None:
        c._index_add, c._events_id = {sym: index_add}, events_id
    for e, x, fl, d in specs:
        c.trades(sym, e, x, fl, d)
    return list(c._store.items())


def _have(cache: TradeCache, key: tuple) -> bool:
    path = cache._path(key)
    return key in cache._store or (path is not None and path.exists())


def precompute(cache: TradeCache, rows, symbols=None, n_workers: int | None = None, mp_context=None) -> dict:
    """Fill `cache` with all trades `rows` can use. n_workers=None -> os.cpu_count(); 1 -> in-process.
    mp_context defaults to "spawn" on every platform: it is the Windows start method, and fork is unsafe in a
    process that already runs Polars / Numba threads."""
    t0 = time.perf_counter()
    specs = sorted(set().union(*(row_specs(r) for r in rows)), key=repr) if rows else []
    syms = sorted(symbols if symbols is not None else cache.arrays)
    regime = (None if cache._regime_dates is None
              else (cache._regime_dates, cache._regime_flags, cache._regime_id))
    tasks = []
    for s in syms:
        todo = [sp for sp in specs if not _have(cache, cache.key_for(s, *sp))]
        if todo:
            tasks.append((s, cache.arrays[s], cache.data_version, cache.notional, cache.cost_model, regime,
                          cache._index_add.get(s), cache._events_id, todo))
    n_workers = n_workers or os.cpu_count() or 1
    added = 0
    bar = progress().counter("trade cache (symbols)", len(tasks))
    if n_workers <= 1 or len(tasks) <= 1:
        results = map(_worker, tasks)
        for t, items in zip(tasks, results):
            added += cache.merge(items)
            bar.tick(label=t[0])
    else:
        ctx = mp_context or multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=n_workers, mp_context=ctx) as ex:
            for t, items in zip(tasks, ex.map(_worker, tasks, chunksize=max(1, len(tasks) // (4 * n_workers)))):
                added += cache.merge(items)
                bar.tick(label=t[0])
    return {"symbols": len(syms), "settings": len(specs), "frames_added": added, "workers": n_workers,
            "seconds": round(time.perf_counter() - t0, 3)}
